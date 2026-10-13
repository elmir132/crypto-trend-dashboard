import json
import shutil
import subprocess
import time

import pytest
import requests

import cli
import history as history_module
import market
from app import create_app


class Resp:
    def __init__(self, data=None, status=200, headers=None):
        self.data, self.status_code, self.headers = data if data is not None else {}, status, headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            error = requests.HTTPError(f"HTTP {self.status_code}")
            error.response = self
            raise error

    def json(self):
        return self.data


def row(symbol, change, **extra):
    return {"id": symbol.lower(), "symbol": symbol.lower(), "name": symbol.upper() + " coin", "current_price": 10.0,
            "market_cap": 1e9, "price_change_percentage_24h": change, **extra}


def coin(symbol, change, name=None):
    return market.coin_from_row(row(symbol, change, name=name or symbol))


# ---------- normalising rows ----------

def test_coin_from_row_prefers_the_currency_specific_change_and_cleans_values():
    c = market.coin_from_row({"id": "x", "symbol": "xyz", "name": "X", "current_price": 5,
                              "price_change_percentage_24h": 1.0, "price_change_percentage_24h_in_currency": 2.5,
                              "price_change_percentage_7d_in_currency": float("nan"), "market_cap": True,
                              "sparkline_in_7d": {"price": [1, 2, None, 3, 4, "x", 5, 6]}})
    assert c["symbol"] == "XYZ"
    assert c["change_24h"] == 2.5
    assert c["change_7d"] is None            # NaN is not a number we can show
    assert c["market_cap"] is None           # a bool is not a market cap
    assert c["sparkline"] == [1.0, 4.0, 5.0]  # every second point (1, None, 4, 5), the bad one dropped


def test_coin_from_row_survives_an_almost_empty_row():
    c = market.coin_from_row({"symbol": "x"})
    assert c["price"] is None and c["change_24h"] is None and c["sparkline"] == []


def test_format_price_keeps_precision_for_small_prices():
    assert market.format_price(64250) == "$64,250.00"
    assert market.format_price(0.5) == "$0.5000"
    assert market.format_price(0.00001234) == "$0.000012"
    assert market.format_price(None) == "n/a"


# ---------- requests, key, backoff ----------

def test_headline_request_asks_for_sparkline_and_multi_period_change(monkeypatch):
    seen = {}

    def fake_get(url, params=None, timeout=None, headers=None):
        seen.update(url=url, params=params, headers=headers)
        return Resp([])
    monkeypatch.setattr(market.requests, "get", fake_get)
    market.fetch_headline()
    assert seen["url"].endswith("/coins/markets")
    assert seen["params"]["ids"] == "bitcoin,ethereum,solana"
    assert seen["params"]["sparkline"] == "true"
    assert seen["params"]["price_change_percentage"] == "1h,24h,7d"


def test_api_key_goes_in_a_header_never_in_the_url(monkeypatch):
    seen = []
    monkeypatch.setattr(market.requests, "get", lambda url, params=None, timeout=None, headers=None: seen.append((url, params, headers)) or Resp([]))
    market._get("/a", {})
    assert "x-cg-demo-api-key" not in seen[0][2]
    market.clear_cache()
    monkeypatch.setenv("COINGECKO_API_KEY", "SECRET-VALUE")
    monkeypatch.setenv("COINGECKO_BASE_URL", "https://example.test/api/")
    market._get("/b", {})
    url, params, headers = seen[1]
    assert url == "https://example.test/api/b"
    assert headers["x-cg-demo-api-key"] == "SECRET-VALUE"
    assert "SECRET-VALUE" not in url and "SECRET-VALUE" not in json.dumps(params)


def test_rate_limit_pauses_further_requests_and_clear_cache_resets(monkeypatch):
    calls = []
    monkeypatch.setattr(market.requests, "get", lambda *a, **k: calls.append(1) or Resp(status=429, headers={"Retry-After": "7"}))
    with pytest.raises(market.UpstreamError, match="HTTP 429"):
        market._get("/a", {})
    with pytest.raises(market.UpstreamError, match="rate limiting"):
        market._get("/b", {})
    assert len(calls) == 1                    # the second call never reached the network
    market.clear_cache()
    with pytest.raises(market.UpstreamError, match="HTTP 429"):
        market._get("/c", {})
    assert len(calls) == 2


