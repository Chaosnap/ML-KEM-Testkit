"""acquire_cpa.run_campaign against the software board model and a stand-in
scope: record length taken from the scope, a changed record length stops the
campaign with a resumable file, resume continues it.

    uv run python -m unittest discover -s tests -v
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cpa_toolkit"))
sys.path.insert(0, str(ROOT / "tests"))

import acquire_cpa as A  # noqa: E402
import hdf5_trace_store as HS  # noqa: E402
import test_uart_target as TU  # noqa: E402
import testvectors as TV  # noqa: E402


class StandInScope:
    """Returns `length` samples per capture (0 = the record length is unknown up front)."""

    samples = 0

    def __init__(self, length):
        self.length, self.armed, self.reads = length, 0, 0

    def arm(self):
        self.armed += 1

    def read(self, timeout=10.0):
        self.reads += 1
        return np.full(self.length, self.reads % 256, dtype=np.uint8)

    def sample_rate(self):
        return 1e9

    def info(self):
        return {"id": "stand-in"}


class TestCampaign(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.target = TU.make_target(TU.FakeBoard(has_run=True))
        self.vs = TV.generate("cpa", 4, bytes(range(64)), "valid")
        self.vs_path = self.vs.save(self.tmp / "set.npz")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_record_length_from_the_scope(self):
        sc = StandInScope(1234)
        self.assertEqual(A.measure_record_length(self.target, sc), 1234)
        out = self.tmp / "a.hdf5"
        s = A.run_campaign(self.target, sc, str(out), 1234, vectors=self.vs, quiet=True)
        self.assertEqual((s["n_completed"], s["n_samples"], s["k_match_fraction"]), (4, 1234, 1.0))

    def test_changed_record_length_stops_resumably(self):
        sc, out = StandInScope(500), self.tmp / "b.hdf5"
        stop = {"after": 2}

        def progress(done, total, msg):
            if done == stop["after"]:
                sc.length = 400                       # timebase changed on the bench
        with self.assertRaisesRegex(RuntimeError, "returned 400 samples but this campaign records 500"):
            A.run_campaign(self.target, sc, str(out), 500, vectors=self.vs, quiet=True, progress_cb=progress,
                           vector_set_path=str(self.vs_path))
        s = HS.summarize(out)
        self.assertEqual((s["n_completed"], s["resumable"]), (2, True))
        sc.length = 500                               # set back, resume
        s = A.run_campaign(self.target, sc, str(out), s["n_samples"], resume=True, quiet=True)
        self.assertEqual((s["n_completed"], s["k_match_fraction"]), (4, 1.0))


if __name__ == "__main__":
    unittest.main()
