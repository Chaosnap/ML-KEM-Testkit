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
                                        ``recipe`` (JSON): {seed, strategy, poly,
                                        pair, invalid_random} -- the exact
                                        deterministic parameters that produced
                                        this file's ciphertexts from its seed,
                                        so a stopped campaign can be resumed by
                                        regenerating the same items and
                                        continuing from n_completed (see
                                        TraceWriter.open_existing() /
                                        acquire_cpa.run_campaign(resume=True))
    /intermediates/ct     (N, 1088) uint8   -- ciphertext sent for this trace
    /intermediates/dk_hash (32,)    uint8   -- SHA3-256 of dk (identifies the
                                                fixed key without storing it)
    /intermediates/cycles (N,)      uint32  -- CYCLE_COUNT reported by the core
    /intermediates/output (N, 32)   uint8   -- K returned by the core
    /intermediates/ok     (N,)      bool    -- integrity checks passed
    /intermediates/class  (N,)      uint8   -- TVLA mode ONLY: 0=fixed, 1=random

Written incrementally (resizable datasets, flushed every trace) so a
multi-hour campaign surviving a crash or Ctrl-C keeps every trace captured
so far; ``n_completed`` in ``/metadata`` records how many rows are valid.

``/metadata`` always carries ``simulated`` (bool): whether this file came
from ``SimTarget``/synthetic scopes (dry-run) or real hardware. Every
consumer (GUI, analysis scripts) must surface this -- a synthetic file is
software validation, never evidence about the real core (README.md section 6).

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
        raise SystemExit("h5py is missing: pip install -r requirements.txt")


class TraceWriter:
    """Incremental writer for one campaign file.

    :param mode: ``"cpa"`` (default) or ``"tvla"``. TVLA mode additionally
        allocates ``/intermediates/class`` and ``append()`` requires ``cls``.
    :param items: the full planned ``list[bytes]`` (cpa) or
        ``list[(cls, bytes)]`` (tvla) -- written into ``/intermediates/ct``
        (and ``class``) for EVERY row immediately, before any trace is
        captured. This is what makes "Resume" correct: re-deriving ``items``
        from a stored seed is NOT reliably reproducible in general (the
        underlying ``pqc-testkit sca gen``'s random/valid-class ciphertexts
        and shuffle order use real OS randomness, not a seeded PRNG -- only
        ``dk`` itself is seed-deterministic; see acquire_cpa.py's
        ``run_campaign`` docstring and dev_regression_test.py, which caught
        this empirically), so the ciphertext a resumed row sends must come
        from what was committed to the file the one time ``items`` existed
        (at creation), never from calling the generator a second time. See
        ``ct_at()``/``cls_at()`` below, which every row -- fresh or resumed
        -- reads from.
    """

    def __init__(self, path: str | pathlib.Path, n_traces: int, n_samples: int, dk: bytes,
                 meta: dict[str, Any], mode: str = "cpa", items: list | None = None):
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
        self._n_written = 0

    def ct_at(self, index: int) -> bytes:
        """This row's ciphertext, as already committed to the file -- the
        one authoritative source both a fresh and a resumed capture loop
        read from (see the class docstring)."""
        return bytes(self.f["intermediates/ct"][index])

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
        caller (``acquire_cpa.run_campaign(..., resume=True)``) is
        responsible for regenerating the exact same ``items`` the file was
        originally planned for (from the ``recipe`` stored in its metadata)
        so that ciphertext-to-row correspondence stays intact.
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

    def set_sample_rate(self, hz: float) -> None:
        if hz > 0:
            self.f["samples"][:] = np.arange(self.n_samples, dtype=np.float64) / hz
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
    def dk_hash(self) -> bytes:
        return bytes(self.f["intermediates/dk_hash"][:])

    @property
    def time_axis(self) -> np.ndarray:
        """``(n_samples,)`` float64 seconds -- 0..n_samples-1 if the sample
        rate was never set (synthetic/older files); for the Raw Trace
        Viewer's x-axis."""
        return self.f["samples"][:]

    @property
    def sample_rate(self) -> float:
        return float(self.f["metadata"].attrs.get("sample_rate", 0.0))

    @property
    def simulated(self) -> bool:
        """True if this file came from SimTarget/a synthetic scope (dry-run)."""
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
            # Resumable: stopped before finishing, with its metadata still
            # holding the deterministic `recipe` needed to regenerate the
            # rest of its ciphertexts (see TraceWriter.open_existing() /
            # acquire_cpa.run_campaign). Older files predating `recipe` just
            # can't be resumed -- start a fresh campaign instead. This is a
            # pure file-mechanics property, independent of `simulated` --
            # the GUI's Acquire Real Traces step additionally refuses to
            # resume a file whose `simulated` is True (continuing a
            # synthetic file with real hardware, or vice versa, would mix
            # fabricated and real rows in one "campaign").
            "resumable": bool(n_planned > r.n_completed and "recipe" in r.f["metadata"].attrs),
            "n_samples": r.f["traces"].shape[1],
            "sample_rate_hz": r.sample_rate,
            "dk_hash": r.dk_hash.hex()[:16] + "...",
            "ok_fraction": float(np.mean(r.ok)) if r.n_completed else 0.0,
            "metadata": r.metadata,
        }
        if r.mode == "tvla" and r.n_completed:
            cls = r.classes
            out["n_fixed"] = int(np.sum(cls == 0))
            out["n_random"] = int(np.sum(cls == 1))
        return out
