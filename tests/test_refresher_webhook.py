import http.server
import json
import threading
import time

import pytest
import requests

import cli
import history as history_module
import market
from app import create_app, refresh_interval_from_env
from refresher import Refresher


def wait_for(condition, seconds=3.0):
    end = time.time() + seconds
    while time.time() < end:
        if condition():
            return True
        time.sleep(0.01)
    return False


# ---------- Refresher ----------

def test_the_work_runs_at_once_and_then_repeatedly_until_stopped():
    calls = []
    refresher = Refresher(lambda: calls.append(1), 0.05).start()
    assert wait_for(lambda: len(calls) >= 3)
    refresher.stop()
    assert not refresher.running
    count = len(calls)
    time.sleep(0.15)
    assert len(calls) == count                  # nothing runs after stop


def test_an_error_is_remembered_and_the_loop_keeps_going():
    state = {"n": 0}

    def flaky():
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("first run fails")
    refresher = Refresher(flaky, 0.03).start()
    assert wait_for(lambda: state["n"] >= 3)
    refresher.stop()
    assert refresher.runs >= 3
    assert refresher.last_error is None          # a later success clears the error
    first = Refresher(lambda: 1 / 0, 0.5).start()
    assert wait_for(lambda: first.runs >= 1)
    assert first.last_error == "ZeroDivisionError: division by zero"
    first.stop()


def test_run_now_false_waits_for_the_first_interval():
    calls = []
    refresher = Refresher(lambda: calls.append(1), 0.2, run_now=False).start()
    time.sleep(0.05)
    assert calls == []
    assert wait_for(lambda: calls)
    refresher.stop()


def test_a_non_positive_interval_is_rejected():
    for bad in (0, -5):
        with pytest.raises(ValueError):
            Refresher(lambda: None, bad)


# ---------- wiring into the app ----------

@pytest.mark.parametrize("env,expected", [
    ({}, 0), ({"REFRESH_INTERVAL": "0"}, 0), ({"REFRESH_INTERVAL": "abc"}, 0), ({"REFRESH_INTERVAL": "-30"}, 0),
    ({"REFRESH_INTERVAL": "10"}, 120),            # at least 60, and never faster than the 120 s cache
    ({"REFRESH_INTERVAL": "600"}, 600),
    ({"REFRESH_INTERVAL": "600", "MOCK_DATA": "1"}, 0),
])
def test_refresh_interval_setting(env, expected, monkeypatch):
    monkeypatch.setattr(market, "CACHE_TTL", 120)
    assert refresh_interval_from_env(env) == expected


def test_the_app_refreshes_in_the_background_records_history_and_reports_status(monkeypatch, tmp_path):
    summary = {"ok": True, "mock": False, "stale": False, "errors": [],
               "headline": [{"id": "bitcoin", "price": 100.0}], "sector_stats": {}, "breadth": {"share_up": 0.5}}
    calls = []
    monkeypatch.setattr(market, "build_summary", lambda: calls.append(1) or dict(summary))
    store = history_module.History(str(tmp_path / "h.sqlite"), min_interval=30)
    app = create_app(history_store=store, refresh_interval=3600)
    try:
        assert wait_for(lambda: calls)
        assert wait_for(lambda: store.series(hours=1))               # the background run saved a snapshot
        health = app.test_client().get("/healthz").get_json()
        assert health["ok"] and health["refresher"]["running"] and health["refresher"]["interval"] == 3600
    finally:
        app.refresher.stop()


def test_no_refresher_by_default_and_health_stays_simple():
    app = create_app()
    assert app.refresher is None
    assert app.test_client().get("/healthz").get_json() == {"ok": True}


# ---------- webhook digest ----------

