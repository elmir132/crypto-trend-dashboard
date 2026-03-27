# Changelog

## Unreleased

- Whole-market overview (total market cap and its 24h change, 24h volume, Bitcoin and Ethereum share, coin count) and a trending list, from CoinGecko's `/global` and `/search/trending`. They never make the summary fail; `OVERVIEW=0` turns them off.
- Big-move alerts: coins that moved `ALERT_THRESHOLD` percent or more are listed on the page and in the update text.
- One upstream request per key at a time: simultaneous visitors share one CoinGecko call.
- Static sample-data demo (`scripts/build_demo.py`, built copy in `docs/`) that runs without a server.
- Background refresher (`REFRESH_INTERVAL`) that keeps the cache warm and saves history while the page is closed; status shown at `/healthz`.
- `cli.py --webhook` / `DIGEST_WEBHOOK_URL`: post the update to a Slack or Discord incoming webhook. The URL is never printed.
- Repeated error messages are shown once.

## 2.0

- Richer headline data with 7-day sparklines, sector averages, breadth, biggest movers, local history, text/Markdown/CSV export, light and dark themes, a strict Content-Security-Policy, rate-limit backoff, command-line output and configurable coins and sectors.
