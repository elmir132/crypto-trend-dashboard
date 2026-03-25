"""Market data helpers: fetch CoinGecko data, group it by sector, summarise movement."""
import contextvars
import csv
import io
import json
import math
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests

DEFAULT_BASE_URL = "https://api.coingecko.com/api/v3"
TIMEOUT = 10
CACHE_TTL = int(os.environ.get("CACHE_TTL", "120"))  # seconds; protects the free-tier rate limit
MAX_BACKOFF = 60  # longest pause after a rate-limit response, in seconds
USER_AGENT = "crypto-trend-dashboard/2.0"
SPARKLINE_STEP = 2  # CoinGecko sends 168 hourly points for 7 days; keep every second one


def _coins_per_sector():
    try:
        return min(20, max(1, int(os.environ.get("COINS_PER_SECTOR", "5"))))
    except ValueError:
        return 5


COINS_PER_SECTOR = _coins_per_sector()

# Display name -> CoinGecko category id
DEFAULT_SECTORS = {
    "GameFi": "gaming",
    "RWA": "real-world-assets-rwa",
    "Memes": "meme-token",
    "DePIN": "depin",
    "AI": "artificial-intelligence",
    "L1": "layer-1",
}
SECTORS = dict(DEFAULT_SECTORS)

# Headline assets shown at the top of the page: display name -> CoinGecko id
DEFAULT_HEADLINE = {"Bitcoin": "bitcoin", "Ethereum": "ethereum", "Solana": "solana"}
HEADLINE_IDS = dict(DEFAULT_HEADLINE)

_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class ConfigError(ValueError):
    """The COINS or SECTORS_FILE setting is not usable."""


def configure(env=None):
    """Apply COINS and SECTORS_FILE from the environment. Raises ConfigError with a clear
    message instead of starting with a half-understood setting."""
    global HEADLINE_IDS, SECTORS
    env = os.environ if env is None else env

    coins = env.get("COINS", "").strip()
    if coins:
        ids = [c.strip().lower() for c in coins.split(",") if c.strip()]
        if not 1 <= len(ids) <= 10:
            raise ConfigError("COINS must list 1 to 10 CoinGecko ids, separated by commas")
        bad = [c for c in ids if not _ID.match(c)]
        if bad:
            raise ConfigError(f"COINS has an invalid CoinGecko id: {bad[0]!r}")
        known = {v: k for k, v in DEFAULT_HEADLINE.items()}
        HEADLINE_IDS = {known.get(c, c.replace("-", " ").title()): c for c in dict.fromkeys(ids)}
    else:
        HEADLINE_IDS = dict(DEFAULT_HEADLINE)

    path = env.get("SECTORS_FILE", "").strip()
    if path:
        try:
            with open(path, encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, ValueError) as exc:
            raise ConfigError(f"Could not read SECTORS_FILE {path}: {exc}") from exc
        if not isinstance(raw, dict) or not 1 <= len(raw) <= 12:
            raise ConfigError("SECTORS_FILE must be a JSON object with 1 to 12 entries")
        for name, category in raw.items():
            if not isinstance(name, str) or not name.strip() or len(name) > 30:
                raise ConfigError("SECTORS_FILE: each display name must be 1 to 30 characters")
            if not isinstance(category, str) or not _ID.match(category):
                raise ConfigError(f"SECTORS_FILE: {name!r} needs a CoinGecko category id such as 'layer-1'")
        SECTORS = dict(raw)
    else:
        SECTORS = dict(DEFAULT_SECTORS)


def mock_enabled():
    return os.environ.get("MOCK_DATA") == "1"


# ---------------------------------------------------------------------------
# Sample data for the offline demo. Illustrative only: nothing here is a real price.
# ---------------------------------------------------------------------------

def _sample_sparkline(base, drift, wobble, points=84):
    return [round(base * (1 + drift * i / (points - 1) + wobble * math.sin(i / 5.0)), 2) for i in range(points)]


