"""Plaintext-checking oracle key recovery (cpa_toolkit/pc_oracle.py).

Covers the chosen-ciphertext construction against the FIPS 203 reference decrypt, the key-independent
references, the thermometer decode, round-tripping a ciphertext back to its query, and a full
generate -> capture -> decode loop on a small simulated campaign.

    uv run python -m unittest discover -s tests -v
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cpa_toolkit"))

import config as C  # noqa: E402
import mlkem768 as KEM  # noqa: E402
import mlkem_ref as R  # noqa: E402
import pc_oracle as PCO  # noqa: E402
import testvectors as TV  # noqa: E402
from hdf5_trace_store import TraceWriter  # noqa: E402

SEED = bytes(range(64))


def key():
    s64 = TV.seed64(SEED)
    return KEM.keygen(s64[:32], s64[32:])[1]


class TestOracle(unittest.TestCase):
    """The chosen ciphertexts drive the reference decrypt exactly as the model predicts."""

    @classmethod
    def setUpClass(cls):
        cls.dk = key()
        cls.truth = PCO.secret_time_domain(cls.dk)

    def test_secret_is_small(self):
        self.assertTrue(np.all(self.truth >= PCO.S_MIN) and np.all(self.truth <= PCO.S_MAX))

    def test_message_matches_the_reference_decrypt(self):
        rng = np.random.default_rng(0)
        for _ in range(120):
            poly, t = int(rng.integers(0, R.K)), int(rng.integers(0, 256))
            z = int(rng.choice(PCO.THERMO_LEVELS + [PCO.REF_ZERO, PCO.REF_ONE]))
            m = R.decrypt(self.dk, PCO.chosen_ciphertext(poly, t, z))
            bits = np.unpackbits(np.frombuffer(m, np.uint8), bitorder="little")
            self.assertEqual(int(bits[t]), PCO.message_bit(z, int(self.truth[poly, t])))
            self.assertEqual(int(bits[np.arange(256) != t].sum()), 0)   # only the attacked bit can be set

    def test_references_are_key_independent(self):
        self.assertEqual({PCO.message_bit(PCO.REF_ZERO, s) for s in PCO.SECRET_VALUES}, {0})
        self.assertEqual({PCO.message_bit(PCO.REF_ONE, s) for s in PCO.SECRET_VALUES}, {1})

    def test_thermometer_decodes_every_secret(self):
        for s in PCO.SECRET_VALUES:
            bits = [PCO.message_bit(z, s) for z in PCO.THERMO_LEVELS]
            d = PCO.decode_coefficient(bits)
            self.assertTrue(d["clean"])
            self.assertEqual(d["value"], s)

    def test_a_non_thermometer_pattern_is_flagged(self):
        # the decoder's contract: a clean thermometer decodes exactly (tested above); a pattern that
        # is not a thermometer (a measurement error) is marked not clean and decoded to a nearest one
        bits = [1, 0, 1, 0]                                         # not monotone
        d = PCO.decode_coefficient(bits)
        self.assertFalse(d["clean"])
        self.assertIn(d["value"], PCO.SECRET_VALUES)

    def test_software_recovers_the_whole_key(self):
        res = PCO.verify_recovery(self.dk)
        self.assertTrue(res["ok"])
        self.assertEqual(res["recovered"], 768)
        self.assertEqual(res["total_queries"], 768 * len(PCO.THERMO_LEVELS))


class TestParsing(unittest.TestCase):
    def test_every_query_round_trips(self):
        polys, coeffs = range(R.K), [0, 1, 5, 200, 255]
        for q in PCO.plan(polys, coeffs):
            p = PCO.parse_ciphertext(q["ct"])
            self.assertEqual((p["poly"], p["coeff"], p["kind"], p["level"]),
                             (q["poly"], q["coeff"], "sweep", q["level"]))
        for q in PCO.reference_plan(polys, coeffs):
            p = PCO.parse_ciphertext(q["ct"])
            self.assertEqual((p["poly"], p["coeff"], p["kind"]), (q["poly"], q["coeff"], "ref-" + q["ref"]))

    def test_other_ciphertexts_do_not_parse(self):
        rng = np.random.default_rng(1)
        misses = sum(PCO.parse_ciphertext(rng.integers(0, 256, R.CT_LEN, dtype=np.uint8).tobytes()) is not None
                     for _ in range(300))
        self.assertEqual(misses, 0)
        # a valid encapsulation is not one of ours either
        vs = TV.generate("cpa", 4, SEED, "valid")
        self.assertTrue(all(PCO.parse_ciphertext(vs.ct[i].tobytes()) is None for i in range(4)))


class TestGenerateStrategy(unittest.TestCase):
    def test_pc_oracle_vectorset_is_all_recognisable_queries(self):
        vs = TV.generate("cpa", 60, SEED, "pc-oracle")
        self.assertEqual(vs.meta["strategy"], "pc-oracle")
        kinds = [PCO.parse_ciphertext(vs.ct[i].tobytes()) for i in range(vs.n)]
        self.assertTrue(all(k is not None for k in kinds))
        self.assertTrue({k["kind"] for k in kinds} <= {"sweep", "ref-zero", "ref-one"})


class TestDecodeCampaign(unittest.TestCase):
    """A small generate -> simulate -> capture -> decode loop (poly 0, 8 coefficients)."""

    def test_recovers_the_covered_coefficients(self):
        dk = key()
        truth = PCO.secret_time_domain(dk)
        polys, coeffs, reps = [0], range(8), 30
        queries = PCO.reference_plan(polys, coeffs) + PCO.plan(polys, coeffs)
        rng = np.random.default_rng(3)
        items = [q for q in queries for _ in range(reps)]
        rng.shuffle(items)
        n_samples = int(38e-6 * 2.5e9)                 # reaches past G(m'||h)
        a, b = PCO.reencrypt_window(2.5e9, n_samples)
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "pco.hdf5")
            cts = [it["ct"] for it in items]
            ks, _ = KEM.decaps_batch(dk, np.stack([np.frombuffer(c, np.uint8) for c in cts]))
            meta = {"mode": "cpa", "strategy": "pc-oracle", "simulated": False, "core_mhz": C.CORE_MHZ,
                    "recipe": json.dumps({"seed": SEED.hex(), "mode": "cpa"})}
            with TraceWriter(path, len(items), n_samples, dk, meta, mode="cpa", items=cts, expected=ks) as w:
                for i, it in enumerate(items):
                    wbit = (PCO.message_bit(it["v_code"], int(truth[it["poly"], it["coeff"]]))
                            if it.get("level") is not None else (1 if it.get("ref") == "one" else 0))
                    tr = rng.normal(0, 2.0, n_samples).astype(np.float32)        # SNR 0.5
                    tr[a:b] += wbit * rng.normal(0.0, 1.0, b - a)                # weight-1 = more switching
                    w.append(i, tr, it["ct"], 26367, ks[i].tobytes(), ok=True)
                w.set_sample_rate(2.5e9)
            res = PCO.decode_campaign(path)
        self.assertEqual(res["coefficients_recovered"], 8)
        self.assertTrue(res["known_key"])
        self.assertEqual(res["correct"], 8)            # every covered coefficient, exactly
        self.assertEqual(res["unknown_ciphertexts"], 0)


if __name__ == "__main__":
    unittest.main()
