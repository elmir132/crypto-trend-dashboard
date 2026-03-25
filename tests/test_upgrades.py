import threading
import time

import pytest
import requests

import market
from app import create_app


class Resp:
    def __init__(self, data=None, status=200):
        self.data, self.status_code, self.headers = data if data is not None else {}, status, {}

    def raise_for_status(self):
        if self.status_code >= 400:
            error = requests.HTTPError(f"HTTP {self.status_code}")
            error.response = self
            raise error

    def json(self):
        return self.data


GLOBAL = {"data": {"active_cryptocurrencies": 22229, "market_cap_change_percentage_24h_usd": -2.25, "updated_at": 1791918294,
                   "total_market_cap": {"usd": 2.55e12, "btc": 3.4e7}, "total_volume": {"usd": 9.1e10},
                   "market_cap_percentage": {"btc": 59.17, "eth": 10.86}}}
TRENDING = {"coins": [
    {"item": {"id": "wisp-2", "name": "Wisp", "symbol": "wisp", "market_cap_rank": 710,
              "data": {"price_change_percentage_24h": {"usd": 94.7, "eur": 90.0}}}},
    {"item": {"id": "no-data", "name": "No data", "symbol": "nd", "market_cap_rank": None}},
    {"item": {"id": "x", "name": "No symbol"}},
    "garbage",
]}


def coin(symbol, change, name=None):
    return market.coin_from_row({"id": symbol.lower(), "symbol": symbol.lower(), "name": name or symbol, "current_price": 1.0,
                                 "price_change_percentage_24h": change})


# ---------- market overview and trending ----------

def test_overview_is_read_from_the_global_endpoint(monkeypatch):
    seen = []
    monkeypatch.setattr(market.requests, "get", lambda url, **k: seen.append(url) or Resp(GLOBAL))
    overview = market.fetch_overview()
    assert seen[0].endswith("/global")
    assert overview == {"total_market_cap": 2.55e12, "total_volume": 9.1e10, "market_cap_change_24h": -2.25, "btc_dominance": 59.17,
                        "eth_dominance": 10.86, "active_coins": 22229, "updated_at": 1791918294}


@pytest.mark.parametrize("payload", [[], {}, {"data": None}, {"data": {}}, {"data": {"total_market_cap": "lots"}}, {"data": {"total_market_cap": {"usd": "x"}}}])
def test_an_unusable_global_response_gives_no_overview(monkeypatch, payload):
    monkeypatch.setattr(market.requests, "get", lambda *a, **k: Resp(payload))
    assert market.fetch_overview() is None


def test_missing_overview_fields_become_none_not_errors(monkeypatch):
    monkeypatch.setattr(market.requests, "get", lambda *a, **k: Resp({"data": {"total_market_cap": {"usd": 5.0e11}}}))
    overview = market.fetch_overview()
    assert overview["total_market_cap"] == 5.0e11
    assert overview["btc_dominance"] is None and overview["active_coins"] is None


def test_trending_keeps_only_usable_entries_and_uppercases_tickers(monkeypatch):
    monkeypatch.setattr(market.requests, "get", lambda *a, **k: Resp(TRENDING))
    trending = market.fetch_trending()
    assert [t["symbol"] for t in trending] == ["WISP", "ND"]
    assert trending[0] == {"id": "wisp-2", "name": "Wisp", "symbol": "WISP", "rank": 710, "change_24h": 94.7}
    assert trending[1]["rank"] is None and trending[1]["change_24h"] is None
    market.clear_cache()                      # the first answer is cached otherwise
    monkeypatch.setattr(market.requests, "get", lambda *a, **k: Resp([1, 2]))
    assert market.fetch_trending() == []


def test_the_overview_can_be_switched_off_and_then_costs_no_requests(monkeypatch):
    calls = []
    monkeypatch.setattr(market.requests, "get", lambda *a, **k: calls.append(a[0]) or Resp([]))
    monkeypatch.setenv("OVERVIEW", "0")
    summary = market.build_summary()
    assert summary["overview"] is None and summary["trending"] == []
    assert not any(url.endswith(("/global", "/search/trending")) for url in calls)


def test_overview_failures_are_reported_but_never_fatal(monkeypatch):
    def fake(url, **k):
        if url.endswith(("/global", "/search/trending")):
            return Resp(status=500)
        return Resp([{"id": "bitcoin", "symbol": "btc", "name": "Bitcoin", "current_price": 100.0, "price_change_percentage_24h": 1.0}])
    monkeypatch.setattr(market.requests, "get", fake)
    summary = market.build_summary()
    assert summary["ok"] and summary["headline"]
    assert summary["overview"] is None and summary["trending"] == []
    assert sum("/global" in e or "/search/trending" in e for e in summary["errors"]) == 2


