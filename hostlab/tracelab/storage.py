"""On-disk format for a measurement campaign.

A campaign directory holds plain ``.npy`` arrays and one JSON file::

    traces.npy    (N, S) samples, dtype as acquired (int8/int16/float32)
    inputs.npy    (N, L) uint8, the input given to the core for each trace
    classes.npy   (N,)   uint8, 0 = fixed, 1 = random
    cycles.npy    (N,)   uint32, CYCLE_COUNT reported by the core
    outputs.npy   (N, K) uint8, what the core returned (K = 32 for Decaps)
    meta.json     parameters, instrument settings, provenance, progress

Arrays are created at full size and filled row by row through a memory map,
so a campaign that is interrupted after a few hours leaves a readable file:
``meta.json`` records ``completed``, the number of rows actually written.
Nothing is held in RAM, so the length of a campaign is limited by disk only.

Everything is standard ``numpy``: analysis code elsewhere (or a colleague's)
can open the set with ``np.load(..., mmap_mode="r")`` without this package.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timezone

import numpy as np

#: Files that make up a campaign, in the order they are described above.
ARRAYS = ("traces", "inputs", "classes", "cycles", "outputs")


def _git_head(root: str) -> str:
    try:
        r = subprocess.run(
            ["git", "-C", root, "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
        )
        return r.stdout.strip() or "unknown"
    except OSError:  # pragma: no cover
        return "unknown"


class TraceSet:
    """A campaign being written to disk, or read back for analysis."""

    def __init__(self, path: str, meta: dict, arrays: dict, mode: str = "r"):
        self.path = path
        self.meta = meta
        self._a = arrays
        self.mode = mode
        self._completed = int(meta.get("completed", 0))

    # --- creation ---------------------------------------------------------
    @classmethod
    def create(
        cls,
        path: str,
        n_traces: int,
        n_samples: int,
        input_len: int,
        output_len: int = 32,
        dtype: str = "int16",
        meta: dict = None,
        root: str = None,
    ) -> "TraceSet":
        """Allocate the arrays of a new campaign under ``path``."""
        os.makedirs(path, exist_ok=True)
        shapes = {
            "traces": ((n_traces, n_samples), np.dtype(dtype)),
            "inputs": ((n_traces, input_len), np.uint8),
            "classes": ((n_traces,), np.uint8),
            "cycles": ((n_traces,), np.uint32),
            "outputs": ((n_traces, output_len), np.uint8),
        }
        arrays = {
            name: np.lib.format.open_memmap(
                os.path.join(path, name + ".npy"), mode="w+", dtype=dt, shape=shape
            )
            for name, (shape, dt) in shapes.items()
        }
        full = dict(meta or {})
        full.update(
            {
                "created": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                "traces": n_traces,
                "samples": n_samples,
                "trace_dtype": str(np.dtype(dtype)),
                "input_bytes": input_len,
                "output_bytes": output_len,
                "completed": 0,
                "git": _git_head(root or os.path.join(os.path.dirname(__file__), "..", "..")),
            }
        )
        ts = cls(path, full, arrays, mode="w")
        ts._write_meta()
        return ts

    # --- writing ----------------------------------------------------------
    def append(
        self,
        index: int,
        trace: np.ndarray,
        input_bytes: bytes,
        cls: int,
        cycles: int,
        output: bytes = b"",
    ) -> None:
        """Store one trace and everything recorded with it."""
        if self.mode != "w":
            raise RuntimeError("trace set is open for reading")
        a = self._a
        n = a["traces"].shape[1]
        if len(trace) < n:
            raise ValueError("trace has %d samples, campaign expects %d" % (len(trace), n))
        a["traces"][index] = trace[:n]
        a["inputs"][index] = np.frombuffer(input_bytes, dtype=np.uint8)[: a["inputs"].shape[1]]
        a["classes"][index] = cls
        a["cycles"][index] = cycles
        if output:
            k = a["outputs"].shape[1]
            a["outputs"][index, : min(len(output), k)] = np.frombuffer(
                output[:k], dtype=np.uint8
            )
        self._completed = max(self._completed, index + 1)

    def flush(self, extra: dict = None) -> None:
        """Push the arrays to disk and update ``meta.json``."""
        for arr in self._a.values():
            arr.flush()
        if extra:
            self.meta.update(extra)
        self.meta["completed"] = self._completed
        self._write_meta()

    def close(self, extra: dict = None) -> None:
        self.flush(extra)
        self.meta["closed"] = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        self._write_meta()

    def _write_meta(self) -> None:
        with open(os.path.join(self.path, "meta.json"), "w") as f:
            json.dump(self.meta, f, indent=1, sort_keys=False)
            f.write("\n")

    def __enter__(self) -> "TraceSet":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- reading ----------------------------------------------------------
    @classmethod
    def load(cls, path: str, mmap: bool = True) -> "TraceSet":
        """Open an existing campaign for analysis."""
        with open(os.path.join(path, "meta.json")) as f:
            meta = json.load(f)
        arrays = {}
        for name in ARRAYS:
            p = os.path.join(path, name + ".npy")
            if os.path.exists(p):
                arrays[name] = np.load(p, mmap_mode="r" if mmap else None)
        return cls(path, meta, arrays, mode="r")

    @property
    def completed(self) -> int:
        """Rows actually written (a campaign may have been interrupted)."""
        return int(self.meta.get("completed", 0))

    def _view(self, name: str) -> np.ndarray:
        return self._a[name][: self.completed]

    @property
    def traces(self) -> np.ndarray:
        return self._view("traces")

    @property
    def inputs(self) -> np.ndarray:
        return self._view("inputs")

    @property
    def classes(self) -> np.ndarray:
        return self._view("classes")

    @property
    def cycles(self) -> np.ndarray:
        return self._view("cycles")

    @property
    def outputs(self) -> np.ndarray:
        return self._view("outputs")

    def summary(self) -> str:
        m = self.meta
        fixed = int((self.classes == 0).sum()) if self.completed else 0
        lines = [
            "campaign  %s" % self.path,
            "  created   %s" % m.get("created", "?"),
            "  operation %s" % m.get("op", "?"),
            "  traces    %d of %d written (%d fixed, %d random)"
            % (self.completed, m.get("traces", 0), fixed, self.completed - fixed),
            "  samples   %d per trace, dtype %s" % (m.get("samples", 0), m.get("trace_dtype", "?")),
            "  core      %s, git %s" % (m.get("core_version", "?"), m.get("git", "?")),
        ]
        if self.completed:
            c = self.cycles
            lines.append(
                "  cycles    %d..%d (%s)"
                % (c.min(), c.max(), "constant" if c.min() == c.max() else "VARYING")
            )
        if m.get("scope"):
            s = m["scope"]
            lines.append(
                "  scope     %s, %s Sa/s, %s samples"
                % (s.get("id", "?"), s.get("sample_rate", "?"), s.get("samples", "?"))
            )
        return "\n".join(lines)
