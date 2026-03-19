# Crypto Trend Dashboard

A small Flask app that builds a paste-ready crypto market update: BTC / ETH / SOL prices with 24h change, a simple sentiment line, and the top coins per sector (GameFi, RWA, Memes, DePIN, AI, L1). Data comes from the public CoinGecko API, so no API key is needed.

I wrote the first version earlier as a personal tool for drafting market posts (it combined a CoinGecko/CoinMarketCap Flask backend with a small BTC price page). In October 2026 I rewrote it: the original sector grouping never actually matched anything (it relied on a category field the API does not return) and padded sectors with unrelated coins, and it hardcoded API keys. The rewrite fixes that.

## What it does

- `GET /api/summary` returns prices, sentiment, sector tickers and the formatted post as JSON.
- `GET /` is a one-page UI with Refresh and Copy buttons.
- Sectors come from CoinGecko category queries (`/coins/markets?category=...`), top 5 by market cap.
- A sector that fails to load stays empty and is reported in `errors`, never filled with unrelated coins.
- Responses are cached for 120 seconds (`CACHE_TTL`). If CoinGecko rate-limits (HTTP 429), the last good data is served instead.
- Returns HTTP 502 only when nothing at all could be fetched.

Sentiment is a deliberately simple rule: bullish if more than half of BTC/ETH/SOL rose in the last 24 hours. It is a talking point, not a signal, and not financial advice.

## Run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python app.py                  # http://localhost:5000  (PORT=5057 python app.py to change)
MOCK_DATA=1 python app.py      # offline demo with fixed data
pytest -q
```

On macOS, port 5000 is often taken by AirPlay Receiver; use `PORT=5057`.

## Limits

- The free CoinGecko tier is rate-limited, and one refresh makes 7 requests. Expect an occasional empty sector right after a cold start.
- Sector membership is whatever CoinGecko's categories say.

## Layout

```
app.py          Flask routes
market.py       fetching, caching, sector grouping, sentiment, formatting
templates/      single-page UI
tests/          pytest suite (network is mocked)
```
