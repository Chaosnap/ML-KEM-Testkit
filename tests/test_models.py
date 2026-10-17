"""Leakage models in cpa_toolkit/models/: registry, self-check (with negative
controls), a positive control through the attack, and the GUI endpoints.

    uv run python -m unittest discover -s tests -v
"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cpa_toolkit"))

import config as C  # noqa: E402
import cpa_attack as CA  # noqa: E402
import hdf5_trace_store as HS  # noqa: E402
import mlkem_ref as R  # noqa: E402
import models as LM  # noqa: E402
import testvectors as TV  # noqa: E402
import web_gui as W  # noqa: E402

#: The register models (one register + R_prev, correlated with every sample of the window).
MODELS = ["hd_basemul_c0", "hd_basemul_c1", "hd_basemul_word", "hd_scalar", "hw_scalar"]
#: The schedule-aware pipeline models (one hypothesis per clock cycle) -- tests/test_aligned_attack.py.
PIPE_MODELS = ["pipe_all", "pipe_barrett", "pipe_dsp", "pipe_output"]
#: Every model file, as models.names() sorts them.
ALL_MODELS = sorted(MODELS + PIPE_MODELS)


class TestRegistry(unittest.TestCase):
    def test_names_and_aliases(self):
        self.assertEqual(LM.names(), ALL_MODELS)
        self.assertEqual(LM.get("basemul-word").name, "hd_basemul_word")      # pre-models/ preset names
        self.assertEqual(LM.resolve("HW"), "hw_scalar")
        self.assertEqual(LM.resolve("custom", "c1"), "hd_basemul_c1")          # old "custom" reports
        self.assertEqual(LM.resolve("nope"), "")
        with self.assertRaises(ValueError):
            LM.get("nope")

    def test_rprev(self):
        self.assertEqual(LM.get("hd_basemul_word").resolve_rprev(""), "prev-word")
        self.assertEqual(LM.get("hw_scalar").resolve_rprev("zero"), "")          # takes no R_prev
        with self.assertRaises(ValueError):
            LM.get("hd_scalar").resolve_rprev("acc")

    def test_new_file_shows_up(self):
        path = ROOT / "cpa_toolkit" / "models" / "zz_test_tmp.py"
        path.write_text((ROOT / "cpa_toolkit" / "models" / "_template.py").read_text())
        try:
            found, errors = LM.scan()
            self.assertIn("zz_test_tmp", [m.name for m in found])
            self.assertEqual(errors, [])
            self.assertTrue(all(ok for _, ok, _ in LM.check(LM.get("zz_test_tmp"))))
            path.write_text("this is not python")
            found, errors = LM.scan()                                            # a broken file is reported,
            self.assertEqual([e["file"] for e in errors], ["models/zz_test_tmp.py"])  # the others still load
            self.assertEqual([m.name for m in found], ALL_MODELS)
        finally:
            path.unlink()


class TestCheck(unittest.TestCase):
    def test_every_model_passes(self):
        for name in MODELS:
            for check, ok, detail in LM.check(LM.get(name)):
                self.assertTrue(ok, "%s: %s -- %s" % (name, check, detail))

    def test_wrong_register_is_caught(self):
        class Wrong(type(LM.get("hd_basemul_word"))):
            def hypotheses(self, guess, rprev, candidates, rows=slice(None)):   # c0 instead of the word
                x = guess.register("c0", candidates, rows)
                return np.asarray(self.leak(x, guess.previous("word", rprev, rows)[:, None]), dtype=np.float32)
        rows = LM.check(Wrong())
        self.assertFalse(all(ok for name, ok, _ in rows if name.startswith("register values")))

    def test_guess_independent_model_is_caught(self):
        class Flat(type(LM.get("hw_scalar"))):
            def leak(self, x, r_prev):
                return np.zeros_like(x)
        self.assertEqual(LM.check(Flat())[-1][:2], ("depends on the guess", False))


class TestAttack(unittest.TestCase):
    """Positive control: traces leak one model's hypothesis of the true key at one
    coefficient; the attack with that model ranks the true value first."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def campaign(self, model, rprev, poly, coeff):
        vs = TV.generate("cpa", 300, bytes(range(64)), "valid")
        u, s = R.u_hat_from_ct(vs.ct), R.s_hat_from_dk(vs.dk)
        g = LM.Guess(u, s, poly).at(coeff)
        leak = LM.get(model).hypotheses(g, rprev, np.array([s[poly, coeff]]))[:, 0]
        rng = np.random.default_rng(3)
        traces = rng.normal(0, 1, (vs.n, 40)).astype(np.float32)
        traces[:, 20] += leak                                        # one sample carries the leakage
        path = self.tmp / "c.hdf5"
        md = {"bmul_windows_cycles": json.dumps([[0, 0], [0, 0], [0, 0]])}
        w = HS.TraceWriter(path, vs.n, 40, vs.dk, md, items=vs.campaign_items(), expected=vs.k)
        for i in range(vs.n):
            w.append(i, traces[i], vs.ct[i].tobytes(), 100, vs.k[i].tobytes(), ok=True)
        w.set_sample_rate(C.CORE_MHZ * 1e6)
        w.close()
        return str(path), vs.dk

    def test_matching_model_recovers(self):
        for model, rprev in (("hd_basemul_word", "prev-word"), ("hw_scalar", "")):
            path, dk = self.campaign(model, rprev, 1, 9)
            r = CA.correlation_curve_for_coefficient(path, model, rprev, 1, 9, 0, dk)
            self.assertEqual((r["model"], r["true_rank"]), (model, 1), model)
            self.assertEqual(r["best_guess"], r["true_value"])


