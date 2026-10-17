"""Row-aligned CPA: the engine (aligned_cpa.py) and the pipe_* models through cpa_attack.

Positive control, the same idea as tests/test_models.py but one hypothesis per clock cycle:
traces carry the true-key hypothesis of a pipeline model in the cycles the RTL schedule puts
it (``mlkem_pipeline.py``), and the attack with that model must rank the true value first.
The traces are written at one sample per core clock cycle, which is what the features reduce
a real (faster) record to.

    uv run python -m unittest discover -s tests -v
"""

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cpa_toolkit"))

import aligned_cpa as AC  # noqa: E402
import config as C  # noqa: E402
import cpa_attack as CA  # noqa: E402
import mlkem_ref as R  # noqa: E402
import models as LM  # noqa: E402
import testvectors as TV  # noqa: E402
from hdf5_trace_store import TraceWriter  # noqa: E402

#: One sample per core clock cycle: the features then read one cell per sample.
RATE = C.CORE_MHZ * 1e6


def inject(traces, model, guess, value, first_cycle, t0, gain, causal=False):
    """Add the true-key hypothesis of every row of ``model`` to the cycle the schedule gives it.

    The hypothesis is standardized first, so against unit-variance noise ``gain`` *is* the
    correlation a perfect single-row model reaches (and the whole model reaches about
    ``sqrt(rows) * gain``).
    """
    h = model.row_hypotheses(guess, np.array([value]), causal)
    base = t0 + 2 * (guess.j >> 1) - first_cycle
    for k, hk in h.items():
        col = base + k
        if 0 <= col < traces.shape[1]:
            x = hk[:, 0].astype(np.float64)
            sd = x.std()
            if sd > 0:
                traces[:, col] += (gain * (x - x.mean()) / sd).astype(np.float32)
    return sorted(h)


class TestAlignedEngine(unittest.TestCase):
    """aligned_cpa.attack_coefficient on per-cycle features built directly (no file, no scope)."""

    def setUp(self):
        rng = np.random.default_rng(7)
        self.model = LM.get("pipe_output")
        self.poly, self.j, self.n = 1, 61, 400
        self.u = rng.integers(0, R.Q, (self.n, R.K, 256))
        self.s = rng.integers(0, R.Q, (R.K, 256))
        self.guess = LM.Guess(self.u, self.s, self.poly).at(self.j)
        self.cand = np.arange(R.Q, dtype=np.int64)
        self.first, self.ncyc = AC.window(self.poly)
        self.t0 = C.BMUL_READ_CYCLES[self.poly]
        self.rng = rng

    def run_attack(self, Z, offsets=AC.DEFAULT_OFFSETS):
        return AC.attack_coefficient(self.model, self.guess, AC.standardize(Z), self.first, self.t0,
                                     offsets, self.cand, False)

    def noise(self):
        return self.rng.normal(0.0, 1.0, (self.n, self.ncyc)).astype(np.float32)

    def test_true_key_is_found_and_uses_many_rows(self):
        Z = self.noise()
        rows = inject(Z, self.model, self.guess, int(self.s[self.poly, self.j]), self.first, self.t0, 0.12)
        res = self.run_attack(Z)
        self.assertEqual(res["best_guess"], int(self.s[self.poly, self.j]))
        self.assertEqual(res["true_rank"], 1)
        self.assertEqual(res["best_sample"], 0)              # the schedule is where we put it
        self.assertEqual(res["rows"], len(rows))
        self.assertGreater(res["rows"], 5)

    def test_a_shifted_record_is_found_at_its_offset(self):
        for shift in (-4, 3):
            Z = self.noise()
            inject(Z, self.model, self.guess, int(self.s[self.poly, self.j]), self.first - shift, self.t0, 0.15)
            res = self.run_attack(Z)
            self.assertEqual(res["true_rank"], 1, "shift %+d" % shift)
            self.assertEqual(res["best_sample"], shift)

    def test_a_shift_far_outside_the_search_is_not_found(self):
        # the pipeline carries a word's data in delayed copies, so a small misalignment still scores
        # (+-5 rows here); only a shift well beyond the search loses the coefficient
        Z = self.noise()
        inject(Z, self.model, self.guess, int(self.s[self.poly, self.j]), self.first - 9, self.t0, 0.15)
        res = self.run_attack(Z, offsets=(-1, 0, 1))
        self.assertGreater(res["true_rank"], 1)

    def test_noise_alone_gives_no_confident_rank(self):
        res = self.run_attack(self.noise())
        self.assertGreater(res["true_rank"], 50)
        self.assertLess(res["best_corr"], 0.2)

    def test_summing_rows_beats_the_best_single_row(self):
        Z = self.noise()
        rows = inject(Z, self.model, self.guess, int(self.s[self.poly, self.j]), self.first, self.t0, 0.09)
        Zs = AC.standardize(Z)
        full = AC.attack_coefficient(self.model, self.guess, Zs, self.first, self.t0, (0,), self.cand, False)
        # the same traces, scored on one row at a time: the best of them should do worse
        best_single = min(
            AC.attack_coefficient(OneRow(self.model, k), self.guess, Zs, self.first, self.t0, (0,), self.cand,
                                  False)["true_rank"] for k in rows)
        self.assertEqual(full["true_rank"], 1)
        self.assertGreater(best_single, 1)


