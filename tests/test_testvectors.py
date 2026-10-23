"""cpa_toolkit/testvectors.py: determinism, expected keys, class split, storage.

    uv run python -m unittest discover -s tests -v
"""

import csv
import io
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cpa_toolkit"))

import mlkem768 as KEM  # noqa: E402
import mlkem_ref as R  # noqa: E402
import testvectors as TV  # noqa: E402

SEED = bytes(range(64))


class TestVectorSets(unittest.TestCase):
    def test_deterministic(self):
        for kw in ({"mode": "cpa", "strategy": "valid"}, {"mode": "cpa", "strategy": "invalid"},
                   {"mode": "tvla", "invalid_random": False}):
            a = TV.generate(n=12, seed=SEED, **kw)
            b = TV.generate(n=12, seed=SEED, **kw)
            self.assertEqual(a.dk, b.dk)
            for f in ("ct", "k", "cls", "m", "valid"):
                np.testing.assert_array_equal(getattr(a, f), getattr(b, f), err_msg=f)
        c = TV.generate("cpa", 12, bytes(64), "valid")
        self.assertFalse(np.array_equal(c.ct, TV.generate("cpa", 12, SEED, "valid").ct))

    def test_keypair_is_fips203_keygen_of_the_seed(self):
        vs = TV.generate("cpa", 2, SEED, "valid")
        ek, dk = KEM.keygen(SEED[:32], SEED[32:])
        self.assertEqual((vs.ek, vs.dk), (ek, dk))

    def test_expected_keys_match_software_decaps(self):
        for kw in ({"mode": "cpa", "strategy": "valid"}, {"mode": "cpa", "strategy": "invalid"},
                   {"mode": "tvla", "invalid_random": True}):
            vs = TV.generate(n=10, seed=SEED, **kw)
            keys, valid = KEM.decaps_batch(vs.dk, vs.ct)
            np.testing.assert_array_equal(keys, vs.k, err_msg=str(kw))
            np.testing.assert_array_equal(valid, vs.valid, err_msg=str(kw))
            self.assertTrue(vs.verify_software()["ok"])

    def test_valid_rows_encapsulate_their_message(self):
        vs = TV.generate("cpa", 6, SEED, "valid")
        self.assertTrue(vs.valid.all() and vs.has_m.all())
        for i in range(vs.n):
            k, ct = KEM.encaps(vs.ek, vs.m[i].tobytes())
            self.assertEqual(ct, vs.ct[i].tobytes())
            self.assertEqual(k, vs.k[i].tobytes())
            self.assertEqual(R.decrypt(vs.dk, ct), vs.m[i].tobytes())

    def test_invalid_rows_are_rejected(self):
        vs = TV.generate("cpa", 8, SEED, "invalid")
        self.assertFalse(vs.valid.any())
        self.assertFalse(vs.has_m.any())

    def test_tvla_split_and_fixed_class(self):
        vs = TV.generate("tvla", 21, SEED)
        self.assertEqual((vs.meta["n_fixed"], vs.meta["n_random"]), (10, 11))
        fixed = vs.ct[vs.cls == 0]
        self.assertTrue((fixed == fixed[0]).all())
        self.assertEqual(vs.m[vs.cls == 0][0].tobytes(), R.fixed_message(SEED))
        self.assertEqual(len({c.tobytes() for c in vs.ct[vs.cls == 1]}), 11)
        self.assertFalse((vs.cls[:10] == 0).all() and (vs.cls[10:] == 1).all(), "classes not shuffled")
        items = vs.campaign_items()
        self.assertEqual(items[3], (int(vs.cls[3]), vs.ct[3].tobytes()))

    def test_save_load_and_exports(self):
        vs = TV.generate("tvla", 6, SEED, invalid_random=True)
        with tempfile.TemporaryDirectory() as td:
            p = vs.save(Path(td) / "set.npz")
            back = TV.VectorSet.load(p)
            self.assertEqual(back.meta, vs.meta)
            self.assertEqual(back.dk, vs.dk)
            np.testing.assert_array_equal(back.ct, vs.ct)
            listed = TV.list_sets(Path(td))
            self.assertEqual(listed[0]["id"], vs.meta["id"])
        rows = list(csv.reader(io.StringIO(vs.to_csv())))
        self.assertEqual(rows[0][:3], ["index", "class", "ciphertext_hex"])
        self.assertEqual(len(rows), vs.n + 1)
        self.assertEqual(rows[1][2], vs.ct[0].tobytes().hex())

    def test_short_seed_is_expanded(self):
        a = TV.generate("cpa", 2, b"\x01\x02", "valid")
        self.assertEqual(len(TV.seed64(b"\x01\x02")), 64)
        self.assertEqual(a.meta["seed"], "0102")


if __name__ == "__main__":
    unittest.main()
