"""web_gui's live-preview endpoint as a long poll: a waiting request returns as
soon as the acquisition stores a newer trace, and answers "unchanged" when
none arrives within ``wait_ms``.

    uv run python -m unittest discover -s tests -v
"""

import sys
import threading
import time
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cpa_toolkit"))

import web_gui as W  # noqa: E402


class TestLivePoll(unittest.TestCase):
    def setUp(self):
        self.job = "test-live-%d" % id(self)
        self.live = W._LiveStats(1000, 1e9, "cpa")
        with W._live_lock:
            W._live[self.job] = self.live

    def tearDown(self):
        with W._live_lock:
            W._live.pop(self.job, None)

    def _get(self, since, wait_ms):
        return W.api_acquire_live({"job_id": self.job, "since": str(since), "wait_ms": str(wait_ms)})

    def test_no_trace_yet(self):
        t0 = time.monotonic()
        self.assertFalse(self._get(-1, 150)["ready"])
        self.assertGreaterEqual(time.monotonic() - t0, 0.12)   # waited for the first trace

    def test_returns_when_a_trace_arrives(self):
        self.live.add(0, np.zeros(1000), None, True)
        threading.Timer(0.2, lambda: self.live.add(1, np.ones(1000), None, True)).start()
        t0 = time.monotonic()
        r = self._get(1, 1500)
        dt = time.monotonic() - t0
        self.assertEqual(r["seq"], 2)
        self.assertEqual(r["index"], 1)
        self.assertGreater(dt, 0.15)
        self.assertLess(dt, 1.0)            # woken by the trace, not by the timeout

    def test_unchanged_after_the_wait(self):
        self.live.add(0, np.zeros(1000), None, True)
        t0 = time.monotonic()
        r = self._get(1, 200)
        self.assertTrue(r["unchanged"])
        self.assertGreaterEqual(time.monotonic() - t0, 0.18)

    def test_immediate_without_wait(self):
        self.live.add(0, np.zeros(1000), None, True)
        t0 = time.monotonic()
        self.assertTrue(self._get(1, 0)["unchanged"])
        self.assertLess(time.monotonic() - t0, 0.1)
        self.assertEqual(self._get(-1, 0)["seq"], 1)

    def test_wait_is_capped(self):
        self.live.add(0, np.zeros(1000), None, True)
        t0 = time.monotonic()
        self._get(1, 60000)
        self.assertLess(time.monotonic() - t0, W.LIVE_WAIT_MAX_S + 0.5)


if __name__ == "__main__":
    unittest.main()
