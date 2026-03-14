import pytest
import requests

import market
from app import create_app


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
    monkeypatch.delenv("MOCK_DATA", raising=False)
    market.clear_cache()


def test_sector_symbols_uppercases_and_skips_missing():
    rows = [{"symbol": "doge"}, {"symbol": ""}, {}, {"symbol": "shib"}]
    assert market.sector_symbols(rows) == ["DOGE", "SHIB"]


def test_sentiment_majority_up_is_bullish():
    prices = {"a": {"usd_24h_change": 1}, "b": {"usd_24h_change": 2}, "c": {"usd_24h_change": -1}}
    assert market.sentiment(prices) == "bullish"


def test_sentiment_ties_and_majority_down_are_bearish():
    assert market.sentiment({"a": {"usd_24h_change": 1}, "b": {"usd_24h_change": -1}}) == "bearish"
    assert market.sentiment({"a": {"usd_24h_change": -3}}) == "bearish"


def test_sentiment_ignores_missing_change_and_handles_empty():
    assert market.sentiment({"a": {"usd_24h_change": None}}) is None
    assert market.sentiment({}) is None


def test_format_line_handles_missing_change():
    assert market.format_line("Bitcoin", 64250.0, 1.84) == "▲ Bitcoin $64,250.00 (+1.8%)"
    assert market.format_line("Solana", 148.2, None).endswith("(n/a)")


def test_failed_sector_stays_empty_instead_of_being_padded(monkeypatch):
    def fake(category_id):
        if category_id == "depin":
            raise market.UpstreamError("boom")
        return [{"symbol": "x"}]
    monkeypatch.setattr(market, "fetch_sector", fake)
    sectors, errors = market.build_sectors()
    assert sectors["DePIN"] == []
    assert sectors["AI"] == ["X"]
    assert len(errors) == 1


def test_get_wraps_network_errors(monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("down")
    monkeypatch.setattr(market.requests, "get", boom)
    with pytest.raises(market.UpstreamError):
        market._get("/simple/price", {})


def test_get_caches_within_ttl(monkeypatch):
    calls = []

    class R:
        def raise_for_status(self): pass
        def json(self): return {"ok": 1}

    monkeypatch.setattr(market.requests, "get", lambda *a, **k: calls.append(1) or R())
    market._get("/x", {"a": 1})
    market._get("/x", {"a": 1})
    assert len(calls) == 1


def test_summary_endpoint_in_mock_mode(monkeypatch):
    monkeypatch.setenv("MOCK_DATA", "1")
    client = create_app().test_client()
    res = client.get("/api/summary")
    body = res.get_json()
    assert res.status_code == 200
    assert body["sentiment"] == "bullish"
    assert "Bitcoin" in body["post"]
    assert set(body["sectors"]) == set(market.SECTORS)


def test_summary_returns_502_when_everything_fails(monkeypatch):
    def boom(*a, **k):
        raise market.UpstreamError("down")
    monkeypatch.setattr(market, "fetch_prices", boom)
    monkeypatch.setattr(market, "fetch_sector", boom)
    res = create_app().test_client().get("/api/summary")
    assert res.status_code == 502
    assert res.get_json()["errors"]


def test_get_serves_stale_data_when_upstream_fails_after_a_success(monkeypatch):
    class R:
        def raise_for_status(self): pass
        def json(self): return {"ok": 1}

    monkeypatch.setattr(market.requests, "get", lambda *a, **k: R())
    assert market._get("/x", {}) == {"ok": 1}
    monkeypatch.setattr(market, "CACHE_TTL", 0)  # force the entry to count as expired

    def boom(*a, **k):
        raise requests.ConnectionError("down")
    monkeypatch.setattr(market.requests, "get", boom)
    assert market._get("/x", {}) == {"ok": 1}