class Hook(http.server.BaseHTTPRequestHandler):
    received = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        Hook.received.append((self.path, json.loads(body)))
        if self.path.startswith("/redirect"):
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:1/elsewhere")
            self.end_headers()
        elif self.path.startswith("/fail"):
            self.send_response(500)
            self.end_headers()
        else:
            self.send_response(204)
            self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def hook():
    Hook.received = []
    server = http.server.HTTPServer(("127.0.0.1", 0), Hook)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def test_a_slack_digest_posts_the_plain_text_update(hook, capsys):
    assert cli.main(["--mock", "--webhook", hook + "/T0/secret-token"]) == 0
    path, payload = Hook.received[0]
    assert path == "/T0/secret-token"
    assert list(payload) == ["text"] and payload["text"].startswith("▲ Bitcoin") and "**" not in payload["text"]
    assert "digest sent (HTTP 204)" in capsys.readouterr().err


def test_a_discord_digest_uses_markdown_in_a_content_field(hook):
    assert cli.main(["--mock", "--webhook", hook + "/discord", "--webhook-format", "discord"]) == 0
    payload = Hook.received[0][1]
    assert list(payload) == ["content"] and payload["content"].startswith("**Crypto market update**")


def test_discord_content_is_cut_to_fit_its_limit():
    sent = {}

    class R:
        status_code = 200
    cli.send_digest("https://example.test/hook", {"post_markdown": "x" * 5000, "post": "y"}, "discord",
                    post=lambda url, **kw: sent.update(kw) or R())
    assert len(sent["json"]["content"]) <= 2000


def test_the_webhook_url_can_come_from_the_environment(hook, monkeypatch):
    monkeypatch.setenv("DIGEST_WEBHOOK_URL", hook + "/from-env")
    assert cli.main(["--mock"]) == 0
    assert Hook.received[0][0] == "/from-env"


def test_the_url_is_never_printed_even_when_sending_fails(hook, capsys):
    secret = "/services/T000/B000/SUPERSECRETTOKEN"
    assert cli.main(["--mock", "--webhook", hook + "/fail" + secret]) == 1
    seen = capsys.readouterr()
    assert "HTTP 500" in seen.err
    assert "SUPERSECRETTOKEN" not in seen.out + seen.err and "127.0.0.1" not in seen.err


def test_redirects_are_not_followed(hook, capsys):
    assert cli.main(["--mock", "--webhook", hook + "/redirect"]) == 1
    assert "HTTP 302" in capsys.readouterr().err
    assert len(Hook.received) == 1                  # it did not go on to the Location address


def test_unreachable_webhooks_give_a_clear_error_without_the_address(capsys):
    assert cli.main(["--mock", "--webhook", "http://127.0.0.1:1/secret-path"]) == 1
    err = capsys.readouterr().err
    assert "could not reach the webhook" in err and "secret-path" not in err


@pytest.mark.parametrize("url", ["http://example.com/hook", "ftp://example.com/x", "not a url", "https://", "//example.com"])
def test_only_https_or_localhost_urls_are_accepted(url, capsys):
    assert cli.main(["--mock", "--webhook", url]) == 2
    assert "must be an https URL" in capsys.readouterr().err
    with pytest.raises(cli.DigestError):
        cli.validate_webhook(url)
    assert cli.validate_webhook("https://hooks.slack.com/services/x") and cli.validate_webhook("http://localhost:9/x")


def test_no_digest_is_sent_when_there_is_no_data(hook, monkeypatch):
    def boom(*a, **k):
        raise market.UpstreamError("down")
    monkeypatch.setattr(market, "fetch_prices", boom)
    monkeypatch.setattr(market, "fetch_sector", boom)
    assert cli.main(["--webhook", hook + "/x"]) == 1
    assert Hook.received == []


def test_send_digest_reports_network_errors_without_a_real_network():
    def broken(*a, **k):
        raise requests.Timeout("slow")
    with pytest.raises(cli.DigestError, match="Timeout"):
        cli.send_digest("https://example.test/h", {"post": "p", "post_markdown": "m"}, post=broken)