MOCK_HEADLINE_ROWS = [
    {"id": "bitcoin", "symbol": "btc", "name": "Bitcoin", "current_price": 64250.0, "market_cap": 1.26e12,
     "total_volume": 2.4e10, "price_change_percentage_24h": 1.8,
     "price_change_percentage_1h_in_currency": 0.1, "price_change_percentage_7d_in_currency": 4.2,
     "sparkline_in_7d": {"price": _sample_sparkline(61500, 0.045, 0.008, 168 // SPARKLINE_STEP)}},
    {"id": "ethereum", "symbol": "eth", "name": "Ethereum", "current_price": 3120.5, "market_cap": 3.75e11,
     "total_volume": 1.3e10, "price_change_percentage_24h": -0.6,
     "price_change_percentage_1h_in_currency": -0.2, "price_change_percentage_7d_in_currency": -1.5,
     "sparkline_in_7d": {"price": _sample_sparkline(3170, -0.015, 0.012, 168 // SPARKLINE_STEP)}},
    {"id": "solana", "symbol": "sol", "name": "Solana", "current_price": 148.2, "market_cap": 6.9e10,
     "total_volume": 3.1e9, "price_change_percentage_24h": 3.4,
     "price_change_percentage_1h_in_currency": 0.4, "price_change_percentage_7d_in_currency": 8.9,
     "sparkline_in_7d": {"price": _sample_sparkline(136, 0.09, 0.02, 168 // SPARKLINE_STEP)}},
]

_MOCK_SECTOR_CHANGES = {
    "gaming": [2.1, -1.2, 0.8, -3.4, 1.5],
    "real-world-assets-rwa": [0.4, 0.9, -0.2, 1.1, 0.3],
    "meme-token": [6.5, -4.8, 3.2, 9.1, -1.7],
    "depin": [-0.9, 2.2, 1.4, -2.6, 0.5],
    "artificial-intelligence": [3.8, 1.9, -0.7, 4.4, 2.6],
    "layer-1": [1.2, -0.4, 0.6, 2.0, -1.1],
}


def _mock_sector_rows(category_id):
    changes = _MOCK_SECTOR_CHANGES.get(category_id, [1.0, -1.0, 0.5, -0.5, 0.0])
    tag = re.sub(r"[^a-z]", "", category_id)[:3].upper() or "SAM"
    return [{"symbol": f"{tag}{i + 1}", "name": f"Sample coin {tag}{i + 1}", "current_price": round(10 / (i + 1), 4),
             "market_cap": 1e9 / (i + 1), "price_change_percentage_24h": change}
            for i, change in enumerate(changes)]


class UpstreamError(Exception):
    """CoinGecko was unreachable or returned something unusable."""


_cache = {}
_key_locks = {}
_lock = threading.Lock()
_blocked_until = 0.0
_stale_log = contextvars.ContextVar("stale_log", default=None)


def _base_url():
    return os.environ.get("COINGECKO_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def _headers():
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    key = os.environ.get("COINGECKO_API_KEY", "").strip()
    if key:  # a free "demo" key raises the rate limit; it is sent as a header, never in the URL
        headers["x-cg-demo-api-key"] = key
    return headers


def _retry_after(response):
    try:
        return min(MAX_BACKOFF, max(1, int(float(response.headers.get("Retry-After", "30")))))
    except (AttributeError, TypeError, ValueError):
        return 30


def _get(path, params):
    """Cached GET. Requests for the same key are serialised: when several callers ask at the
    same moment, one goes to CoinGecko and the others wait and reuse its answer."""
    global _blocked_until
    key = (path, tuple(sorted(params.items())))
    with _lock:
        hit = _cache.get(key)
        key_lock = _key_locks.setdefault(key, threading.Lock())
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]
    with key_lock:
        with _lock:
            hit = _cache.get(key)
            blocked = _blocked_until - time.time()
        if hit and time.time() - hit[0] < CACHE_TTL:  # someone else just refreshed it
            return hit[1]

        def stale():
            log = _stale_log.get()
            if log is not None:
                log.append(path)
            return hit[1]

        if blocked > 0:  # recently rate limited: do not hammer the API
            if hit:
                return stale()
            raise UpstreamError(f"CoinGecko is rate limiting requests; retry in {int(blocked) + 1}s")
        try:
            resp = requests.get(f"{_base_url()}{path}", params=params, timeout=TIMEOUT, headers=_headers())
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as exc:
            response = getattr(exc, "response", None)
            status = getattr(response, "status_code", None)
            if status == 429:
                with _lock:
                    _blocked_until = time.time() + _retry_after(response)
            if hit:  # serve stale data rather than failing (e.g. on HTTP 429)
                return stale()
            reason = f"HTTP {status}" if status else type(exc).__name__
            raise UpstreamError(f"CoinGecko {path} failed ({reason})") from exc
        with _lock:
            _cache[key] = (time.time(), data)
        return data


def clear_cache():
    global _blocked_until
    with _lock:
        _cache.clear()
        _blocked_until = 0.0


# ---------------------------------------------------------------------------
# Normalising CoinGecko rows
# ---------------------------------------------------------------------------

def _num(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        return None
    return float(value)


def coin_from_row(row):
    """One /coins/markets row as the compact dict the rest of the app uses."""
    change_24h = _num(row.get("price_change_percentage_24h_in_currency"))
    if change_24h is None:
        change_24h = _num(row.get("price_change_percentage_24h"))
    spark = (row.get("sparkline_in_7d") or {}).get("price") or []
    return {
        "id": row.get("id"),
        "symbol": (row.get("symbol") or "").upper(),
        "name": row.get("name"),
        "price": _num(row.get("current_price")),
        "change_1h": _num(row.get("price_change_percentage_1h_in_currency")),
        "change_24h": change_24h,
        "change_7d": _num(row.get("price_change_percentage_7d_in_currency")),
        "market_cap": _num(row.get("market_cap")),
        "volume": _num(row.get("total_volume")),
        "sparkline": [round(v, 6) for v in (_num(p) for p in spark[::SPARKLINE_STEP]) if v is not None],
    }


def fetch_headline():
    """Raw /coins/markets rows for the headline assets, with 7-day sparkline."""
    ids = list(HEADLINE_IDS.values())
    if mock_enabled():
        return [row for row in MOCK_HEADLINE_ROWS if row["id"] in ids]
    return _get("/coins/markets", {
        "vs_currency": "usd",
        "ids": ",".join(ids),
        "order": "market_cap_desc",
        "per_page": len(ids),
        "page": 1,
        "sparkline": "true",
        "price_change_percentage": "1h,24h,7d",
    })


def fetch_prices():
    """Headline assets keyed by CoinGecko id. `usd` and `usd_24h_change` keep the shape
    earlier versions returned; the other keys are extra detail."""
    prices = {}
    for row in fetch_headline():
        coin = coin_from_row(row)
        if coin["id"]:
            prices[coin["id"]] = {
                "usd": coin["price"], "usd_24h_change": coin["change_24h"],
                "usd_1h_change": coin["change_1h"], "usd_7d_change": coin["change_7d"],
                "market_cap": coin["market_cap"], "volume": coin["volume"],
                "name": coin["name"], "symbol": coin["symbol"], "sparkline": coin["sparkline"],
            }
    return prices


def fetch_sector(category_id):
    """Top coins of one CoinGecko category by market cap."""
    if mock_enabled():
        return _mock_sector_rows(category_id)[:_coins_per_sector()]
    return _get("/coins/markets", {
        "vs_currency": "usd",
        "category": category_id,
        "order": "market_cap_desc",
        "per_page": _coins_per_sector(),
        "page": 1,
    })


MOCK_OVERVIEW = {"total_market_cap": 2.55e12, "total_volume": 9.1e10, "market_cap_change_24h": 0.8,
                 "btc_dominance": 52.3, "eth_dominance": 14.8, "active_coins": 14000, "updated_at": None}
MOCK_TRENDING = [
    {"id": "sample-a", "name": "Sample trending A", "symbol": "TRA", "rank": 120, "change_24h": 18.4},
    {"id": "sample-b", "name": "Sample trending B", "symbol": "TRB", "rank": 340, "change_24h": -6.2},
    {"id": "sample-c", "name": "Sample trending C", "symbol": "TRC", "rank": 55, "change_24h": 3.1},
]


def overview_enabled():
    return os.environ.get("OVERVIEW", "1") != "0"


def fetch_overview():
    """Whole-market figures from CoinGecko's /global endpoint, or None when the response has
    no usable total. Values the response lacks are None."""
    if mock_enabled():
        return dict(MOCK_OVERVIEW)
    payload = _get("/global", {})
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return None
    caps, volumes, share = data.get("total_market_cap"), data.get("total_volume"), data.get("market_cap_percentage")
    caps, volumes, share = (x if isinstance(x, dict) else {} for x in (caps, volumes, share))
    overview = {
        "total_market_cap": _num(caps.get("usd")),
        "total_volume": _num(volumes.get("usd")),
        "market_cap_change_24h": _num(data.get("market_cap_change_percentage_24h_usd")),
        "btc_dominance": _num(share.get("btc")),
        "eth_dominance": _num(share.get("eth")),
        "active_coins": data.get("active_cryptocurrencies") if isinstance(data.get("active_cryptocurrencies"), int) else None,
        "updated_at": data.get("updated_at") if isinstance(data.get("updated_at"), int) else None,
    }
    return overview if overview["total_market_cap"] is not None else None


def fetch_trending(count=7):
    """Coins CoinGecko lists as trending right now: id, name, ticker, market cap rank, 24h change."""
    if mock_enabled():
        return [dict(c) for c in MOCK_TRENDING][:count]
    payload = _get("/search/trending", {})
    entries = payload.get("coins") if isinstance(payload, dict) else None
    out = []
    for entry in (entries or [])[:count]:
        item = entry.get("item") if isinstance(entry, dict) else None
        if not isinstance(item, dict) or not item.get("symbol"):
            continue
        change = ((item.get("data") or {}).get("price_change_percentage_24h") or {})
        out.append({"id": item.get("id"), "name": item.get("name"), "symbol": str(item["symbol"]).upper(),
                    "rank": item.get("market_cap_rank") if isinstance(item.get("market_cap_rank"), int) else None,
                    "change_24h": _num(change.get("usd")) if isinstance(change, dict) else None})
    return out


def alert_threshold():
    """Percent move that counts as a big move (ALERT_THRESHOLD, default 5, allowed 0.5 to 100)."""
    try:
        return min(100.0, max(0.5, float(os.environ.get("ALERT_THRESHOLD", "5"))))
    except ValueError:
        return 5.0


def alerts(headline, details, threshold=None, limit=10):
    """Coins whose 24h move is at least `threshold` percent in either direction, biggest first."""
    threshold = alert_threshold() if threshold is None else threshold
    big = [c for c in _unique_coins(headline, details) if c["change_24h"] is not None and abs(c["change_24h"]) >= threshold]
    big.sort(key=lambda c: abs(c["change_24h"]), reverse=True)
    return [{"symbol": c["symbol"], "name": c["name"], "change_24h": c["change_24h"], "sector": c["sector"],
             "direction": "up" if c["change_24h"] > 0 else "down"} for c in big[:limit]]


def sector_symbols(rows):
    """Upper-case ticker symbols from a /coins/markets response."""
    return [r["symbol"].upper() for r in rows if r.get("symbol")]


def _fetch_workers():
    try:
        return min(6, max(1, int(os.environ.get("FETCH_WORKERS", "2"))))
    except ValueError:
        return 2


def build_sector_details(workers=None):
    """Sector name -> list of coin dicts. A sector that fails stays empty instead of being
    padded with unrelated coins, so the output is never misleading. Sectors are fetched a
    few at a time, which keeps a cold refresh quick without hammering the API."""
    items = list(SECTORS.items())
    errors = []

    def one(item):
        name, category_id = item
        try:
            rows = fetch_sector(category_id)
            return name, [coin_from_row(r) for r in rows if r.get("symbol")], None
        except UpstreamError as exc:
            return name, [], str(exc)

    workers = _fetch_workers() if workers is None else workers
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(items) or 1))) as pool:
        futures = [pool.submit(contextvars.copy_context().run, one, item) for item in items]
        results = [f.result() for f in futures]
    details = {}
    for name, coins, error in results:  # keeps the configured order
        details[name] = coins
        if error:
            errors.append(error)
    return details, errors


def build_sectors():
    """Sector name -> list of tickers."""
    details, errors = build_sector_details()
    return {name: [c["symbol"] for c in coins] for name, coins in details.items()}, errors


# ---------------------------------------------------------------------------
# Summaries
# ---------------------------------------------------------------------------

def sentiment(prices):
    """'bullish' if more than half of the headline assets rose in 24h, else 'bearish'.
    Assets without a 24h change are ignored; None if nothing is usable."""
    changes = [p["usd_24h_change"] for p in prices.values()
               if p.get("usd_24h_change") is not None]
    if not changes:
        return None
    up = sum(1 for c in changes if c > 0)
    return "bullish" if up > len(changes) / 2 else "bearish"


def sector_stats(details):
    """Per sector: mean 24h change of its listed coins, how many rose, best and worst."""
    stats = {}
    for name, coins in details.items():
        known = [c for c in coins if c["change_24h"] is not None]
        if not known:
            stats[name] = {"avg_change_24h": None, "up": 0, "down": 0, "total": 0, "best": None, "worst": None}
            continue
        best = max(known, key=lambda c: c["change_24h"])
        worst = min(known, key=lambda c: c["change_24h"])
        stats[name] = {
            "avg_change_24h": round(sum(c["change_24h"] for c in known) / len(known), 2),
            "up": sum(1 for c in known if c["change_24h"] > 0),
            "down": sum(1 for c in known if c["change_24h"] < 0),
            "total": len(known),
            "best": {"symbol": best["symbol"], "change_24h": best["change_24h"]},
            "worst": {"symbol": worst["symbol"], "change_24h": worst["change_24h"]},
        }
    return stats


def _unique_coins(headline, details):
    seen, coins = set(), []
    for coin in headline:
        coins.append({**coin, "sector": None})
        seen.add(coin["symbol"])
    for sector, members in details.items():
        for coin in members:
            if coin["symbol"] not in seen:
                seen.add(coin["symbol"])
                coins.append({**coin, "sector": sector})
    return coins


def market_breadth(headline, details):
    """Share of the tracked coins (headline plus sector lists, each counted once) that rose
    in 24h. A description of the coins shown here, not of the whole market."""
    known = [c for c in _unique_coins(headline, details) if c["change_24h"] is not None]
    up = sum(1 for c in known if c["change_24h"] > 0)
    down = sum(1 for c in known if c["change_24h"] < 0)
    return {"up": up, "down": down, "flat": len(known) - up - down, "total": len(known),
            "share_up": round(up / len(known), 3) if known else None}


def movers(headline, details, count=3):
    known = [c for c in _unique_coins(headline, details) if c["change_24h"] is not None]

    def slim(coin):
        return {"symbol": coin["symbol"], "name": coin["name"], "change_24h": coin["change_24h"], "sector": coin["sector"]}

    ranked = sorted(known, key=lambda c: c["change_24h"], reverse=True)
    return {"gainers": [slim(c) for c in ranked[:count] if c["change_24h"] > 0],
            "losers": [slim(c) for c in reversed(ranked[-count:]) if c["change_24h"] < 0]}


def headline_list(prices):
    """Headline assets in configured order, as the compact dicts the UI draws."""
    out = []
    for display, cid in HEADLINE_IDS.items():
        p = prices.get(cid)
        if not p:
            continue
        out.append({"id": cid, "name": p.get("name") or display, "symbol": p.get("symbol") or "",
                    "price": p.get("usd"), "change_1h": p.get("usd_1h_change"),
                    "change_24h": p.get("usd_24h_change"), "change_7d": p.get("usd_7d_change"),
                    "market_cap": p.get("market_cap"), "volume": p.get("volume"),
                    "sparkline": p.get("sparkline") or []})
    return out


def format_price(price):
    if price is None:
        return "n/a"
    if price >= 1:
        return f"${price:,.2f}"
    return f"${price:,.4f}" if price >= 0.01 else f"${price:,.6f}"


def format_line(name, price, change):
    arrow = "▲" if (change or 0) >= 0 else "▼"
    pct = "n/a" if change is None else f"{change:+.1f}%"
    return f"{arrow} {name} {format_price(price)} ({pct})"


def format_big(number):
    """$2.55T, $91.0B, $4.2M"""
    if number is None:
        return "n/a"
    for limit, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if abs(number) >= limit:
            return f"${number / limit:,.2f}{suffix}" if suffix == "T" else f"${number / limit:,.1f}{suffix}"
    return f"${number:,.0f}"


def _overview_line(overview):
    if not overview or overview.get("total_market_cap") is None:
        return None
    line = f"Total market cap {format_big(overview['total_market_cap'])}"
    if overview.get("market_cap_change_24h") is not None:
        line += f" ({overview['market_cap_change_24h']:+.1f}% 24h)"
    if overview.get("btc_dominance") is not None:
        line += f", BTC dominance {overview['btc_dominance']:.1f}%"
    return line


def _alerts_line(alert_list, threshold):
    if not alert_list:
        return None
    moves = ", ".join(f"{a['symbol']} {a['change_24h']:+.1f}%" for a in alert_list[:6])
    return f"Big moves ({threshold:g}%+ in 24h): {moves}"


def _headline_label():
    return "BTC/ETH/SOL" if HEADLINE_IDS == DEFAULT_HEADLINE else "headline assets"


def _hottest(stats):
    ranked = [(name, s["avg_change_24h"]) for name, s in (stats or {}).items() if s["avg_change_24h"] is not None]
    return max(ranked, key=lambda item: item[1]) if ranked else None


def build_post(prices, sectors, stats=None, overview=None, alert_list=None, threshold=None):
    """Plain-text market update, ready to paste."""
    lines = []
    for name, cid in HEADLINE_IDS.items():
        if cid in prices and prices[cid].get("usd") is not None:
            lines.append(format_line(prices[cid].get("name") or name, prices[cid]["usd"], prices[cid].get("usd_24h_change")))
    market_line = _overview_line(overview)
    if market_line:
        lines += ["", market_line]
    mood = sentiment(prices)
    if mood:
        lines += ["", f"24h sentiment across {_headline_label()}: {mood}"]
    hottest = _hottest(stats)
    if hottest:
        lines.append(f"Strongest sector (average 24h change): {hottest[0]} {hottest[1]:+.1f}%")
    big = _alerts_line(alert_list, alert_threshold() if threshold is None else threshold)
    if big:
        lines.append(big)
    lines.append("")
    for name, coins in sectors.items():
        if coins:
            lines.append(f"{name}: {', '.join(coins)}")
    return "\n".join(lines)


def build_markdown(prices, sectors, stats=None, overview=None, alert_list=None, threshold=None):
    """The same update as Markdown, for chats and docs that render it."""
    lines = ["**Crypto market update**", ""]
    for name, cid in HEADLINE_IDS.items():
        if cid in prices and prices[cid].get("usd") is not None:
            label = "**" + (prices[cid].get("name") or name) + "**"
            lines.append("- " + format_line(label, prices[cid]["usd"], prices[cid].get("usd_24h_change")))
    market_line = _overview_line(overview)
    if market_line:
        lines += ["", market_line]
    mood = sentiment(prices)
    if mood:
        lines += ["", f"24h sentiment across {_headline_label()}: **{mood}**"]
    hottest = _hottest(stats)
    if hottest:
        lines.append(f"Strongest sector (average 24h change): **{hottest[0]}** {hottest[1]:+.1f}%")
    big = _alerts_line(alert_list, alert_threshold() if threshold is None else threshold)
    if big:
        lines.append(big)
    rows = [f"- **{name}:** {', '.join(coins)}" for name, coins in sectors.items() if coins]
    if rows:
        lines += ["", *rows]
    return "\n".join(lines)


def _csv_safe(value):
    """Spreadsheets run text that starts with = + - @ as a formula. Names come from an
    outside source, so neutralise those."""
    if value is None:
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value  # real numbers (including negatives) are not formulas
    text = str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def build_csv(headline, details):
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["group", "symbol", "name", "price_usd", "change_24h_pct", "change_7d_pct", "market_cap_usd"])
    for coin in headline:
        writer.writerow([_csv_safe(v) for v in ("headline", coin["symbol"], coin["name"], coin["price"], coin["change_24h"], coin["change_7d"], coin["market_cap"])])
    for sector, coins in details.items():
        for coin in coins:
            writer.writerow([_csv_safe(v) for v in (sector, coin["symbol"], coin["name"], coin["price"], coin["change_24h"], None, coin["market_cap"])])
    return out.getvalue()


def build_summary():
    """Everything the page, the API and the command line need, from one set of fetches."""
    stale = []
    token = _stale_log.set(stale)
    try:
        errors = []
        try:
            prices = fetch_prices()
        except UpstreamError as exc:
            prices, errors = {}, [str(exc)]
        details, sector_errors = build_sector_details()
        errors += sector_errors
        overview, trending = None, []
        if overview_enabled():
            for label, fetch in (("overview", fetch_overview), ("trending", fetch_trending)):
                try:
                    value = fetch()
                except UpstreamError as exc:
                    errors.append(str(exc))
                    continue
                if label == "overview":
                    overview = value
                else:
                    trending = value
    finally:
        _stale_log.reset(token)
    errors = list(dict.fromkeys(errors))   # one message per problem, however many requests hit it
    headline = headline_list(prices)
    stats = sector_stats(details)
    sectors = {name: [c["symbol"] for c in coins] for name, coins in details.items()}
    threshold = alert_threshold()
    big_moves = alerts(headline, details, threshold)
    return {
        "ok": bool(prices or any(details.values())),
        "mock": mock_enabled(),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "stale": bool(stale),
        "cache_ttl": CACHE_TTL,
        "prices": prices,
        "headline": headline,
        "sentiment": sentiment(prices),
        "breadth": market_breadth(headline, details),
        "sectors": sectors,
        "sector_details": details,
        "sector_stats": stats,
        "movers": movers(headline, details),
        "overview": overview,
        "trending": trending,
        "alerts": {"threshold": threshold, "coins": big_moves},
        "post": build_post(prices, sectors, stats, overview, big_moves, threshold),
        "post_markdown": build_markdown(prices, sectors, stats, overview, big_moves, threshold),
        "errors": errors,
    }
