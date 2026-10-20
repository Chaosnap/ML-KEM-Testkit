"""cpa_toolkit/cpa_attack.py: the simple coefficient CPA on synthetic campaigns.

A campaign is built from a real vector set (real ciphertexts, real key) with noise traces that
carry the hypothesised register value at the cycle the RTL schedule puts it (``T + 10 / T + 11``
for the reduced product, ``T + 5 / T + 6`` for the raw product, ``T = BMUL_READ_CYCLES + 2 w``),
at 2 samples per core cycle and a record that starts 1 us BEFORE the trigger -- so a wrong mapping
of core cycles to samples (the record start, the slack in cycles) moves the leakage out of the
searched range and the tests fail. The positive control must recover the key; a noise-only campaign
and a wrong key must not.

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

import acquire_cpa as A  # noqa: E402
import config as C  # noqa: E402
import cpa_attack as CA  # noqa: E402
import hdf5_trace_store as HS  # noqa: E402
import mlkem_ref as R  # noqa: E402
import testvectors as TV  # noqa: E402

RATE = 2 * C.CORE_MHZ * 1e6            # 2 samples per core clock cycle
START = -1e-6                          # the record starts 1 us before the trigger
N_SAMPLES = 4200                       # ~ -1 us .. 9.4 us: holds BMUL 0 (cycles 1249 .. 1610) only
N = 1500
POLY, NCOEF = 0, 8                     # leakage injected for coefficients 0 .. 7 of polynomial 0


def cycle_sample(c):
    return int(round((c / (C.CORE_MHZ * 1e6) - START) * RATE))


def campaign(tmp, leak="mod", gain=1.0, noise=4.0, seed=5, write_start=True):
    vs = TV.generate("cpa", N, bytes(range(64)), "valid")
    u, s = R.u_hat_from_ct(vs.ct), R.s_hat_from_dk(vs.dk)
    rng = np.random.default_rng(seed)
    traces = rng.normal(0, noise, (N, N_SAMPLES)).astype(np.float32)
    if leak:
        model = CA.MODELS["hw_product_" + leak]
        for j in range(NCOEF):
            c0 = C.BMUL_READ_CYCLES[POLY] + 2 * (j >> 1)
            h = model.hypotheses(u[:, POLY, j], np.array([s[POLY, j]]))[:, 0]
            for cyc in model.cycles:
                traces[:, cycle_sample(c0 + cyc)] += gain * h
    path = Path(tmp) / "c.hdf5"
    w = HS.TraceWriter(path, N, N_SAMPLES, vs.dk, {"bmul_windows_cycles": "[]"}, items=vs.campaign_items(),
                       expected=vs.k)
    for i in range(N):
        w.append(i, traces[i], vs.ct[i].tobytes(), 100, vs.k[i].tobytes(), ok=True)
    w.set_sample_rate(RATE, START if write_start else None)
    w.close()
    return str(path), vs.dk, s


class TestCpa(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.path, cls.dk, cls.s = campaign(cls.tmp, "mod")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def test_blind_attack_recovers_the_key_at_the_predicted_cycle(self):
        r = CA.run_attack(self.path, "hw_product_mod", 0, None, "0", "4")
        self.assertFalse(r["oracle_assisted"])
        for res in r["results"]:
            self.assertEqual(res["best_guess"], int(self.s[0, res["coeff"]]), res)
            self.assertEqual(res["votes_best"], 4)
            self.assertGreater(res["best_corr"], r["noise_floor_corr"])
            self.assertTrue(-0.6 <= res["offset_cycles"] <= 1.6, res["offset_cycles"])   # T+10 / T+11 (+- half a sample)
        self.assertAlmostEqual(r["alignment"]["median_offset_cycles"], 0.5, delta=1.0)

    def test_scoring_ranks_the_true_key_but_does_not_change_the_attack(self):
        blind = CA.run_attack(self.path, "hw_product_mod", 0, None, "0", "3")
        scored = CA.run_attack(self.path, "hw_product_mod", 0, self.dk, "0", "3")
        self.assertTrue(scored["oracle_assisted"])
        self.assertEqual([x["best_guess"] for x in blind["results"]], [x["best_guess"] for x in scored["results"]])
        self.assertEqual([x["best_corr"] for x in blind["results"]], [x["best_corr"] for x in scored["results"]])
        self.assertEqual(scored["rank1_fraction"], 1.0)
        self.assertTrue(all(x["true_rank"] == 1 for x in scored["results"]))

    def test_wrong_key_is_refused(self):
        with self.assertRaises(SystemExit):
            CA.run_attack(self.path, "hw_product_mod", 0, bytes(2400), "0", "1")

    def test_gate_search_finds_it_with_far_fewer_samples(self):
        full = CA.run_attack(self.path, "hw_product_mod", 0, None, "0", "2")
        gate = CA.run_attack(self.path, "hw_product_mod", 0, None, "0", "2", "gate", 8)
        for f, g in zip(full["results"], gate["results"]):
            self.assertEqual(g["best_guess"], f["best_guess"])
            self.assertLess(g["n_search_samples"], f["n_search_samples"] / 5)

    def test_a_wrong_delay_moves_the_gate_off_the_leakage(self):
        r = CA.run_attack(self.path, "hw_product_mod", 0, self.dk, "0", "2", "gate", 4, delay=60)
        self.assertTrue(all(x["true_rank"] > 1 for x in r["results"]))

    def test_the_raw_model_does_not_find_a_reduced_product_leak(self):
        # same record, other register: not the key at the other register's cycle (nothing leaks there)
        r = CA.run_attack(self.path, "hw_product_raw", 0, self.dk, "0", "2", "gate", 2)
        self.assertTrue(all(x["true_rank"] > 1 for x in r["results"]))

    def test_raw_model_positive_control(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, dk, s = campaign(tmp, "raw")
            r = CA.run_attack(path, "hw_product_raw", 0, dk, "0", "3")
            self.assertTrue(all(x["true_rank"] == 1 for x in r["results"]))

    def test_noise_only_campaign_gives_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, dk, s = campaign(tmp, None, seed=9)
            r = CA.run_attack(path, "hw_product_mod", 0, dk, "0", "4")
            self.assertGreater(min(x["true_rank"] for x in r["results"]), 20)
            self.assertLess(r["mean_best_corr"], 1.35 * r["noise_floor_corr"])
            self.assertLess(r["n_above_floor"], len(r["results"]))

    def test_without_the_recorded_record_start_the_mapping_is_wrong_and_shows(self):
        # an old campaign (no record_start) is read as "sample 0 = trigger" although the record starts 1 us
        # (206 cycles) earlier: a gate around the prediction misses the leakage, and a full-window search
        # finds the key but reports every peak 206 cycles late -- the alignment line makes the error visible
        with tempfile.TemporaryDirectory() as tmp:
            path, dk, s = campaign(tmp, "mod", write_start=False)
            gate = CA.run_attack(path, "hw_product_mod", 0, dk, "0", "3", "gate", 8)
            self.assertTrue(all(x["true_rank"] > 1 for x in gate["results"]))
            full = CA.run_attack(path, "hw_product_mod", 0, dk, "0", "3")
            self.assertAlmostEqual(full["alignment"]["median_offset_cycles"], 206.25, delta=2.0)

    def test_inspector_data(self):
        d = CA.coefficient_detail(self.path, "hw_product_mod", 0, 2, 0, self.dk)
        self.assertEqual(d["best_guess"], int(self.s[0, 2]))
        self.assertEqual(d["true_rank"], 1)
        self.assertEqual(len(d["peak_per_guess"]), R.Q)
        self.assertEqual(len(d["curve"]), len(d["x"]))
        self.assertEqual(d["heat"]["guesses"][0], int(self.s[0, 2]))
        conv = d["convergence"]
        self.assertEqual(conv["reference"], "true key")
        self.assertEqual(conv["points"][-1]["rank"], 1)
        self.assertGreater(conv["points"][-1]["ref"], conv["points"][-1]["best_other"])
        self.assertLessEqual(d["predicted_us"][0], d["x"][d["curve"].index(max(d["curve"], key=abs))] + 0.01)

    def test_ranges(self):
        self.assertEqual(CA.parse_range("0-2", 3), [0, 1, 2])
        self.assertEqual(CA.parse_range("8", 256), list(range(8)))
        self.assertEqual(CA.parse_range("0", 3), [0, 1, 2])
        self.assertEqual(CA.parse_range("1,5", 256), [1, 5])
        self.assertEqual(CA.parse_polys("0"), [0])
        self.assertEqual(CA.parse_polys("1,2"), [1, 2])
        self.assertEqual(CA.parse_polys(""), [0, 1, 2])
        with self.assertRaises(ValueError):
            CA.parse_polys("3")
        with self.assertRaises(ValueError):
            CA.parse_range("0-3", 3)

    def test_models_listed(self):
        self.assertEqual({m["name"] for m in CA.list_models()}, {"hw_product_mod", "hw_product_raw"})


class TestWindows(unittest.TestCase):
    def test_windows_follow_the_record_start(self):
        w0 = A.window_sample_ranges(RATE, N_SAMPLES, START)[0]
        self.assertAlmostEqual(w0[0], cycle_sample(C.BMUL_WINDOWS[0][0] - C.TRIGGER_SLACK_CYCLES), delta=1)
        self.assertAlmostEqual(w0[1], cycle_sample(C.BMUL_WINDOWS[0][1] + C.TRIGGER_SLACK_CYCLES), delta=1)
        # the slack is in core cycles: 40 cycles = 80 samples at 2 samples per cycle (not 40 samples)
        w1 = A.window_sample_ranges(RATE, 10 ** 6, 0.0)[0]
        self.assertAlmostEqual(w1[1] - w1[0], 2 * (C.BMUL_WINDOWS[0][1] - C.BMUL_WINDOWS[0][0] + 2 * C.TRIGGER_SLACK_CYCLES), delta=2)

    def test_a_window_past_the_record_is_empty_not_inverted(self):
        for a, b in A.window_sample_ranges(RATE, 1000, START):
            self.assertLessEqual(a, b)


class TestGuiServer(unittest.TestCase):
    def test_axis_follows_the_record_start_and_keeps_bmul_numbers(self):
        import web_gui as W
        full = W._axis(RATE, 20000, START)
        self.assertEqual(full["bmul_index"], [0, 1, 2])
        self.assertAlmostEqual(full["bmul_windows"][0][0], (C.BMUL_WINDOWS[0][0] - C.TRIGGER_SLACK_CYCLES) / C.CORE_MHZ, delta=0.01)
        # a record that starts after BMUL 0: BMUL 1 and 2 must keep their own numbers, not become 0 and 1
        late = W._axis(RATE, 20000, 8e-6)
        self.assertEqual(late["bmul_index"], [1, 2])
        self.assertEqual(late["bmul_outside"], 1)
        # a record that ends in the middle of BMUL 1: it is reported as cut
        cut = W._axis(RATE, int((9.5e-6 - START) * RATE), START)
        self.assertGreaterEqual(cut["bmul_outside"] + cut["bmul_cut"], 1)
        self.assertEqual(W._axis(RATE, 20000, None)["record_start_known"], False)

    def test_series_x_is_time_after_the_trigger(self):
        import web_gui as W
        x = W._series(np.zeros(10), RATE, 100, START)["x"]
        self.assertAlmostEqual(x[0], START * 1e6, places=3)
        self.assertAlmostEqual(x[1] - x[0], 1e6 / RATE, places=3)

    def test_settings_are_checked_before_the_job_starts(self):
        import web_gui as W
        st = W._cpa_settings({"model": "hw_product_raw", "coeffs": "0-3", "polys": "1,2", "search": "gate", "margin": "6"})
        self.assertEqual((st["model"], st["search"], st["margin"]), ("hw_product_raw", "gate", 6))
        for bad in ({"model": "nope"}, {"search": "x"}, {"polys": "3"}, {"coeffs": "0-300"}):
            with self.assertRaises(ValueError):
                W._cpa_settings(bad)

    def test_old_format_reports_are_listed_but_not_compared(self):
        import web_gui as W
        old = W._cpa_summary({"kind": "cpa", "model": "hd_basemul_word", "results": []})
        self.assertTrue(old["legacy"])
        new = W._cpa_summary({"kind": "cpa", "format": W.CPA_FORMAT, "model": "hw_product_mod", "results": [], "n_traces_used": 5})
        self.assertFalse(new["legacy"])
        self.assertEqual(W.api_model_list({})["default_model"], C.CPA_MODEL)


if __name__ == "__main__":
    unittest.main()
