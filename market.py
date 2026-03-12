"""Market data helpers: fetch CoinGecko data, group it by sector, summarise sentiment."""
import os
import time

import requests

BASE_URL = "https://api.coingecko.com/api/v3"
TIMEOUT = 10
CACHE_TTL = int(os.environ.get("CACHE_TTL", "120"))  # seconds; protects the free-tier rate limit
COINS_PER_SECTOR = 5

# Display name -> CoinGecko category id
SECTORS = {
    "GameFi": "gaming",
    "RWA": "real-world-assets-rwa",
    "Memes": "meme-token",
    "DePIN": "depin",
    "AI": "artificial-intelligence",
    "L1": "layer-1",
}

# Headline assets shown at the top of the page
HEADLINE_IDS = {"Bitcoin": "bitcoin", "Ethereum": "ethereum", "Solana": "solana"}

MOCK_PRICES = {
    "bitcoin": {"usd": 64250.0, "usd_24h_change": 1.8},
    "ethereum": {"usd": 3120.5, "usd_24h_change": -0.6},
    "solana": {"usd": 148.2, "usd_24h_change": 3.4},
}
MOCK_SECTOR_COINS = [
    {"symbol": "sym", "name": "Example coin", "price_change_percentage_24h": 2.0}
]


class UpstreamError(Exception):
    """CoinGecko was unreachable or returned something unusable."""


_cache = {}


def _get(path, params):
    key = (path, tuple(sorted(params.items())))
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]
    try:
        resp = requests.get(f"{BASE_URL}{path}", params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        if hit:  # serve stale data rather than failing (e.g. on HTTP 429)
            return hit[1]
        raise UpstreamError(f"CoinGecko request to {path} failed: {exc}") from exc
    _cache[key] = (time.time(), data)
    return data


def clear_cache():
    _cache.clear()


def fetch_prices():
    """Price and 24h change for the headline assets, keyed by CoinGecko id."""
    if os.environ.get("MOCK_DATA") == "1":
        return MOCK_PRICES
    return _get("/simple/price", {
        "ids": ",".join(HEADLINE_IDS.values()),
        "vs_currencies": "usd",
        "include_24hr_change": "true",
    })


def fetch_sector(category_id):
    """Top coins of one CoinGecko category by market cap."""
    if os.environ.get("MOCK_DATA") == "1":
        return MOCK_SECTOR_COINS * COINS_PER_SECTOR
    return _get("/coins/markets", {
        "vs_currency": "usd",
        "category": category_id,
        "order": "market_cap_desc",
        "per_page": COINS_PER_SECTOR,
        "page": 1,
    })


def sector_symbols(rows):
    """Upper-case ticker symbols from a /coins/markets response."""
    return [r["symbol"].upper() for r in rows if r.get("symbol")]


def build_sectors():
    """Sector name -> list of tickers. A sector that fails stays empty instead of
    being padded with unrelated coins, so the output is never misleading."""
    result, errors = {}, []
    for name, category_id in SECTORS.items():
        try:
            result[name] = sector_symbols(fetch_sector(category_id))
        except UpstreamError as exc:
            result[name] = []
            errors.append(str(exc))
    return result, errors


def sentiment(prices):
    """'bullish' if more than half of the headline assets rose in 24h, else 'bearish'.
    Assets without a 24h change are ignored; None if nothing is usable."""
    changes = [p["usd_24h_change"] for p in prices.values()
               if p.get("usd_24h_change") is not None]
    if not changes:
        return None
    up = sum(1 for c in changes if c > 0)
    return "bullish" if up > len(changes) / 2 else "bearish"


def format_price(price):
    return f"${price:,.2f}"


def format_line(name, price, change):
    arrow = "▲" if (change or 0) >= 0 else "▼"
    pct = "n/a" if change is None else f"{change:+.1f}%"
    return f"{arrow} {name} {format_price(price)} ({pct})"


def build_post(prices, sectors):
    """Plain-text market update, ready to paste."""
    lines = []
    for name, cid in HEADLINE_IDS.items():
        if cid in prices:
            lines.append(format_line(name, prices[cid]["usd"], prices[cid].get("usd_24h_change")))
    mood = sentiment({k: v for k, v in prices.items()})
    if mood:
        lines += ["", f"24h sentiment across BTC/ETH/SOL: {mood}"]
    lines.append("")
    for name, coins in sectors.items():
        if coins:
            lines.append(f"{name}: {', '.join(coins)}")
    return "\n".join(lines)
