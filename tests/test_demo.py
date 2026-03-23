import importlib.util
import json
import os
import re
import shutil
import subprocess

import pytest

import market
from app import create_app

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_builder():
    spec = importlib.util.spec_from_file_location("build_demo", os.path.join(ROOT, "scripts", "build_demo.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def built(tmp_path, monkeypatch):
    monkeypatch.setenv("MOCK_DATA", "1")
    out = load_builder().build(str(tmp_path / "site"))
    yield out
    market.configure({})


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def test_the_demo_is_a_folder_of_plain_files(built):
    for name in ("index.html", "summary.json", ".nojekyll", "static/app.js", "static/style.css"):
        assert os.path.isfile(os.path.join(built, name)), name


def test_demo_links_are_relative_so_it_works_under_a_project_pages_path(built):
    html = read(os.path.join(built, "index.html"))
    assert 'href="static/style.css"' in html and 'src="static/app.js"' in html
    assert '"/static' not in html and "'/static" not in html
    assert 'data-demo="true"' in html


def test_demo_page_keeps_the_strict_page_rules(built):
    html = read(os.path.join(built, "index.html"))
    assert "<style" not in html and ' style="' not in html and " onclick=" not in html
    assert all("src=" in tag.split(">")[0] for tag in html.split("<script")[1:])


def test_the_demo_summary_is_sample_data_with_every_new_section(built):
    summary = json.loads(read(os.path.join(built, "summary.json")))
    assert summary["mock"] is True and "ok" not in summary
    assert summary["overview"]["total_market_cap"] > 0
    assert len(summary["trending"]) == 3
    assert summary["alerts"]["coins"]
    assert [c["symbol"] for c in summary["headline"]] == ["BTC", "ETH", "SOL"]
    assert all(c["name"].startswith("Sample") for details in summary["sector_details"].values() for c in details)


def test_the_demo_ships_the_same_script_and_styles_as_the_app(built):
    for name in ("app.js", "style.css"):
        assert read(os.path.join(built, "static", name)) == read(os.path.join(ROOT, "static", name))


def test_building_twice_replaces_the_old_static_folder(built):
    stale = os.path.join(built, "static", "old-file.txt")
    with open(stale, "w") as handle:
        handle.write("x")
    load_builder().build(built)
    assert not os.path.exists(stale)


def test_the_live_page_is_not_in_demo_mode(monkeypatch):
    monkeypatch.setenv("MOCK_DATA", "1")
    html = create_app().test_client().get("/").get_data(as_text=True)
    assert 'data-demo="false"' in html
    for section in ('id="overview"', 'id="alerts"', 'id="trending"', 'id="csv"'):
        assert section in html, section


def test_the_script_reads_the_prepared_file_in_demo_mode_and_skips_history_and_csv():
    source = read(os.path.join(ROOT, "static", "app.js"))
    assert 'DEMO ? "summary.json" : "/api/summary"' in source
    assert re.search(r"if \(DEMO\) \$\(\"csv\"\)\.remove\(\)", source)
    assert "History is not part of the static demo" in source
    for forbidden in ("innerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert forbidden not in source


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_script_syntax_is_valid():
    assert subprocess.run(["node", "--check", os.path.join(ROOT, "static", "app.js")], capture_output=True).returncode == 0
