"""Print the market update in the terminal: python cli.py [--format text|markdown|json|csv] [--mock]"""
import argparse
import json
import os
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description="Print the crypto market update without starting the web app.")
    parser.add_argument("--format", choices=["text", "markdown", "json", "csv"], default="text")
    parser.add_argument("--mock", action="store_true", help="use fixed sample data and make no network requests")
    args = parser.parse_args(argv)
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
