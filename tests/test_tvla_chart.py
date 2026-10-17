"""The TVLA chart must keep the peaks of the |t| curve it decimates.

A TVLA verdict is "does |t| cross 4.5 anywhere"; plotting every 62nd sample of a
125 000-sample curve would hide a one-sample peak the KPI above it reports.

    uv run python -m unittest discover -s tests -v
"""

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cpa_toolkit"))

import tvla_analysis as TA  # noqa: E402
from hdf5_trace_store import TraceWriter  # noqa: E402


class TestPeakIndices(unittest.TestCase):
    def test_short_curve_is_not_decimated(self):
        self.assertEqual(TA.peak_indices(np.arange(10.0), 2000).tolist(), list(range(10)))

    def test_every_bin_keeps_its_largest_sample(self):
        rng = np.random.default_rng(0)
        a = rng.random(10_000)
        a[7777] = 50.0
        a[3] = 40.0
        a[9999] = 30.0                                  # the last, partial bin
        idx = TA.peak_indices(a, 500)
        self.assertLessEqual(idx.size, 500)
        self.assertTrue(np.all(np.diff(idx) > 0))       # increasing, no duplicates
        for s in (7777, 3, 9999):
            self.assertIn(s, idx.tolist())

    def test_odd_lengths_never_index_past_the_end(self):
        for n in (2001, 2999, 4001, 124_999):
            idx = TA.peak_indices(np.random.default_rng(n).random(n), 2000)
            self.assertLess(idx.max(), n)
            self.assertLessEqual(idx.size, 2000)


class TestChartData(unittest.TestCase):
    def test_a_single_sample_peak_survives_the_decimation(self):
        n_traces, n_samples, spike = 80, 30_001, 12_345
        rng = np.random.default_rng(1)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.hdf5"
            with TraceWriter(path, n_traces, n_samples, bytes(2400), {"simulated": False}, mode="tvla") as w:
                for i in range(n_traces):
                    cls = i % 2
                    tr = rng.normal(0.0, 1.0, n_samples)
                    if cls == 0:
                        tr[spike] += 3.0                # a clear difference at ONE sample
                    w.append(i, tr, bytes(1088), 26367, bytes(32), True, cls=cls)
            full = TA.run_tvla(str(path))
            plotted = TA.chart_data(str(path), max_points=2000)
        self.assertEqual(full["max_abs_t_sample"], spike)
        self.assertGreater(full["max_abs_t"], TA.LEAKAGE_THRESHOLD)
        self.assertTrue(plotted["decimated"])
        self.assertLessEqual(len(plotted["t_curve"]), 2000)
        i = int(np.argmax(np.abs(plotted["t_curve"])))
        self.assertEqual(plotted["sample_indices"][i], spike)          # the plot shows the peak ...
        self.assertAlmostEqual(abs(plotted["t_curve"][i]), plotted["max_abs_t"], places=3)   # ... at full height
        # every series is read at the same indices
        n = len(plotted["sample_indices"])
        self.assertEqual(len(plotted["fixed_mean_curve"]), n)
        self.assertEqual(len(plotted["random_mean_curve"]), n)


if __name__ == "__main__":
    unittest.main()
