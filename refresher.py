"""Runs a piece of work on a timer in the background.

Used to keep the cache warm, and the local history growing, while nobody has the page open.
The work runs once straight away, then every `interval` seconds. An error is remembered and
logged but never stops the loop.
"""
import logging
import threading
import time

log = logging.getLogger("refresher")


class Refresher:
    def __init__(self, work, interval, run_now=True):
        if interval <= 0:
            raise ValueError("interval must be positive")
        self.work = work
        self.interval = interval
        self.run_now = run_now
        self.runs = 0
        self.last_run = None
        self.last_error = None
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="refresher", daemon=True)
            self._thread.start()
        return self

    def _once(self):
        try:
            self.work()
            self.last_error = None
        except Exception as exc:  # one bad run must not end the loop
            self.last_error = f"{type(exc).__name__}: {exc}"
            log.warning("background refresh failed: %s", self.last_error)
        finally:
            self.runs += 1
            self.last_run = time.time()

    def _loop(self):
        if self.run_now:
            self._once()
        while not self._stop.wait(self.interval):
            self._once()

    def stop(self, timeout=5):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def status(self):
        return {"running": self.running, "interval": self.interval, "runs": self.runs,
                "last_run": self.last_run, "last_error": self.last_error}
