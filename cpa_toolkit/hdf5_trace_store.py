"""HDF5 trace storage: one campaign = one ``.hdf5`` file.

Two campaign *modes*, stored in ``/metadata`` as ``mode`` and never mixed in
one file -- they answer different questions and need different analysis
(``cpa_attack.py`` vs ``tvla_analysis.py``):

- ``"cpa"``: fixed ``dk``, N *different* ciphertexts (one trace each),
  attacked by correlation against a key-dependent hypothesis.
- ``"tvla"``: fixed ``dk``, ciphertexts drawn from two classes (fixed /
  random, FIPS203 Decaps TVLA methodology), compared by Welch's t-test --
  no key hypothesis involved at all.

Layout (CPA-mode layout is unchanged and stays compatible with the
structure read by ``hdf5_loader.py`` in
Chaosnap/Post-Quantum-Cryptography-ML-, so either loader can open files the
other wrote)::

    /traces        (N, S) float32   -- one row per capture
    /samples       (S,)   float64   -- time axis in seconds (0 if unknown)
    /metadata/*    scalars          -- mode, sample_rate, core_mhz, trigger, ...
                                        ``recipe`` (JSON): the metadata of the
                                        test-vector set the campaign sends
                                        (testvectors.py: mode, strategy, n, seed,
                                        ...) -- enough to rebuild its key pair
                                        when a stopped campaign is resumed
    /intermediates/ct     (N, 1088) uint8   -- ciphertext sent for this trace
    /intermediates/dk_hash (32,)    uint8   -- SHA3-256 of dk (identifies the
                                                fixed key without storing it)
    /intermediates/cycles (N,)      uint32  -- CYCLE_COUNT reported by the core
    /intermediates/output (N, 32)   uint8   -- K returned by the core
    /intermediates/k_expected (N, 32) uint8 -- K the core must return (FIPS 203),
                                                when known; ``ok`` includes the check
    /intermediates/ok     (N,)      bool    -- integrity checks passed
    /intermediates/class  (N,)      uint8   -- TVLA mode ONLY: 0=fixed, 1=random

Written incrementally (resizable datasets, flushed every trace) so a
multi-hour campaign surviving a crash or Ctrl-C keeps every trace captured
so far; ``n_completed`` in ``/metadata`` records how many rows are valid.

``/metadata`` carries ``simulated`` (bool). Files written by this toolkit are
always real captures (False); older files from the removed synthetic dry-run
flow carry True, and every consumer still surfaces it.

The secret key never goes into the file -- only a hash of ``dk``, enough to
confirm two files used the same key pair without ever writing it to disk.
"""

from __future__ import annotations

import hashlib
import pathlib
import time
from typing import Any

import numpy as np


def _h5py():
    try:
        import h5py
        return h5py
    except ImportError:
        raise SystemExit("h5py is missing: uv sync (or pip install h5py)")


