"""A small local record of past summaries, so the dashboard can chart its own history.

Everything stays on this machine in one SQLite file. A snapshot is saved at most once per
interval, only when real (not sample, not stale) price data came back, and old rows are
deleted after the retention period. Set HISTORY=0 to switch it off.
"""
import json
import os
import sqlite3
import time

DEFAULT_PATH = os.path.join("data", "history.sqlite")


class History:
    def __init__(self, path=DEFAULT_PATH, min_interval=300, retention_days=30):
        self.path = path
        self.min_interval = min_interval
        self.retention_seconds = retention_days * 86400
        folder = os.path.dirname(os.path.abspath(path))
        os.makedirs(folder, exist_ok=True)
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS snapshots (ts INTEGER PRIMARY KEY, data TEXT NOT NULL)")

    def _connect(self):
        return sqlite3.connect(self.path, timeout=5)

    def record(self, summary, now=None):
        """Save one snapshot. Returns True if a row was written."""
        if summary.get("mock") or summary.get("stale") or not summary.get("headline"):
            return False
        now = int(time.time() if now is None else now)
        data = {
            "prices": {c["id"]: c["price"] for c in summary["headline"] if c.get("price") is not None},
            "sector_avg": {n: s["avg_change_24h"] for n, s in summary["sector_stats"].items() if s["avg_change_24h"] is not None},
            "share_up": summary["breadth"]["share_up"],
        }
        if not data["prices"]:
            return False
        with self._connect() as db:
            last = db.execute("SELECT MAX(ts) FROM snapshots").fetchone()[0]
            if last is not None and now - last < self.min_interval:
                return False
            db.execute("INSERT OR REPLACE INTO snapshots (ts, data) VALUES (?, ?)", (now, json.dumps(data)))
            db.execute("DELETE FROM snapshots WHERE ts < ?", (now - self.retention_seconds,))
        return True

    def series(self, hours=24, now=None):
        """Snapshots from the last `hours`, oldest first."""
        now = int(time.time() if now is None else now)
        since = now - int(hours * 3600)
        with self._connect() as db:
            rows = db.execute("SELECT ts, data FROM snapshots WHERE ts >= ? ORDER BY ts", (since,)).fetchall()
        points = []
        for ts, raw in rows:
            try:
                points.append({"ts": ts, **json.loads(raw)})
            except ValueError:
                continue  # a damaged row is skipped, not fatal
        return points


def from_env(env=None):
    """A History built from HISTORY, HISTORY_DB and HISTORY_MIN_INTERVAL, or None when off.
    Sample-data mode never records."""
    env = os.environ if env is None else env
    if env.get("HISTORY", "1") == "0" or env.get("MOCK_DATA") == "1":
        return None
    try:
        interval = max(30, int(env.get("HISTORY_MIN_INTERVAL", "300")))
    except ValueError:
        interval = 300
    return History(path=env.get("HISTORY_DB", DEFAULT_PATH), min_interval=interval)