class OneRow(LM.PipelineModel):
    """Test helper: the same model restricted to a single row, to show what summing the rows buys."""

    def __init__(self, model, row):
        self._m, self._row = model, row
        self.name, self.group = "%s@%d" % (model.name, row), model.group

    def row_hypotheses(self, guess, candidates, causal=False):
        h = self._m.row_hypotheses(guess, candidates, causal)
        return {self._row: h[self._row]} if self._row in h else {}


class TestAlignedCampaign(unittest.TestCase):
    """cpa_attack with a pipe_* model on an HDF5 campaign (the path the GUI and CLI take)."""

    POLY, COEFF = 2, 9          # word 4: far enough in that every transition of the schedule exists

    @classmethod
    def setUpClass(cls):
        rng = np.random.default_rng(42)
        n = 400
        vs = TV.generate("cpa", n, bytes(range(64)), "valid")
        cls.dk = vs.dk
        model = LM.get("pipe_all")
        s_hat, u_hat = R.s_hat_from_dk(vs.dk), R.u_hat_from_ct(vs.ct)
        first, ncyc = AC.window(R.K - 1)                       # the record must reach the last BMUL
        n_samples = first + ncyc + 8
        traces = rng.normal(0.0, 1.0, (n, n_samples)).astype(np.float32)
        guess = LM.Guess(u_hat, s_hat, cls.POLY).at(cls.COEFF)
        inject(traces, model, guess, int(s_hat[cls.POLY, cls.COEFF]), 0, C.BMUL_READ_CYCLES[cls.POLY], 0.25)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = str(Path(cls.tmp.name) / "cpa.hdf5")
        meta = {"mode": "cpa", "strategy": "valid", "op": "decaps", "model": "synthetic positive control",
                "n_traces": n, "core_mhz": C.CORE_MHZ, "bmul_windows_cycles": "null", "scope": "{}",
                "board": "{}", "simulated": False, "created_unix": 0.0, "recipe": "{}", "vector_set": "test"}
        with TraceWriter(cls.path, n, n_samples, vs.dk, meta, mode="cpa", items=vs.campaign_items(),
                         expected=vs.k) as w:
            for i in range(n):
                w.append(i, traces[i], vs.ct[i].tobytes(), 26367, vs.k[i].tobytes(), ok=True)
            w.set_sample_rate(RATE)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_scored_run_ranks_the_injected_coefficient_first(self):
        summary = CA.run_attack(self.path, LM.get("pipe_all"), "", 0, None, self.dk, coeffs=self.COEFF + 1)
        self.assertTrue(summary["oracle_assisted"])
        self.assertEqual(summary["coefficients_per_poly"], self.COEFF + 1)
        row = next(r for r in summary["results"] if r["poly"] == self.POLY and r["coeff"] == self.COEFF)
        self.assertEqual(row["true_rank"], 1)
        self.assertEqual(row["best_guess"], row["true_value"])
        f = summary["features"]
        self.assertEqual(f["mode"], "pca")
        self.assertEqual(f["offsets_searched"], list(AC.DEFAULT_OFFSETS))
        self.assertEqual(f["offset_histogram"].get(0, 0) >= 1, True)   # the injected offset was searched and used

    def test_blind_run_needs_no_key_and_reports_no_ranks(self):
        summary = CA.run_attack(self.path, LM.get("pipe_output"), "", 0, None, None, coeffs=2)
        self.assertFalse(summary["oracle_assisted"])
        self.assertNotIn("rank1_fraction", summary)
        self.assertEqual(len(summary["results"]), R.K * 2)
        self.assertTrue(all(r["true_rank"] is None for r in summary["results"]))

    def test_feature_modes_and_a_fixed_offset(self):
        for mode in ("energy", "abs"):
            s = CA.run_attack(self.path, LM.get("pipe_all"), "", 0, None, self.dk, features=mode,
                              offsets="0", coeffs=2)
            self.assertEqual(s["features"]["mode"], mode)
            self.assertEqual(s["features"]["offsets_searched"], [0])

    def test_coefficient_inspector_curve_is_per_row(self):
        out = CA.correlation_curve_for_coefficient(self.path, LM.get("pipe_all"), "", self.POLY, self.COEFF,
                                                   0, self.dk)
        self.assertEqual(out["unit"], "row")
        self.assertEqual(out["true_rank"], 1)
        self.assertEqual(len(out["best_curve"]), len(out["rows"]))
        self.assertEqual(len(out["true_curve"]), len(out["rows"]))
        self.assertGreater(max(abs(v) for v in out["true_curve"]), 0.1)

    def test_a_register_model_still_runs_on_the_same_file(self):
        summary = CA.run_attack(self.path, LM.get("hw_scalar"), "", 0, None, None, coeffs=1)
        self.assertNotIn("features", summary)
        self.assertEqual(len(summary["results"]), R.K)

    def test_an_aligned_model_needs_the_sample_rate(self):
        import h5py
        other = str(Path(self.tmp.name) / "norate.hdf5")
        with h5py.File(self.path, "r") as src, h5py.File(other, "w") as dst:
            for k in src:
                src.copy(k, dst)
            del dst["metadata"].attrs["sample_rate"]
        with self.assertRaises(SystemExit) as e:
            CA.run_attack(other, LM.get("pipe_all"), "", 0, None, None, coeffs=1)
        self.assertIn("sample rate", str(e.exception))


class TestOffsetParsing(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(CA.parse_offsets("-3:3"), (-3, -2, -1, 0, 1, 2, 3))
        self.assertEqual(CA.parse_offsets("2"), (2,))
        self.assertEqual(CA.parse_offsets("-2,0,2"), (-2, 0, 2))
        self.assertEqual(CA.parse_offsets([1, 2]), (1, 2))
        self.assertEqual(CA.parse_offsets(""), AC.DEFAULT_OFFSETS)

    def test_bad_range(self):
        with self.assertRaises(ValueError):
            CA.parse_offsets("3:-3")

    def test_feature_mode_rejects_a_raw_request(self):
        with self.assertRaises(SystemExit):
            CA.feature_mode(LM.get("pipe_all"), "raw")
        self.assertEqual(CA.feature_mode(LM.get("pipe_all"), "auto"), "pca")


if __name__ == "__main__":
    unittest.main()
