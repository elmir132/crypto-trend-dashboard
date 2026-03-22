import os
import sys

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import market  # noqa: E402


def _no_network(*args, **kwargs):
    raise requests.ConnectionError("the network is switched off in tests")


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    """Every test starts with default settings, an empty cache and no history file."""
    for name in ("MOCK_DATA", "COINS", "SECTORS_FILE", "COINGECKO_API_KEY", "COINGECKO_BASE_URL", "HISTORY", "HISTORY_DB", "HISTORY_MIN_INTERVAL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HISTORY", "0")
    monkeypatch.setattr(market.requests, "get", _no_network)   # a test that needs a response sets its own
    market.configure({})
    market.clear_cache()
    yield
    market.configure({})
    market.clear_cache()