def test_retry_after_is_capped_and_bad_values_fall_back():
    assert market._retry_after(Resp(headers={"Retry-After": "9999"})) == market.MAX_BACKOFF
    assert market._retry_after(Resp(headers={"Retry-After": "soon"})) == 30
    assert market._retry_after(Resp(headers={})) == 30


def test_rate_limited_request_serves_stale_data_and_marks_the_summary_stale(monkeypatch):
    monkeypatch.setenv("COINS", "bitcoin")
    market.configure()
    good = [row("btc", 1.0, id="bitcoin", name="Bitcoin")]
    monkeypatch.setattr(market.requests, "get", lambda *a, **k: Resp(good))
    first = market.build_summary()
    assert first["stale"] is False and first["ok"]
    monkeypatch.setattr(market, "CACHE_TTL", 0)
    monkeypatch.setattr(market.requests, "get", lambda *a, **k: Resp(status=429, headers={"Retry-After": "5"}))
    second = market.build_summary()
    assert second["stale"] is True and second["ok"]
    assert second["headline"][0]["symbol"] == "BTC"


# ---------- configuration ----------

def test_coins_setting_replaces_the_headline_assets():
    market.configure({"COINS": "bitcoin, dogecoin,bitcoin,wrapped-steth"})
    assert market.HEADLINE_IDS == {"Bitcoin": "bitcoin", "Dogecoin": "dogecoin", "Wrapped Steth": "wrapped-steth"}


@pytest.mark.parametrize("value", ["", ",,,", "Bit Coin", "bitcoin;ethereum", ",".join(f"coin{i}" for i in range(11))])
def test_bad_coins_setting_is_rejected_or_ignored(value):
    if value in ("", ",,,"):
        if value == ",,,":
            with pytest.raises(market.ConfigError):
                market.configure({"COINS": value})
        else:
            market.configure({"COINS": value})
            assert market.HEADLINE_IDS == market.DEFAULT_HEADLINE
    else:
        with pytest.raises(market.ConfigError):
            market.configure({"COINS": value})


def test_sectors_file_is_validated(tmp_path):
    good = tmp_path / "s.json"
    good.write_text(json.dumps({"Stable": "stablecoins", "Exchange": "exchange-based-tokens"}))
    market.configure({"SECTORS_FILE": str(good)})
    assert market.SECTORS == {"Stable": "stablecoins", "Exchange": "exchange-based-tokens"}
    for payload, message in [("[]", "JSON object"), ("{}", "JSON object"), (json.dumps({"A": "Bad Id"}), "category id"),
                             (json.dumps({"": "x"}), "display name"), (json.dumps({f"s{i}": "x" for i in range(13)}), "1 to 12"), ("{not json", "Could not read")]:
        bad = tmp_path / "bad.json"
        bad.write_text(payload)
        with pytest.raises(market.ConfigError, match=message):
            market.configure({"SECTORS_FILE": str(bad)})
    with pytest.raises(market.ConfigError, match="Could not read"):
        market.configure({"SECTORS_FILE": str(tmp_path / "missing.json")})


def test_app_refuses_to_start_with_a_broken_setting(monkeypatch):
    monkeypatch.setenv("COINS", "not valid")
    with pytest.raises(market.ConfigError):
        create_app()


# ---------- sectors, stats, breadth, movers ----------

def test_sector_details_keep_configured_order_even_when_fetches_finish_out_of_order(monkeypatch):
    def slow_first(category_id):
        if category_id == "gaming":
            time.sleep(0.05)
        if category_id == "depin":
            raise market.UpstreamError("boom")
        return [row(category_id[:3], 1.0)]
    monkeypatch.setattr(market, "fetch_sector", slow_first)
    details, errors = market.build_sector_details()
    assert list(details) == list(market.SECTORS)
    assert details["DePIN"] == [] and len(errors) == 1
    assert details["GameFi"][0]["symbol"] == "GAM"


def test_fetch_workers_setting_is_clamped(monkeypatch):
    assert market._fetch_workers() == 2
    monkeypatch.setenv("FETCH_WORKERS", "99")
    assert market._fetch_workers() == 6
    monkeypatch.setenv("FETCH_WORKERS", "0")
    assert market._fetch_workers() == 1
    monkeypatch.setenv("FETCH_WORKERS", "many")
    assert market._fetch_workers() == 2


