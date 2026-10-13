import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import market  # noqa: E402


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    """Every test starts with default settings, an empty cache and no history file."""
    for name in ("MOCK_DATA", "COINS", "SECTORS_FILE", "COINGECKO_API_KEY", "COINGECKO_BASE_URL", "HISTORY", "HISTORY_DB", "HISTORY_MIN_INTERVAL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HISTORY", "0")
    market.configure({})
    market.clear_cache()
    yield
    market.configure({})
    market.clear_cache()
