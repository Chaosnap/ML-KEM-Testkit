"""The Decaps timeline in config.py (measured in the RTL simulation by
scripts/sim/bmul_timing.py) and how the GUI maps it onto a captured record.

    uv run python -m unittest discover -s tests -v
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cpa_toolkit"))

import config as C  # noqa: E402
import web_gui as W  # noqa: E402


class TestConfigTimeline(unittest.TestCase):
    def test_phases_tile_the_operation(self):
        ph = C.DECAPS_PHASES
        self.assertEqual(ph[0][1], 0)
        for (_, _, end), (_, start, _) in zip(ph, ph[1:]):
            self.assertEqual(end, start)           # no gap, no overlap
        for _, a, b in ph:
            self.assertLess(a, b)

    def test_decryption_ntts_bmuls_and_intt_are_in_order_inside_the_first_phase(self):
        dec_end = C.DECAPS_PHASES[0][2]
        seq = []
        for ntt, bmul in zip(C.NTT_WINDOWS_CYCLES, C.BMUL_WINDOWS):
            seq += [ntt, bmul]                      # NTT(u_hat_i) feeds BMUL i
        seq.append(C.INTT_WINDOW_CYCLES)
        for a, b in seq:
            self.assertLess(a, b)
        for (_, prev_end), (nxt_start, _) in zip(seq, seq[1:]):
            self.assertLessEqual(prev_end, nxt_start + 6)   # back to back, within the BMUL write-back tail
        self.assertLessEqual(C.INTT_WINDOW_CYCLES[1], dec_end)

    #: Cycles in which the BMUL datapath is active, measured in the RTL simulation (scripts/sim/bmul_timing.py).
    MEASURED_BMUL = [(1289, 1564), (2855, 3130), (4421, 4696)]

    def test_bmul_windows_contain_the_measured_datapath_activity(self):
        for (a, b), (m0, m1) in zip(C.BMUL_WINDOWS, self.MEASURED_BMUL):
            self.assertLessEqual(a, m0)
            self.assertGreaterEqual(b, m1)
            self.assertLessEqual(b - a, (m1 - m0) + 10)      # not much wider than the activity itself


def parsed(rate):
    """A parse with the Decaps start at sample 0, nominal clock (what trace_parse gives when the
    trigger point is the first sample)."""
    spc = rate / (C.CORE_MHZ * 1e6) if rate else 1.0
    return {"rise_median": 0, "samples_per_cycle": spc, "spc_source": "nominal", "clock_mhz": C.CORE_MHZ}


class TestAxisPhases(unittest.TestCase):
    RATE = 2.5e9

    def test_no_phases_without_a_parse(self):
        self.assertEqual(W._axis(self.RATE, 125000)["phases"], [])

    def test_phases_follow_the_record(self):
        n = int(50e-6 * self.RATE)                  # a 50 us record, as on the bench
        axis = W._axis(self.RATE, n, None, parsed(self.RATE))
        labels = [p["label"] for p in axis["phases"]]
        self.assertIn("J(z‖c)", labels)
        for p in axis["phases"]:
            self.assertLessEqual(p["samples"][1], n)         # clipped to the record
            self.assertLess(p["samples"][0], p["samples"][1])
        # the last phase of the record ends where the record does, later phases are not drawn
        self.assertEqual(axis["phases"][-1]["samples"][1], n)
        self.assertNotIn("compare/select", labels)

    def test_phase_boundaries_are_in_microseconds_on_a_timed_axis(self):
        axis = W._axis(self.RATE, int(200e-6 * self.RATE), None, parsed(self.RATE))
        j = next(p for p in axis["phases"] if p["label"].startswith("J("))
        self.assertAlmostEqual(j["x"][0], 6623 / C.CORE_MHZ, places=2)
        self.assertAlmostEqual(j["x"][1], 7375 / C.CORE_MHZ, places=2)

    def test_untimed_axis_uses_samples_at_one_per_core_cycle(self):
        axis = W._axis(0.0, 30000, None, parsed(0.0))
        self.assertEqual(axis["unit"], "sample")
        j = next(p for p in axis["phases"] if p["label"].startswith("J("))
        self.assertEqual(j["x"], [6623, 7375])

    def test_the_tvla_peak_of_the_bench_run_falls_in_the_ciphertext_hash(self):
        # sample 83485 at 2.5 GS/s: the TVLA peak of the 2026-10-17 bench campaign
        axis = W._axis(self.RATE, 125002, None, parsed(self.RATE))
        s = 83485
        hit = [p["label"] for p in axis["phases"] if p["samples"][0] <= s <= p["samples"][1]]
        self.assertEqual(hit, ["J(z‖c)"])


if __name__ == "__main__":
    unittest.main()