def test_sector_stats_average_best_and_worst():
    stats = market.sector_stats({"A": [coin("a", 4), coin("b", -2), coin("c", None)], "B": []})
    assert stats["A"]["avg_change_24h"] == 1.0
    assert (stats["A"]["up"], stats["A"]["down"], stats["A"]["total"]) == (1, 1, 2)
    assert stats["A"]["best"]["symbol"] == "A" and stats["A"]["worst"]["symbol"] == "B"
    assert stats["B"]["avg_change_24h"] is None and stats["B"]["best"] is None


def test_breadth_counts_each_symbol_once_and_ignores_unknown_changes():
    headline = [coin("btc", 2), coin("eth", -1)]
    details = {"X": [coin("btc", 2), coin("doge", 5), coin("nan", None)], "Y": [coin("doge", 5), coin("zzz", 0)]}
    b = market.market_breadth(headline, details)
    assert (b["up"], b["down"], b["flat"], b["total"]) == (2, 1, 1, 4)
    assert b["share_up"] == 0.5
    assert market.market_breadth([], {})["share_up"] is None


def test_movers_only_list_real_gainers_and_losers():
    m = market.movers([coin("btc", 2)], {"X": [coin("a", 9), coin("b", -7), coin("c", 0.5), coin("d", -0.1), coin("e", 0)]})
    assert [g["symbol"] for g in m["gainers"]] == ["A", "BTC", "C"]
    assert [x["symbol"] for x in m["losers"]] == ["B", "D"]
    assert m["gainers"][0]["sector"] == "X" and m["gainers"][1]["sector"] is None
    assert market.movers([coin("a", 1)], {})["losers"] == []


# ---------- text, Markdown, CSV ----------

PRICES = {"bitcoin": {"usd": 100.0, "usd_24h_change": 2.0, "name": "Bitcoin"},
          "ethereum": {"usd": 50.0, "usd_24h_change": -1.0, "name": "Ethereum"}}


def test_post_mentions_the_strongest_sector_only_when_there_is_one():
    stats = {"Memes": {"avg_change_24h": 3.14}, "AI": {"avg_change_24h": -1.0}, "Empty": {"avg_change_24h": None}}
    text = market.build_post(PRICES, {"Memes": ["A", "B"], "Empty": []}, stats)
    assert "Strongest sector (average 24h change): Memes +3.1%" in text
    assert "Memes: A, B" in text and "Empty:" not in text
    assert "Strongest sector" not in market.build_post(PRICES, {}, None)


def test_post_label_changes_when_the_headline_assets_are_customised():
    assert "across BTC/ETH/SOL" in market.build_post({"bitcoin": {"usd": 1.0, "usd_24h_change": 1}}, {})
    market.configure({"COINS": "bitcoin,dogecoin"})
    assert "across headline assets" in market.build_post({"bitcoin": {"usd": 1.0, "usd_24h_change": 1}}, {})


def test_markdown_post_bolds_names_and_lists_sectors():
    text = market.build_markdown(PRICES, {"Memes": ["A", "B"]}, {"Memes": {"avg_change_24h": 1.0}})
    assert text.startswith("**Crypto market update**")
    assert "- ▲ **Bitcoin** $100.00 (+2.0%)" in text
    assert "- **Memes:** A, B" in text


def test_csv_neutralises_spreadsheet_formulas_and_lists_every_coin():
    headline = [{"symbol": "BTC", "name": "=HYPERLINK(\"http://x\")", "price": 1.0, "change_24h": 1.0, "change_7d": 2.0, "market_cap": 3.0}]
    details = {"S": [{"symbol": "A", "name": "+cmd", "price": 2.0, "change_24h": -1.0, "market_cap": 4.0},
                     {"symbol": "B", "name": "ok, with comma", "price": None, "change_24h": None, "market_cap": None}]}
    lines = market.build_csv(headline, details).splitlines()
    assert lines[0].startswith("group,symbol,name")
    assert lines[1].startswith("headline,BTC,\"'=HYPERLINK")
    assert "S,A,'+cmd,2.0,-1.0,,4.0" in lines   # names are guarded, a negative number is left alone
    assert 'S,B,"ok, with comma"' in lines[3]
    assert len(lines) == 4


# ---------- history ----------

def summary_for(history_price=100.0, **over):
    s = {"mock": False, "stale": False, "headline": [{"id": "bitcoin", "price": history_price}],
         "sector_stats": {"Memes": {"avg_change_24h": 1.5}, "Empty": {"avg_change_24h": None}}, "breadth": {"share_up": 0.6}}
    s.update(over)
    return s


