"""Print the market update in the terminal: python cli.py [--format text|markdown|json|csv] [--mock]"""
import argparse
import json
import os
import sys
from urllib.parse import urlparse

import requests

LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


class DigestError(Exception):
    pass


def validate_webhook(url):
    """https only, except for a server on this machine (useful for testing). Raises DigestError."""
    parsed = urlparse(url or "")
    if parsed.scheme == "https" and parsed.hostname:
        return url
    if parsed.scheme == "http" and parsed.hostname in LOCAL_HOSTS:
        return url
    raise DigestError("the webhook must be an https URL (plain http is only allowed for localhost)")


def send_digest(url, summary, fmt="slack", post=requests.post):
    """Post the update to a Slack or Discord incoming webhook. The URL is a secret, so it is
    never printed and error messages never contain it. Redirects are not followed."""
    validate_webhook(url)
    if fmt == "discord":
        payload = {"content": summary["post_markdown"][:1990]}   # Discord allows 2000 characters
    else:
        payload = {"text": summary["post"]}
    try:
        response = post(url, json=payload, timeout=10, allow_redirects=False)
    except requests.RequestException as exc:
        raise DigestError(f"could not reach the webhook ({type(exc).__name__})")
    if not 200 <= response.status_code < 300:
        raise DigestError(f"the webhook answered HTTP {response.status_code}")
    return response.status_code


def main(argv=None):
    parser = argparse.ArgumentParser(description="Print the crypto market update without starting the web app.")
    parser.add_argument("--format", choices=["text", "markdown", "json", "csv"], default="text")
    parser.add_argument("--mock", action="store_true", help="use fixed sample data and make no network requests")
    parser.add_argument("--webhook", metavar="URL", help="also post the update to a Slack or Discord webhook (or set DIGEST_WEBHOOK_URL, which keeps the URL out of your shell history)")
    parser.add_argument("--webhook-format", choices=["slack", "discord"], default="slack")
    args = parser.parse_args(argv)
    webhook = args.webhook or os.environ.get("DIGEST_WEBHOOK_URL")
    if webhook:
        try:
            validate_webhook(webhook)
        except DigestError as exc:
            print(f"cli: {exc}", file=sys.stderr)
            return 2
    if args.mock:
        os.environ["MOCK_DATA"] = "1"

    import market  # imported after --mock so MOCK_DATA is already set

    try:
        market.configure()
    except market.ConfigError as exc:
        print(f"cli: {exc}", file=sys.stderr)
        return 2
    summary = market.build_summary()
    if not summary["ok"]:
        for error in summary["errors"]:
            print(f"cli: {error}", file=sys.stderr)
        return 1
    if args.format == "json":
        print(json.dumps({k: v for k, v in summary.items() if k != "ok"}, indent=2))
    elif args.format == "csv":
        sys.stdout.write(market.build_csv(summary["headline"], summary["sector_details"]))
    else:
        print(summary["post_markdown"] if args.format == "markdown" else summary["post"])
    for error in summary["errors"]:
        print(f"cli: {error}", file=sys.stderr)
    if webhook:
        try:
            status = send_digest(webhook, summary, args.webhook_format)
        except DigestError as exc:
            print(f"cli: {exc}", file=sys.stderr)
            return 1
        print(f"cli: digest sent (HTTP {status})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
