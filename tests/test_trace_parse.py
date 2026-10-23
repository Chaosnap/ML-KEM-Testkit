"""cpa_toolkit/trace_parse.py: finding the Decaps start of every trace in the trig_out marker.

The CPA test campaign is jittered: the Decaps starts up to +-40 samples off the trigger in every
trace, as with trigger jitter or a moving record start. Mapped from the trigger, the leak is smeared
over 80 samples and the key is not found; parsed from the marker, every trace is cut at its own
start and the key is found.

    uv run python -m unittest tests.test_trace_parse -v
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cpa_toolkit"))

import config as C  # noqa: E402
import cpa as CA  # noqa: E402
import hdf5_trace_store as HS  # noqa: E402
import mlkem_ref as R  # noqa: E402
import testvectors as TV  # noqa: E402
import trace_parse as P  # noqa: E402

RATE = 2 * C.CORE_MHZ * 1e6      # 2 samples per core cycle
N, N_SAMPLES, PRE = 1200, 3600, 200
JITTER = 40


def pulse(n_samples, rise, fall=None, low=0.0, high=2.5, noise=0.02, rng=None):
    rng = rng or np.random.default_rng(0)
    m = np.full(n_samples, low) + rng.normal(0, noise, n_samples)
    m[rise:fall if fall is not None else n_samples] += high - low
    return m


class TestOneMarker(unittest.TestCase):
    def test_rise_and_fall(self):
        p = P.parse_marker(pulse(2000, 300, 1500))
        self.assertTrue(p["ok"])
        self.assertLessEqual(abs(p["rise"] - 300), 1)
        self.assertLessEqual(abs(p["fall"] - 1500), 1)

    def test_pulse_longer_than_the_record(self):
        p = P.parse_marker(pulse(2000, 300))
        self.assertEqual((p["ok"], p["fall"]), (True, -1))

    def test_record_that_starts_after_the_decaps_start_is_refused(self):
        p = P.parse_marker(pulse(2000, 0, 1200))
        self.assertFalse(p["ok"])
        self.assertIn("high at sample 0", p["reason"])

    def test_noise_and_flat_markers_are_refused(self):
        rng = np.random.default_rng(1)
        for m in (rng.normal(0, 1, 5000), np.zeros(500), pulse(2000, 0)):
            p = P.parse_marker(m)
            self.assertFalse(p["ok"])
            self.assertIn("does not change", p["reason"])

    def test_a_short_pulse_is_still_found(self):
        p = P.parse_marker(pulse(2000, 1563, 1625))          # a 25 ns pulse in a 0.8 us record
        self.assertTrue(p["ok"])
        self.assertLessEqual(abs(p["rise"] - 1563), 2)


class TestManyMarkers(unittest.TestCase):
    def test_measured_clock_from_the_pulse_width(self):
        cycles = 1000
        spc = 2.0 * 1.01                                     # the real clock is 1 % slower than nominal
        markers = np.stack([pulse(3000, 100 + k, 100 + k + int(round(cycles * spc))) for k in range(20)])
        r = P.parse_markers(markers, np.full(20, cycles), RATE)
        self.assertEqual(r["n_ok"], 20)
        self.assertEqual(r["spc_source"], "measured")
        self.assertAlmostEqual(r["samples_per_cycle"], spc, delta=0.01)
        self.assertAlmostEqual(r["clock_mhz"], C.CORE_MHZ / 1.01, delta=0.5)

    def test_a_wrong_cycle_count_falls_back_to_the_nominal_clock(self):
        markers = np.stack([pulse(3000, 100, 2100) for _ in range(5)])
        r = P.parse_markers(markers, np.full(5, 300), RATE)   # width / cycles = 6.7, far from 2
        self.assertEqual(r["spc_source"], "nominal")

    def test_aligned_window_cuts_every_trace_at_its_own_start(self):
        traces = np.zeros((3, 100))
        rise = np.array([10, 20, 30])
        for i, r in enumerate(rise):
            traces[i, r + 5] = 1.0
        m, rows = P.aligned_window(traces, rise, np.ones(3, bool), 1.0, 0, 10)
        self.assertEqual(rows.tolist(), [0, 1, 2])
        self.assertTrue((m[:, 5] == 1).all() and m.sum() == 3)


def jittered_campaign(tmp, with_marker=True):
    vs = TV.generate("cpa", N, bytes(range(64)), "valid")
    u, s = R.u_hat_from_ct(vs.ct), R.s_hat_from_dk(vs.dk)
    rng = np.random.default_rng(7)
    traces = rng.normal(0, 4.0, (N, N_SAMPLES)).astype(np.float32)
    rises = PRE + rng.integers(-JITTER, JITTER + 1, N)
    model = CA.MODELS["hw_product_mod"]
    for j in range(4):
        c0 = C.BMUL_READ_CYCLES[0] + 2 * (j >> 1)
        h = model.hypotheses(u[:, 0, j], np.array([s[0, j]]))[:, 0]
        for cyc in model.cycles:
            traces[np.arange(N), rises + int(round((c0 + cyc) * 2))] += h
    path = Path(tmp) / "jit.hdf5"
    w = HS.TraceWriter(path, N, N_SAMPLES, vs.dk, {"bmul_windows_cycles": "[]"}, items=vs.campaign_items(),
                       expected=vs.k, marker=with_marker)
    for i in range(N):
        m = (pulse(N_SAMPLES, int(rises[i]), rng=rng) * 1000).astype(np.int16) if with_marker else None
        w.append(i, traces[i], vs.ct[i].tobytes(), 26300, vs.k[i].tobytes(), ok=True, marker=m,
                 parse=P.parse_marker(m) if with_marker else None)
    w.set_sample_rate(RATE, -PRE / RATE)                     # the trigger point is sample PRE
    w.finalize_parse(RATE)
    w.close()
    return str(path), vs.dk, rises


class TestJitteredCampaign(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.path, cls.dk, cls.rises = jittered_campaign(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def test_live_parse_is_stored_with_the_campaign(self):
        p = P.load(self.path)
        self.assertIsNotNone(p)
        self.assertEqual(p["n_ok"], N)
        self.assertTrue(np.all(np.abs(p["rise"] - self.rises) <= 1))
        self.assertTrue(HS.summarize(self.path)["parsed"])

    def test_parsed_traces_give_the_key_and_unparsed_do_not(self):
        parsed = CA.run_cpa(self.path, "hw_product_mod", 0, self.dk, "0", "4")
        self.assertTrue(parsed["parsed"])
        self.assertEqual(parsed["rank1_fraction"], 1.0)
        for res in parsed["results"]:
            self.assertTrue(-0.6 <= res["offset_cycles"] <= 1.6, res["offset_cycles"])
        P.save(self.path, {"rise": np.zeros(N, dtype=np.int64), "fall": np.zeros(N, dtype=np.int64),
                           "ok": np.zeros(N, dtype=bool), "samples_per_cycle": 2.0})   # make it unusable
        import h5py
        with h5py.File(self.path, "r+") as f:
            del f["parse"].attrs["json"]                        # no parse: the trigger is taken as the start
        try:
            nominal = CA.run_cpa(self.path, "hw_product_mod", 0, self.dk, "0", "4")
            self.assertFalse(nominal["parsed"])
            self.assertLess(nominal["rank1_fraction"], 0.5)
        finally:
            with h5py.File(self.path, "r") as f:
                markers = f["marker"][:]
            P.save(self.path, P.parse_markers(markers, np.full(N, 26300), RATE))   # parse again for the other tests

    def test_reparse_from_the_stored_marker(self):
        import h5py
        with h5py.File(self.path, "r") as f:
            r = P.parse_markers(f["marker"], f["intermediates/cycles"][:], RATE)
        self.assertEqual(r["n_ok"], N)
        self.assertLessEqual(r["rise_max"] - r["rise_min"], 2 * JITTER + 2)


class TestExternalFiles(unittest.TestCase):
    def test_per_channel_folder_parse_and_view(self):
        import h5py
        import web_gui as W
        with tempfile.TemporaryDirectory() as tmp:
            rng = np.random.default_rng(3)
            for ch, data in (("C2", rng.normal(0, 1, (6, 800))), ("C4", np.stack([pulse(800, 300 + k) for k in range(6)]))):
                with h5py.File(Path(tmp) / ("%s_traces.h5" % ch), "w") as h:
                    h.create_dataset("trace_data_set", data=data.astype(np.float32))
                    h.attrs["sample_rate_hz"] = 2.5e9
            power, marker = str(Path(tmp) / "C2_traces.h5"), str(Path(tmp) / "C4_traces.h5")
            d = W.api_h5_describe({"path": power})
            self.assertEqual((d["kind"], d["power"]), ("external", "trace_data_set"))
            self.assertEqual([x["name"] for x in d["siblings"]], ["C4_traces.h5"])
            res = P.parse_markers(h5py.File(marker, "r")["trace_data_set"], None, 2.5e9)
            where = P.save(power, res)
            self.assertTrue(where.endswith(".parse.npz"))
            self.assertEqual(P.load(power)["n_ok"], 6)
            v = W.api_h5_view({"file": power, "marker_file": marker, "count": 3, "marker": True, "stats": True})
            self.assertTrue(v["aligned"])
            self.assertEqual(v["axis"]["bmul_windows"], [])      # not an ML-KEM campaign: no ML-KEM windows
            self.assertEqual(v["axis"]["origin"], "marker edge")
            x0 = [t["x"][int(np.argmin(np.abs(np.array(t["x"]))))] for t in v["traces"]]
            self.assertTrue(all(abs(x) < 0.001 for x in x0))     # every trace starts at its own edge


if __name__ == "__main__":
    unittest.main()
