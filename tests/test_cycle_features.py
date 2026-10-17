"""Per-clock-cycle trace features on a synthetic EM-like signal.

Every core cycle contributes a signed, ringing burst at its clock edge whose amplitude
follows a per-cycle activity count (what the probe sees of the switching flops); the
sample grid (2.5 GS/s against a 206.25 MHz clock: 12.12 samples per cycle) is not locked to
the clock. The features must bring the activity back out better than the best raw sample.

    uv run python -m unittest discover -s tests -v
"""

import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cpa_toolkit"))

import config as C  # noqa: E402
import cycle_features as CF  # noqa: E402

RATE = 2.5e9
TC = RATE / (C.CORE_MHZ * 1e6)


def synth(n=500, cycles=140, edge0=4.37, sign=1.0, noise=3.0, seed=1):
    """``(traces, activity)``: ``activity[i, c]`` toggles in cycle ``c`` of trace ``i``."""
    rng = np.random.default_rng(seed)
    act = rng.binomial(60, 0.5, (n, cycles)).astype(np.float64)
    length = int((cycles + 3) * TC)
    x = rng.normal(0.0, noise, (n, length))
    k = np.arange(24)
    for c in range(cycles):
        edge = edge0 + c * TC                                   # fractional sample position of this edge
        s = int(np.ceil(edge)) + k
        tau = (s - edge) / RATE
        burst = sign * np.exp(-tau / 1.8e-9) * np.sin(2 * np.pi * tau / 3.0e-9)   # signed ringing, ~8 samples long
        x[:, s] += act[:, c:c + 1] * burst[None, :]
    return x.astype(np.float32), act


def best_raw_corr(x, act, c):
    """Best |correlation| of any single raw sample of cycle ``c`` with that cycle's activity."""
    lo = int(c * TC) - 2
    return max(abs(np.corrcoef(x[:, s], act[:, c])[0, 1]) for s in range(lo, lo + int(TC) + 5))


def feat_corr(f, act, c):
    """Best |correlation| of the feature column at c-1 / c / c+1 (the cell may straddle the burst)."""
    return max(abs(np.corrcoef(f[:, cc], act[:, c])[0, 1]) for cc in (c - 1, c, c + 1))


class TestCycleFeatures(unittest.TestCase):
    def test_phase_puts_each_burst_inside_one_cell(self):
        for edge0 in (0.9, 4.37, 9.6, 11.8):
            x, _ = synth(edge0=edge0)
            phase = CF.estimate_phase(x, RATE, 10, 100)
            self.assertGreaterEqual(phase, 0.0)
            self.assertLess(phase, TC)
            # a burst starts at its clock edge and lasts about 8 samples: the cell must start at the
            # edge or a little before it (never inside the burst)
            ahead = (edge0 - phase) % TC                 # samples from the cell start to the edge
            self.assertTrue(ahead < 3.0 or ahead > TC - 0.5, "edge %.2f, cell starts at %.2f" % (edge0, phase))

    def test_pca_beats_the_best_raw_sample(self):
        x, act = synth()
        f, info = CF.features(x, RATE, 0, 120, mode="pca")
        self.assertEqual(f.shape, (500, 120))
        cs = range(20, 100, 7)
        raw = np.mean([best_raw_corr(x, act, c) for c in cs])
        pca = np.mean([feat_corr(f, act, c) for c in cs])
        self.assertGreater(pca, raw + 0.05)                     # better than picking the best of the ~16 samples
        self.assertGreater(pca, 0.75)
        self.assertGreater(info["explained"], 2.0 / CF.POINTS_PER_CYCLE)   # well above isotropic noise (1/12)
        self.assertAlmostEqual(float(np.linalg.norm(info["shape"])), 1.0, places=4)

    def test_pca_orientation_makes_activity_positive(self):
        for sign in (1.0, -1.0):                               # a probe may see the burst either way up
            x, act = synth(sign=sign)
            f, _ = CF.features(x, RATE, 0, 120, mode="pca")
            r = [np.corrcoef(f[:, c], act[:, c])[0, 1] for c in range(20, 100, 7)]
            best = [max(rr, np.corrcoef(f[:, c + 1], act[:, c])[0, 1], np.corrcoef(f[:, c - 1], act[:, c])[0, 1])
                    for rr, c in zip(r, range(20, 100, 7))]
            self.assertGreater(np.mean(best), 0.5, "sign %+g" % sign)

    def test_energy_and_abs_need_no_shape_but_track_activity(self):
        x, act = synth(noise=2.0)
        for mode in ("energy", "abs"):
            f, info = CF.features(x, RATE, 0, 120, mode=mode)
            self.assertNotIn("shape", info)
            self.assertGreater(np.mean([feat_corr(f, act, c) for c in range(20, 100, 7)]), 0.4, mode)

    def test_unknown_mode_and_short_window_are_errors(self):
        x, _ = synth(n=40, cycles=30)
        with self.assertRaises(ValueError):
            CF.features(x, RATE, 0, 20, mode="rms")
        with self.assertRaises(ValueError):
            CF.estimate_phase(x, RATE, 0, 2)

    def test_cycle_vectors_pad_outside_the_record(self):
        x, _ = synth(n=10, cycles=30)
        v = CF.cycle_vectors(x, RATE, 25, 20, 0.0)               # runs past the end of the record
        self.assertEqual(v.shape, (10, 20, CF.POINTS_PER_CYCLE))
        self.assertTrue(np.all(v[:, -1] == 0.0))


if __name__ == "__main__":
    unittest.main()