def test_history_throttles_prunes_and_filters_by_window(tmp_path):
    h = history_module.History(str(tmp_path / "h.sqlite"), min_interval=300, retention_days=1)
    t = 1_000_000
    assert h.record(summary_for(100), now=t) is True
    assert h.record(summary_for(101), now=t + 100) is False       # too soon
    assert h.record(summary_for(102), now=t + 400) is True
    assert h.record(summary_for(103), now=t + 400 + 2 * 86400) is True   # old rows are pruned
    points = h.series(hours=72, now=t + 400 + 2 * 86400)
    assert [p["prices"]["bitcoin"] for p in points] == [103]
    assert points[0]["sector_avg"] == {"Memes": 1.5}
    assert points[0]["share_up"] == 0.6


def test_history_ignores_sample_stale_and_empty_data(tmp_path):
    h = history_module.History(str(tmp_path / "h.sqlite"))
    assert h.record(summary_for(mock=True), now=10) is False
    assert h.record(summary_for(stale=True), now=10) is False
    assert h.record(summary_for(headline=[]), now=10) is False
    assert h.record(summary_for(headline=[{"id": "bitcoin", "price": None}]), now=10) is False
    assert h.series(hours=1, now=11) == []


def test_history_skips_a_damaged_row(tmp_path):
    h = history_module.History(str(tmp_path / "h.sqlite"))
    h.record(summary_for(1), now=1000)
    with h._connect() as db:
        db.execute("INSERT INTO snapshots (ts, data) VALUES (2000, 'not json')")
    assert [p["ts"] for p in h.series(hours=1, now=2100)] == [1000]


def test_history_from_env_switches():
    assert history_module.from_env({"HISTORY": "0"}) is None
    assert history_module.from_env({"MOCK_DATA": "1"}) is None


def test_history_from_env_uses_given_path_and_clamps_interval(tmp_path):
    h = history_module.from_env({"HISTORY_DB": str(tmp_path / "x.sqlite"), "HISTORY_MIN_INTERVAL": "1"})
    assert h.min_interval == 30
    assert history_module.from_env({"HISTORY_DB": str(tmp_path / "y.sqlite"), "HISTORY_MIN_INTERVAL": "abc"}).min_interval == 300


# ---------- the web app ----------

@pytest.fixture
def mock_client(monkeypatch):
    monkeypatch.setenv("MOCK_DATA", "1")
    return create_app().test_client()


def test_summary_has_the_new_fields_in_mock_mode(mock_client):
    body = mock_client.get("/api/summary").get_json()
    assert "ok" not in body
    assert body["mock"] is True and body["stale"] is False
    assert [c["symbol"] for c in body["headline"]] == ["BTC", "ETH", "SOL"]
    assert len(body["headline"][0]["sparkline"]) > 10
    assert body["breadth"]["total"] == 33
    assert body["sector_stats"]["Memes"]["avg_change_24h"] == 2.46
    assert "Strongest sector" in body["post"]
    assert body["prices"]["bitcoin"]["usd"] == 64250.0 and body["prices"]["bitcoin"]["usd_24h_change"] == 1.8


def test_post_endpoint_formats(mock_client):
    text = mock_client.get("/api/post")
    assert text.status_code == 200 and text.mimetype == "text/plain"
    assert text.get_data(as_text=True).startswith("▲ Bitcoin")
    md = mock_client.get("/api/post?format=markdown")
    assert md.mimetype == "text/markdown" and md.get_data(as_text=True).startswith("**Crypto market update**")
    assert mock_client.get("/api/post?format=html").status_code == 400


def test_csv_endpoint_downloads_a_file(mock_client):
    res = mock_client.get("/api/summary.csv")
    assert res.status_code == 200 and res.mimetype == "text/csv"
    assert 'attachment; filename="crypto-trend-summary.csv"' in res.headers["Content-Disposition"]
    assert res.get_data(as_text=True).splitlines()[0].startswith("group,symbol,name")


def test_endpoints_return_502_when_nothing_could_be_fetched(monkeypatch):
    def boom(*a, **k):
        raise market.UpstreamError("down")
    monkeypatch.setattr(market, "fetch_prices", boom)
    monkeypatch.setattr(market, "fetch_sector", boom)
    client = create_app().test_client()
    assert client.get("/api/summary").status_code == 502
    assert client.get("/api/post").status_code == 502
    assert client.get("/api/summary.csv").status_code == 502


