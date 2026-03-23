"""Build a static copy of the dashboard from the built-in sample data.

    python scripts/build_demo.py [output-folder]      (default: docs)

It writes index.html, summary.json and static/ so the page can be hosted anywhere that serves
plain files, for example GitHub Pages with the "docs" folder as the source. Nothing is fetched
from CoinGecko: every number comes from the offline sample data and is made up.
"""
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["MOCK_DATA"] = "1"   # must be set before the app and market modules are imported
os.environ["HISTORY"] = "0"

from flask import render_template  # noqa: E402

import market  # noqa: E402
from app import create_app  # noqa: E402


def build(out):
    market.configure({})
    app = create_app()
    with app.test_request_context():
        html = render_template("index.html", static_base="static/", demo=True)
    summary = app.test_client().get("/api/summary").get_json()
    summary.pop("ok", None)
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "index.html"), "w", encoding="utf-8") as handle:
        handle.write(html)
    with open(os.path.join(out, "summary.json"), "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=1)
        handle.write("\n")
    target = os.path.join(out, "static")
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(os.path.join(ROOT, "static"), target)
    open(os.path.join(out, ".nojekyll"), "w").close()   # tell GitHub Pages not to run Jekyll
    return out


if __name__ == "__main__":
    folder = build(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "docs"))
    print(f"demo written to {folder}")