class TestGuiEndpoints(unittest.TestCase):
    def test_list_and_selftest(self):
        r = W.api_model_list({})
        self.assertEqual([m["name"] for m in r["models"]], ALL_MODELS)
        self.assertEqual(r["errors"], [])
        self.assertEqual(r["default_model"], LM.resolve(C.CPA_MODEL))
        word = next(m for m in r["models"] if m["name"] == "hd_basemul_word")
        self.assertEqual((word["register"], word["bits"], [x["name"] for x in word["rprevs"]]),
                         ("word", 24, ["prev-word", "zero", "acc"]))
        self.assertTrue(W.api_model_selftest({"model": "hd_scalar"})["ok"])

    def test_model_of_requests_and_reports(self):
        self.assertEqual(W._model_of({"model": "hd_scalar", "rprev": "zero"}), ("hd_scalar", "zero"))
        self.assertEqual(W._model_of({"model": "basemul-c0"}), ("hd_basemul_c0", "prev-word"))
        self.assertEqual(W._model_of({"target": "word", "rprev": "acc"}), ("hd_basemul_word", "acc"))
        with self.assertRaises(ValueError):
            W._model_of({"model": "hd_scalar", "rprev": "acc"})
        old = {"model_config": {"preset": "HW", "target": "p_self", "rprev": "zero"}}
        self.assertEqual(W._report_model(old), ("hw_scalar", ""))
        old = {"model_config": {"preset": "custom", "target": "c1", "rprev": "acc"}}
        self.assertEqual(W._report_model(old), ("hd_basemul_c1", "acc"))

    def test_save_default(self):
        saved = {}
        orig, keep = (W.save_config_values, W.log), (C.CPA_MODEL, C.CPA_RPREV)
        W.save_config_values, W.log = (lambda **kv: saved.update(kv)), (lambda *a: None)
        try:                                                     # config.py itself is never written
            W.api_model_save_default({"model": "hd_scalar", "rprev": "zero"})
            self.assertEqual(saved, {"CPA_MODEL": "hd_scalar", "CPA_RPREV": "zero"})
        finally:
            W.save_config_values, W.log = orig
            C.CPA_MODEL, C.CPA_RPREV = keep


if __name__ == "__main__":
    unittest.main()