def test_security_headers_and_no_store_on_api(mock_client):
    api = mock_client.get("/api/summary")
    assert api.headers["Cache-Control"] == "no-store"
    for res in (api, mock_client.get("/")):
        assert res.headers["X-Content-Type-Options"] == "nosniff"
        assert res.headers["Referrer-Policy"] == "no-referrer"
        csp = res.headers["Content-Security-Policy"]
        assert "default-src 'none'" in csp and "script-src 'self'" in csp and "unsafe-inline" not in csp


def test_page_has_no_inline_script_style_or_event_handlers(mock_client):
    html = mock_client.get("/").get_data(as_text=True)
    assert "/static/app.js" in html and "/static/style.css" in html
    assert "<style" not in html and ' style="' not in html
    assert " onclick=" not in html and "javascript:" not in html
    assert all("src=" in tag for tag in html.split("<script")[1:] for tag in [tag.split(">")[0]])
    assert mock_client.get("/static/app.js").status_code == 200
    assert mock_client.get("/static/style.css").status_code == 200


def test_history_endpoint(monkeypatch, tmp_path):
    assert create_app().test_client().get("/api/history").get_json() == {"enabled": False, "points": []}
    store = history_module.History(str(tmp_path / "h.sqlite"), min_interval=30)
    store.record(summary_for(100), now=time.time() - 60)
    client = create_app(history_store=store).test_client()
    body = client.get("/api/history?hours=2").get_json()
    assert body["enabled"] and body["hours"] == 2 and len(body["points"]) == 1
    assert client.get("/api/history?hours=99999").get_json()["hours"] == 720
    assert client.get("/api/history?hours=abc").status_code == 400


def test_live_summary_is_recorded_once_per_interval_and_never_for_sample_data(monkeypatch, tmp_path):
    store = history_module.History(str(tmp_path / "h.sqlite"), min_interval=300)
    monkeypatch.setenv("MOCK_DATA", "1")
    create_app(history_store=store).test_client().get("/api/summary")
    assert store.series(hours=1) == []                         # sample data is never saved
    monkeypatch.delenv("MOCK_DATA")
    rows = [row("btc", 1.0, id="bitcoin", name="Bitcoin")]
    monkeypatch.setattr(market.requests, "get", lambda *a, **k: Resp(rows))
    client = create_app(history_store=store).test_client()
    client.get("/api/summary")
    client.get("/api/summary")
    assert len(store.series(hours=1)) == 1


def test_a_history_failure_does_not_break_the_page(monkeypatch):
    monkeypatch.setenv("MOCK_DATA", "1")

    class Broken:
        def record(self, summary):
            raise OSError("disk full")
    body = create_app(history_store=Broken()).test_client().get("/api/summary").get_json()
    assert any("History could not be saved" in e for e in body["errors"])
    assert body["headline"]


# ---------- command line ----------

def test_cli_formats(capsys):
    assert cli.main(["--mock"]) == 0
    assert capsys.readouterr().out.startswith("▲ Bitcoin")
    assert cli.main(["--mock", "--format", "markdown"]) == 0
    assert capsys.readouterr().out.startswith("**Crypto market update**")
    assert cli.main(["--mock", "--format", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["breadth"]["total"] == 33 and "ok" not in data
    assert cli.main(["--mock", "--format", "csv"]) == 0
    assert capsys.readouterr().out.startswith("group,symbol,name")


def test_cli_reports_failure_and_bad_configuration(monkeypatch, capsys):
    def boom(*a, **k):
        raise market.UpstreamError("down")
    monkeypatch.setattr(market, "fetch_prices", boom)
    monkeypatch.setattr(market, "fetch_sector", boom)
    assert cli.main([]) == 1
    assert "down" in capsys.readouterr().err
    monkeypatch.setenv("COINS", "not valid")
    assert cli.main(["--mock"]) == 2
    assert "invalid CoinGecko id" in capsys.readouterr().err


# ---------- front end ----------

@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_front_end_script_has_valid_syntax():
    result = subprocess.run(["node", "--check", "static/app.js"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_front_end_never_writes_server_data_with_innerhtml():
    source = open("static/app.js", encoding="utf-8").read()
    assert "innerHTML" not in source and "insertAdjacentHTML" not in source and "document.write" not in source