def test_mock_summary_has_overview_trending_and_alerts(monkeypatch):
    monkeypatch.setenv("MOCK_DATA", "1")
    body = create_app().test_client().get("/api/summary").get_json()
    assert body["overview"]["total_market_cap"] == 2.55e12
    assert [t["symbol"] for t in body["trending"]] == ["TRA", "TRB", "TRC"]
    assert body["alerts"]["threshold"] == 5.0
    assert [a["symbol"] for a in body["alerts"]["coins"]] == ["MEM4", "MEM1"]     # MEM2 moves -4.8%, under the 5% line
    assert "Total market cap $2.55T (+0.8% 24h), BTC dominance 52.3%" in body["post"]
    assert "Big moves (5%+ in 24h): MEM4 +9.1%, MEM1 +6.5%" in body["post"]


# ---------- big-move alerts ----------

def test_alerts_include_both_directions_sorted_by_size_and_count_each_coin_once():
    headline = [coin("btc", 2.0)]
    details = {"X": [coin("a", 9.0), coin("b", -12.0), coin("c", 4.9), coin("btc", 2.0)], "Y": [coin("a", 9.0), coin("d", -5.0), coin("e", None)]}
    result = market.alerts(headline, details, threshold=5.0)
    assert [(a["symbol"], a["direction"]) for a in result] == [("B", "down"), ("A", "up"), ("D", "down")]
    assert result[0]["sector"] == "X"
    assert market.alerts(headline, details, threshold=50.0) == []


def test_the_alert_limit_and_threshold_setting(monkeypatch):
    details = {"X": [coin(f"c{i}", 10 + i) for i in range(15)]}
    assert len(market.alerts([], details, threshold=5.0)) == 10
    assert market.alert_threshold() == 5.0
    for raw, expected in (("7.5", 7.5), ("0", 0.5), ("500", 100.0), ("lots", 5.0)):
        monkeypatch.setenv("ALERT_THRESHOLD", raw)
        assert market.alert_threshold() == expected


def test_posts_are_unchanged_when_no_overview_or_alerts_are_given():
    prices = {"bitcoin": {"usd": 100.0, "usd_24h_change": 2.0, "name": "Bitcoin"}}
    plain = market.build_post(prices, {"X": ["A"]})
    assert "Total market cap" not in plain and "Big moves" not in plain
    assert market.build_post(prices, {"X": ["A"]}, None, None, [], 5.0) == plain


def test_posts_show_the_market_line_and_big_moves():
    prices = {"bitcoin": {"usd": 100.0, "usd_24h_change": 2.0, "name": "Bitcoin"}}
    overview = {"total_market_cap": 1.2e12, "market_cap_change_24h": -1.04, "btc_dominance": 55.55}
    moves = [{"symbol": "AAA", "change_24h": 12.34}, {"symbol": "BBB", "change_24h": -8.0}]
    text = market.build_post(prices, {}, None, overview, moves, 5.0)
    assert "Total market cap $1.20T (-1.0% 24h), BTC dominance 55.5%" in text
    assert "Big moves (5%+ in 24h): AAA +12.3%, BBB -8.0%" in text
    md = market.build_markdown(prices, {}, None, overview, moves, 5.0)
    assert "Total market cap $1.20T" in md and "Big moves (5%+ in 24h)" in md
    assert market.build_post(prices, {}, None, {"total_market_cap": None}, [], 5.0).count("Total market cap") == 0


def test_format_big_numbers():
    assert market.format_big(2.55e12) == "$2.55T"
    assert market.format_big(9.1e10) == "$91.0B"
    assert market.format_big(4.2e6) == "$4.2M"
    assert market.format_big(950) == "$950"
    assert market.format_big(None) == "n/a"


# ---------- one request at a time per key ----------

def test_simultaneous_callers_share_one_upstream_request(monkeypatch):
    calls = []

    def slow(*a, **k):
        calls.append(1)
        time.sleep(0.15)
        return Resp({"ok": 1})
    monkeypatch.setattr(market.requests, "get", slow)
    results = []
    threads = [threading.Thread(target=lambda: results.append(market._get("/same", {"a": 1}))) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1
    assert results == [{"ok": 1}] * 8


def test_different_keys_are_still_fetched_in_parallel(monkeypatch):
    started = []
    barrier = threading.Barrier(2, timeout=3)

    def meet(*a, **k):
        started.append(1)
        barrier.wait()          # only passes if both requests are in flight at the same time
        return Resp({"ok": 1})
    monkeypatch.setattr(market.requests, "get", meet)
    threads = [threading.Thread(target=market._get, args=(path, {})) for path in ("/one", "/two")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert len(started) == 2


def test_a_repeated_error_message_is_shown_once(monkeypatch):
    monkeypatch.setattr(market.requests, "get", lambda *a, **k: Resp(status=429))
    summary = market.build_summary()
    assert not summary["ok"]
    assert len(summary["errors"]) == len(set(summary["errors"])) >= 1
    assert any("HTTP 429" in e or "rate limiting" in e for e in summary["errors"])
