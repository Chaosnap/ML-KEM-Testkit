"""cpa_toolkit/mlkem768.py against the NIST ACVP ML-KEM-768 vectors (tests/acvp/).

    uv run python -m unittest discover -s tests -v
"""

import json
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cpa_toolkit"))

import mlkem768 as KEM  # noqa: E402

ACVP = ROOT / "tests" / "acvp"


def _groups(name: str) -> list:
    d = json.loads((ACVP / name / "internalProjection.json").read_text())
    if isinstance(d, list):  # ACVP files are [ {acvVersion}, {vectorSet} ]
        d = d[-1]
    return [g for g in d["testGroups"] if g["parameterSet"] == "ML-KEM-768"]


def _hex(s: str) -> bytes:
    return bytes.fromhex(s)


class TestMLKEM768(unittest.TestCase):
    def test_keygen_acvp(self):
        tests = [t for g in _groups("ML-KEM-keyGen-FIPS203") for t in g["tests"]]
        self.assertEqual(len(tests), 25)
        for t in tests:
            ek, dk = KEM.keygen(_hex(t["d"]), _hex(t["z"]))
            self.assertEqual(ek.hex(), t["ek"].lower(), "tcId %d ek" % t["tcId"])
            self.assertEqual(dk.hex(), t["dk"].lower(), "tcId %d dk" % t["tcId"])

    def test_encaps_acvp(self):
        g = [g for g in _groups("ML-KEM-encapDecap-FIPS203") if g["function"] == "encapsulation"]
        tests = [t for gg in g for t in gg["tests"]]
        self.assertEqual(len(tests), 25)
        for t in tests:
            k, c = KEM.encaps(_hex(t["ek"]), _hex(t["m"]))
            self.assertEqual(c.hex(), t["c"].lower(), "tcId %d c" % t["tcId"])
            self.assertEqual(k.hex(), t["k"].lower(), "tcId %d k" % t["tcId"])

    def test_encaps_batch_matches_single(self):
        t = [t for g in _groups("ML-KEM-encapDecap-FIPS203") if g["function"] == "encapsulation"
             for t in g["tests"]][:6]
        ek = _hex(t[0]["ek"])
        ms = [_hex(x["m"]) for x in t]
        keys, cts = KEM.encaps_batch(ek, ms)
        for i, m in enumerate(ms):
            k1, c1 = KEM.encaps(ek, m)
            self.assertEqual(keys[i].tobytes(), k1)
            self.assertEqual(cts[i].tobytes(), c1)

    def test_decaps_acvp(self):
        g = [g for g in _groups("ML-KEM-encapDecap-FIPS203") if g["function"] == "decapsulation"]
        tests = [t for gg in g for t in gg["tests"]]
        self.assertEqual(len(tests), 10)
        for t in tests:
            k = KEM.decaps(_hex(t["dk"]), _hex(t["c"]))
            self.assertEqual(k.hex(), t["k"].lower(), "tcId %d (%s)" % (t["tcId"], t.get("reason")))

    def test_decaps_batch_flags_rejection(self):
        g = [g for g in _groups("ML-KEM-encapDecap-FIPS203") if g["function"] == "decapsulation"][0]
        by_dk = {}
        for t in g["tests"]:
            by_dk.setdefault(t["dk"], []).append(t)
        for dk_hex, tests in by_dk.items():
            cts = np.array([np.frombuffer(_hex(t["c"]), dtype=np.uint8) for t in tests])
            keys, valid = KEM.decaps_batch(_hex(dk_hex), cts)
            for i, t in enumerate(tests):
                self.assertEqual(keys[i].tobytes().hex(), t["k"].lower())
                self.assertEqual(bool(valid[i]), t.get("reason") == "valid decapsulation",
                                 "tcId %d: %s" % (t["tcId"], t.get("reason")))


if __name__ == "__main__":
    unittest.main()
