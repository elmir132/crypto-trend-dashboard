# Crypto Trend Dashboard

A small Flask app that builds a paste-ready crypto market update with BTC, ETH, and SOL prices, a simple 24-hour sentiment summary, and leading tickers from six CoinGecko sectors. It uses CoinGecko's public API and needs no API key.

![Crypto Trend Dashboard screenshot](docs/screenshot.png)

## What it does

- Shows USD prices and 24-hour changes for Bitcoin, Ethereum, and Solana.
- Summarizes sentiment as **bullish** when more than half of the available BTC, ETH, and SOL 24-hour changes are positive; ties are **bearish**. This is a simple description of recent movement, not a forecast.
- Lists up to five coins by market cap from each CoinGecko category: GameFi, RWA, Memes, DePIN, AI, and Layer 1.
- Creates a plain-text update that can be copied from the dashboard.
- Caches each upstream response for 120 seconds by default and serves the last successful response when a refresh fails.
- Reports category errors and leaves unavailable sectors empty instead of filling them with unrelated coins.

An earlier local version mixed CoinGecko and CoinMarketCap data and included a separate BTC price page. This repository is a smaller rewrite: sector membership comes from CoinGecko categories, missing data is not fabricated, and API credentials are not hard-coded.

## Requirements

- Python 3.11+ (CI currently covers Python 3.11 and 3.12)
- Internet access for live CoinGecko data

The application makes one price request and one request for each of the six sectors on a cold refresh. CoinGecko may rate-limit requests; the local cache reduces repeated calls.

## Run locally

Create a virtual environment and install the dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python app.py
```

Open [http://localhost:5000](http://localhost:5000). To use another port:

```bash
PORT=5057 python app.py
```

On macOS, AirPlay Receiver may already use port 5000; choose another port if the app cannot start.

### Offline demo

Run the dashboard with fixed sample data and no CoinGecko requests:

```bash
MOCK_DATA=1 python app.py
```

The sample prices and sector tickers are illustrative and do not reflect current markets.

## HTTP endpoints

| Route | Response |
| --- | --- |
| `GET /` | Dashboard page with Refresh and Copy controls. |
| `GET /api/summary` | JSON containing `prices`, `sentiment`, `sectors`, the formatted `post`, and any `errors`. Partial data is returned when available; the route responds with HTTP 502 only when no price or sector data could be fetched. |
| `GET /healthz` | `{"ok": true}` when the Flask process is responding. |

`/api/summary` includes the CoinGecko price records keyed by coin ID. `sectors` maps the six display names to lists of uppercase ticker symbols. `sentiment` is `bullish`, `bearish`, or `null` when none of the headline assets has a usable 24-hour change.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `PORT` | `5000` | Port used by the Flask development server. |
| `CACHE_TTL` | `120` | Cache lifetime in seconds for each upstream response. |
| `MOCK_DATA` | unset | Set to `1` to serve fixed sample data without network requests. |

Example with a longer cache lifetime and alternate port:

```bash
CACHE_TTL=300 PORT=5057 python app.py
```

## Tests and continuous integration

Run the test suite locally with:

```bash
python -m pytest -q
```

The tests cover ticker normalization, sentiment rules, formatting, cache behavior, stale-data fallback, upstream errors, sector failures, and the Flask summary endpoint. Upstream calls are mocked, so the suite does not need a CoinGecko key or live API access.

GitHub Actions runs the same test suite on Python 3.11 and 3.12 for pushes to `main`, pull requests, and manual workflow runs.

## Project structure

```text
app.py                  Flask routes and HTTP responses
market.py               CoinGecko access, caching, grouping, sentiment, formatting
templates/index.html    Dashboard UI
tests/test_market.py    Unit and endpoint tests
docs/screenshot.png     Dashboard screenshot
.github/workflows/      GitHub Actions test workflow
```

## Limitations

CoinGecko category membership can change, and its public API can be slow or rate-limited. An empty sector means its category request did not produce data; it does not imply that the sector has no assets. The sentiment rule only describes whether a majority of BTC, ETH, and SOL rose during the previous 24 hours. This dashboard is for summarizing market data, not investment advice or trading signals.

## License

MIT. See [LICENSE](LICENSE).
