"""Trace library and vector-set management of cpa_toolkit/web_gui.py:
tags (single and bulk), trash, restore, permanent delete, empty trash.

Runs the API functions directly against temporary data directories.

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

import hdf5_trace_store as HS  # noqa: E402
import testvectors as TV  # noqa: E402
import web_gui as W  # noqa: E402


class TestLibrary(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.saved = (W.TRACES_DIR, W.TRASH_DIR, W.ANALYSIS_DIR, W.VECTORS_DIR, W.VEC_TRASH)
        W.TRACES_DIR, W.ANALYSIS_DIR, W.VECTORS_DIR = self.tmp / "traces", self.tmp / "analysis", self.tmp / "vectors"
        W.TRASH_DIR, W.VEC_TRASH = W.TRACES_DIR / ".trash", W.VECTORS_DIR / ".trash"
        for d in (W.TRACES_DIR, W.ANALYSIS_DIR, W.VECTORS_DIR):
            d.mkdir(parents=True)
        self.log = W.log
        W.log = lambda *a: None

    def tearDown(self):
        W.TRACES_DIR, W.TRASH_DIR, W.ANALYSIS_DIR, W.VECTORS_DIR, W.VEC_TRASH = self.saved
        W.log = self.log
        shutil.rmtree(self.tmp)

    # helpers
    def campaign(self, name):
        vs = TV.generate("cpa", 2, bytes(range(64)), "valid")
        path = W.TRACES_DIR / name
        w = HS.TraceWriter(path, 2, 8, vs.dk, {"vector_set": "x"}, items=[c.tobytes() for c in vs.ct], expected=vs.k)
        for i in range(2):
            w.append(i, np.zeros(8, np.float32), vs.ct[i].tobytes(), 100, vs.k[i].tobytes(), ok=True)
        w.close()
        return str(path)

    def vector_set(self):
        vs = TV.generate("cpa", 2, bytes(range(64)), "valid")
        return str(vs.save(W.VECTORS_DIR / (vs.meta["id"] + ".npz")))

    def trace_rows(self, trash=False):
        return {Path(r["path"]).name: r for r in W.api_traces_list({"trash": trash})["files"]}

    # tags
    def test_clean_tags(self):
        self.assertEqual(W._clean_tags(" probe A ,probe a, , x,y "), ["probe A", "x", "y"])
        self.assertEqual(W._clean_tags(["a" * 40]), ["a" * W.MAX_TAG_LEN])
        self.assertEqual(len(W._clean_tags([str(i) for i in range(40)])), W.MAX_TAGS)
        self.assertEqual(W._edit_tags(["A", "b"], ["c", "B"], ["a"]), ["b", "c"])

    def test_trace_tags(self):
        a, b = self.campaign("a.hdf5"), self.campaign("b.hdf5")
        W.api_traces_update({"path": a, "label": "run 1", "tags": "em-probe, 20k"})
        rows = self.trace_rows()
        self.assertEqual((rows["a.hdf5"]["label"], rows["a.hdf5"]["tags"]), ("run 1", ["em-probe", "20k"]))
        W.api_traces_tag({"paths": [a, b], "add": ["baseline"], "remove": ["20k"]})
        rows = self.trace_rows()
        self.assertEqual(rows["a.hdf5"]["tags"], ["em-probe", "baseline"])
        self.assertEqual(rows["b.hdf5"]["tags"], ["baseline"])
        self.assertEqual(rows["a.hdf5"]["label"], "run 1")             # tags do not touch the name

    def test_trace_trash_restore_purge(self):
        a, b = self.campaign("a.hdf5"), self.campaign("b.hdf5")
        (W.ANALYSIS_DIR / "a_x_cpa_report.json").write_text(json.dumps({"dataset": {"path": a}}))
        W.api_traces_delete({"paths": [a, b]})
        self.assertEqual(self.trace_rows(), {})
        self.assertEqual(set(self.trace_rows(trash=True)), {"a.hdf5", "b.hdf5"})
        W.api_traces_restore({"paths": [str(W.TRASH_DIR / "b.hdf5")]})
        self.assertEqual(set(self.trace_rows()), {"b.hdf5"})
        r = W.api_traces_purge({"paths": [str(W.TRASH_DIR / "a.hdf5")]})
        self.assertEqual((r["deleted"], r["reports"]), (1, 1))         # the report goes with it
        self.assertEqual(list(W.ANALYSIS_DIR.glob("*.json")), [])
        W.api_traces_delete({"path": b})
        self.assertEqual(W.api_traces_empty_trash({})["deleted"], 1)
        self.assertEqual((self.trace_rows(), self.trace_rows(trash=True)), ({}, {}))
        with self.assertRaises(ValueError):                             # only trashed files can be purged
            W.api_traces_purge({"path": self.campaign("c.hdf5")})

    def test_vector_sets(self):
        p = self.vector_set()
        W.api_vectors_update({"path": p, "label": "fixed key A", "tags": ["cpa", "key-A"]})
        (s,) = W.api_vectors_list({})["sets"]
        self.assertEqual((s["label"], s["tags"]), ("fixed key A", ["cpa", "key-A"]))
        W.api_vectors_tag({"paths": [p], "add": "night run", "remove": "cpa"})
        self.assertEqual(W.api_vectors_list({})["sets"][0]["tags"], ["key-A", "night run"])
        W.api_vectors_delete({"path": p})                               # name and tags go along
        self.assertEqual(W.api_vectors_list({})["sets"], [])
        (t,) = W.api_vectors_list({"trash": "1"})["sets"]
        self.assertEqual((t["label"], t["tags"]), ("fixed key A", ["key-A", "night run"]))
        W.api_vectors_restore({"path": t["path"]})
        self.assertEqual(W.api_vectors_list({})["sets"][0]["label"], "fixed key A")
        W.api_vectors_delete({"path": p})
        W.api_vectors_purge({"path": t["path"]})
        self.assertEqual(list(W.VEC_TRASH.iterdir()), [])                # .npz and .meta.json gone
        with self.assertRaises(ValueError):
            W.api_vectors_purge({"path": self.vector_set()})            # not in the trash


if __name__ == "__main__":
    unittest.main()