class TraceWriter:
    """Incremental writer for one campaign file.

    :param mode: ``"cpa"`` (default) or ``"tvla"``. TVLA mode additionally
        allocates ``/intermediates/class`` and ``append()`` requires ``cls``.
    :param items: the full planned ``list[bytes]`` (cpa) or
        ``list[(cls, bytes)]`` (tvla) -- written into ``/intermediates/ct``
        (and ``class``) for EVERY row immediately, before any trace is
        captured. Every row, fresh or resumed, reads its ciphertext back from
        the file (``ct_at()`` / ``cls_at()``), so the file alone defines what
        a campaign sent.
    :param expected: optional ``(N, 32)`` uint8, the shared secret each row's
        Decaps must return; stored as ``/intermediates/k_expected``.
    """

    def __init__(self, path: str | pathlib.Path, n_traces: int, n_samples: int, dk: bytes,
                 meta: dict[str, Any], mode: str = "cpa", items: list | None = None,
                 expected: np.ndarray | None = None):
        if mode not in ("cpa", "tvla"):
            raise ValueError("mode must be 'cpa' or 'tvla', got %r" % mode)
        h5py = _h5py()
        self.path = pathlib.Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.n_traces, self.n_samples, self.mode = n_traces, n_samples, mode
        self.f = h5py.File(str(self.path), "w")
        self.f.create_dataset("traces", (n_traces, n_samples), dtype="float32",
                               chunks=(1, n_samples), compression="gzip", compression_opts=4)
        self.f.create_dataset("samples", data=np.arange(n_samples, dtype=np.float64))
        g = self.f.create_group("metadata")
        for k, v in meta.items():
            g.attrs[k] = v
        g.attrs["mode"] = mode
        g.attrs["n_traces_planned"] = n_traces
        g.attrs["n_completed"] = 0
        g.attrs["created_unix"] = time.time()
        if "simulated" not in g.attrs:  # callers should always pass this in meta
            g.attrs["simulated"] = False
        iv = self.f.create_group("intermediates")
        iv.create_dataset("ct", (n_traces, 1088), dtype="uint8")
        iv.create_dataset("dk_hash", data=np.frombuffer(hashlib.sha3_256(dk).digest(), dtype="uint8"))
        iv.create_dataset("cycles", (n_traces,), dtype="uint32")
        iv.create_dataset("output", (n_traces, 32), dtype="uint8")
        iv.create_dataset("ok", (n_traces,), dtype="bool")
        if mode == "tvla":
            iv.create_dataset("class", (n_traces,), dtype="uint8")
        if items is not None:
            if len(items) != n_traces:
                raise ValueError("items has %d entries, expected n_traces=%d" % (len(items), n_traces))
            cts = [it[1] if mode == "tvla" else it for it in items]
            iv["ct"][:] = np.stack([np.frombuffer(ct.ljust(1088, b"\0")[:1088], dtype=np.uint8) for ct in cts])
            if mode == "tvla":
                iv["class"][:] = np.array([int(it[0]) for it in items], dtype=np.uint8)
        if expected is not None:
            if expected.shape != (n_traces, 32):
                raise ValueError("expected must be (%d, 32), got %s" % (n_traces, expected.shape))
            iv.create_dataset("k_expected", data=np.asarray(expected, dtype=np.uint8))
        self._n_written = 0

    def ct_at(self, index: int) -> bytes:
        """This row's ciphertext, as already committed to the file -- the
        one authoritative source both a fresh and a resumed capture loop
        read from (see the class docstring)."""
        return bytes(self.f["intermediates/ct"][index])

    def expected_at(self, index: int) -> bytes | None:
        """The shared secret this row must produce, or None if the file has none."""
        if "k_expected" not in self.f["intermediates"]:
            return None
        return bytes(self.f["intermediates/k_expected"][index])

    def cls_at(self, index: int) -> int:
        if self.mode != "tvla":
            raise ValueError("cls_at() is TVLA-mode only")
        return int(self.f["intermediates/class"][index])

    def append(self, index: int, trace: np.ndarray, ct: bytes, cycles: int, output: bytes, ok: bool,
               cls: int | None = None) -> None:
        if self.mode == "tvla" and cls is None:
            raise ValueError("tvla-mode campaign: append() needs cls=0 (fixed) or cls=1 (random)")
        self.f["traces"][index] = trace.astype(np.float32)
        self.f["intermediates/ct"][index] = np.frombuffer(ct.ljust(1088, b"\0")[:1088], dtype="uint8")
        self.f["intermediates/cycles"][index] = cycles
        out32 = (output + b"\0" * 32)[:32]
        self.f["intermediates/output"][index] = np.frombuffer(out32, dtype="uint8")
        self.f["intermediates/ok"][index] = ok
        if self.mode == "tvla":
            self.f["intermediates/class"][index] = int(cls)
        self._n_written = max(self._n_written, index + 1)
        self.f["metadata"].attrs["n_completed"] = self._n_written

    @classmethod
    def open_existing(cls, path: str | pathlib.Path) -> "TraceWriter":
        """Reopen a partially-written file (from an earlier, stopped campaign) to continue it.

        Used by the GUI's/CLI's "Resume" action: the file already has its
        datasets allocated at full size (``n_traces`` from when it was first
        created, never resized), so continuing just means writing into the
        rows from ``n_completed`` onward -- no schema change needed. The
        ciphertexts, classes and expected keys of the remaining rows are
        already in the file; only ``dk`` (never stored) has to be rebuilt by
        the caller from the ``recipe`` seed.
        """
        self = cls.__new__(cls)
        h5py = _h5py()
        self.path = pathlib.Path(path)
        self.f = h5py.File(str(self.path), "r+")
        mode = str(self.f["metadata"].attrs.get("mode", "cpa"))
        if mode not in ("cpa", "tvla"):
            self.f.close()
            raise ValueError("mode must be 'cpa' or 'tvla', got %r" % mode)
        self.mode = mode
        self.n_traces = int(self.f["traces"].shape[0])
        self.n_samples = int(self.f["traces"].shape[1])
        self._n_written = int(self.f["metadata"].attrs.get("n_completed", 0))
        return self

    @property
    def n_completed(self) -> int:
        return self._n_written

    def flush(self) -> None:
        self.f.flush()

    def set_sample_rate(self, hz: float, record_start_s: float | None = None) -> None:
        """Sample rate and, when the scope reports it, the time of sample 0 relative
        to the trigger (negative = pre-trigger data). ``samples`` becomes the time
        after the trigger of every sample."""
        if record_start_s is not None:
            self.f["metadata"].attrs["record_start_s"] = float(record_start_s)
        if hz > 0:
            start = float(self.f["metadata"].attrs.get("record_start_s", 0.0))
            self.f["samples"][:] = start + np.arange(self.n_samples, dtype=np.float64) / hz
            self.f["metadata"].attrs["sample_rate"] = hz

    def close(self) -> None:
        self.f.close()

    def __enter__(self) -> "TraceWriter":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class TraceReader:
    """Read-only access to a campaign file, for the CPA/TVLA analysis scripts."""

    def __init__(self, path: str | pathlib.Path):
        h5py = _h5py()
        self.path = pathlib.Path(path)
        self.f = h5py.File(str(self.path), "r")
        self.n_completed = int(self.f["metadata"].attrs.get("n_completed", self.f["traces"].shape[0]))
        # Older files (written before `mode` existed) are all CPA-mode.
        self.mode = str(self.f["metadata"].attrs.get("mode", "cpa"))

    @property
    def traces(self) -> np.ndarray:
        """``(n_completed, n_samples)`` float32, loaded into memory."""
        return self.f["traces"][: self.n_completed]

    @property
    def ciphertexts(self) -> np.ndarray:
        """``(n_completed, 1088)`` uint8."""
        return self.f["intermediates/ct"][: self.n_completed]

    @property
    def cycles(self) -> np.ndarray:
        return self.f["intermediates/cycles"][: self.n_completed]

    @property
    def ok(self) -> np.ndarray:
        return self.f["intermediates/ok"][: self.n_completed]

    @property
    def classes(self) -> np.ndarray:
        """``(n_completed,)`` uint8, 0=fixed/1=random. TVLA-mode files only."""
        if "class" not in self.f["intermediates"]:
            raise ValueError("%s is not a TVLA-mode file (no class labels)" % self.path)
        return self.f["intermediates/class"][: self.n_completed]

    @property
    def expected_k(self) -> np.ndarray | None:
        """``(n_completed, 32)`` uint8 expected shared secrets, or None (older files)."""
        if "k_expected" not in self.f["intermediates"]:
            return None
        return self.f["intermediates/k_expected"][: self.n_completed]

    @property
    def outputs(self) -> np.ndarray:
        return self.f["intermediates/output"][: self.n_completed]

    @property
    def dk_hash(self) -> bytes:
        return bytes(self.f["intermediates/dk_hash"][:])

    @property
    def time_axis(self) -> np.ndarray:
        """``(n_samples,)`` float64 seconds -- 0..n_samples-1 if the sample
        rate was never set; for the trace viewer's x-axis."""
        return self.f["samples"][:]

    @property
    def sample_rate(self) -> float:
        return float(self.f["metadata"].attrs.get("sample_rate", 0.0))

    @property
    def record_start_s(self) -> float | None:
        """Time of sample 0 relative to the trigger (s), or None if the campaign did
        not record it (taken as 0: sample 0 = Decaps start)."""
        v = self.f["metadata"].attrs.get("record_start_s")
        return None if v is None else float(v)

    @property
    def simulated(self) -> bool:
        """True only for files from the removed synthetic dry-run flow."""
        return bool(self.f["metadata"].attrs.get("simulated", False))

    @property
    def metadata(self) -> dict:
        return dict(self.f["metadata"].attrs)

    def close(self) -> None:
        self.f.close()

    def __enter__(self) -> "TraceReader":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def summarize(path: str | pathlib.Path) -> dict:
    with TraceReader(path) as r:
        n_planned = int(r.f["metadata"].attrs.get("n_traces_planned", r.n_completed))
        out = {
            "path": str(path),
            "mode": r.mode,
            "simulated": r.simulated,
            "n_completed": r.n_completed,
            "n_traces_planned": n_planned,
            # Resumable: stopped before finishing, and its `recipe` (vector-set
            # metadata, with the seed) lets acquire_cpa rebuild dk to continue.
            "resumable": bool(n_planned > r.n_completed and "recipe" in r.f["metadata"].attrs
                              and "k_expected" in r.f["intermediates"]),
            "n_samples": r.f["traces"].shape[1],
            "sample_rate_hz": r.sample_rate,
            "record_start_s": r.record_start_s,
            "dk_hash": r.dk_hash.hex()[:16] + "...",
            "ok_fraction": float(np.mean(r.ok)) if r.n_completed else 0.0,
            "metadata": r.metadata,
        }
        exp = r.expected_k
        if exp is not None and r.n_completed:
            # fraction of captures whose Decaps output equals the FIPS 203 answer
            out["k_match_fraction"] = float(np.mean(np.all(r.outputs == exp, axis=1)))
        if r.mode == "tvla" and r.n_completed:
            cls = r.classes
            out["n_fixed"] = int(np.sum(cls == 0))
            out["n_random"] = int(np.sum(cls == 1))
        return out
