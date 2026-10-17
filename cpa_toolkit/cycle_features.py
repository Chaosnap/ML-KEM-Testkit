"""One number per core clock cycle from an oscilloscope trace.

The core toggles ~1400 flops at every edge of its 206.25 MHz clock; a scope at
2.5 GS/s sees that as ~12 samples per cycle. A CPA on the raw samples picks the
single best of those 12, and an EM probe (it measures dI/dt) makes the best one
unpredictable: the per-cycle waveform is a ringing whose sign and phase against
the sample grid depend on the probe, its position and the amplifier. This module
collapses every cycle to one feature, so the cycle -- which is what the RTL
schedule (``mlkem_pipeline.py``) speaks -- is the unit of the analysis:

* ``estimate_phase``: where a cycle starts in the sample grid, from the clock-locked
  part of the across-trace variance (no key, no model needed);
* ``features``: the cycle's samples (linear interpolation at exactly ``M`` points
  per cycle, so the 12.12 samples/cycle of a 2.5 GS/s scope is no problem) reduced
  to one value by

  ``pca``     projection on the dominant fluctuation shape of the window, learned
              from the traces themselves (a matched filter for the probe's response);
  ``energy``  sum of squares (polarity-free, a power proxy);
  ``abs``     sum of absolute values.

The trigger is ``trig_out`` = STATUS.busy, which rises on a core clock edge, so the
phase is the same for every trace; the *integer* number of cycles between that edge
and sample 0 (cable, probe amplifier, trigger path) is not known here and is searched
by the analysis (``cpa_attack``'s cycle-offset search).
"""

from __future__ import annotations

import numpy as np

import config as C

MODES = ("pca", "energy", "abs")
#: Interpolation points per cycle used to build the per-cycle vectors.
POINTS_PER_CYCLE = 12


def samples_per_cycle(rate_hz: float, core_mhz: float = 0.0) -> float:
    """Scope samples per core clock cycle."""
    return rate_hz / ((core_mhz or C.CORE_MHZ) * 1e6)


def estimate_phase(traces: np.ndarray, rate_hz: float, first_cycle: int, n_cycles: int,
                   core_mhz: float = 0.0, bins: int = 60) -> float:
    """Sample position (``0 <= x < samples per cycle``) at which a core clock cycle starts.

    The data-dependent activity is locked to the clock and follows the clock edge, so the
    across-trace *variance* at each sample, folded at the clock period, is high in a burst right
    after the edge and low in between. The cell start is the position whose following half cycle
    holds the most of that variance (of all equally good ones, the earliest: a small margin before
    the edge), which keeps a burst with a sharp rise and a long tail inside one cell.
    """
    tc = samples_per_cycle(rate_hz, core_mhz)
    s0 = max(0, int(first_cycle * tc))
    s1 = min(traces.shape[1], int((first_cycle + n_cycles) * tc))
    if s1 - s0 < 4 * tc:
        raise ValueError("the window (%d samples) is shorter than four clock cycles" % (s1 - s0))
    var = np.asarray(traces[:, s0:s1], dtype=np.float64).var(axis=0)
    ph = (np.arange(s0, s1) % tc) / tc                                   # position within the cycle, [0, 1)
    idx = np.minimum((ph * bins).astype(np.int64), bins - 1)
    prof = np.bincount(idx, weights=var, minlength=bins) / np.maximum(np.bincount(idx, minlength=bins), 1)
    half = bins // 2
    ext = np.concatenate([prof, prof[:half]])
    cum = np.concatenate([[0.0], np.cumsum(ext)])
    after = cum[half:half + bins] - cum[:bins]                           # variance in the half cycle after each start
    good = after >= after.max() - 0.03 * (after.max() - after.min())    # the plateau of equally good starts
    first = int(np.argmax(good & ~np.roll(good, 1))) if not good.all() else int(np.argmax(after))
    return float(first / bins * tc)


def cycle_vectors(traces: np.ndarray, rate_hz: float, first_cycle: int, n_cycles: int, phase: float,
                  points: int = POINTS_PER_CYCLE, core_mhz: float = 0.0) -> np.ndarray:
    """``(N, n_cycles, points)`` float32: the samples of cycles ``first_cycle ...`` (absolute, 0 = the
    trigger edge) by linear interpolation; ``phase`` is where a cycle starts in the sample grid
    (:func:`estimate_phase`). Positions outside the record read as 0."""
    tc = samples_per_cycle(rate_hz, core_mhz)
    pos = phase + (first_cycle + np.arange(n_cycles)[:, None] + (np.arange(points)[None, :] + 0.5) / points) * tc
    i0 = np.floor(pos).astype(np.int64)
    frac = (pos - i0).astype(np.float32)
    n = traces.shape[1]
    ok = (i0 >= 0) & (i0 + 1 < n)
    i0c = np.clip(i0, 0, n - 2)
    x = np.asarray(traces, dtype=np.float32)
    v = x[:, i0c] * (1.0 - frac) + x[:, i0c + 1] * frac
    return np.where(ok[None], v, 0.0).astype(np.float32)


def features(traces: np.ndarray, rate_hz: float, first_cycle: int, n_cycles: int, mode: str = "pca",
             phase: float | None = None, core_mhz: float = 0.0) -> tuple[np.ndarray, dict]:
    """One value per core clock cycle: ``(N, n_cycles)`` float32 and an info dict.

    ``mode``: ``"pca"`` / ``"energy"`` / ``"abs"`` (module docstring). ``phase=None`` estimates
    it from the traces. The info dict has ``phase``, ``samples_per_cycle`` and for ``pca`` the
    learned ``shape`` (the cycle waveform, unit norm) and the ``explained`` variance share.
    """
    if mode not in MODES:
        raise ValueError("unknown feature mode %r (one of %s)" % (mode, ", ".join(MODES)))
    tc = samples_per_cycle(rate_hz, core_mhz)
    if phase is None:
        phase = estimate_phase(traces, rate_hz, first_cycle, n_cycles, core_mhz)
    v = cycle_vectors(traces, rate_hz, first_cycle, n_cycles, phase, core_mhz=core_mhz)
    v -= v.mean(axis=2, keepdims=True)                      # a slow baseline is not switching activity
    info = {"mode": mode, "phase": float(phase), "samples_per_cycle": tc}
    if mode == "energy":
        return (v * v).sum(axis=2), info
    if mode == "abs":
        return np.abs(v).sum(axis=2), info
    n, c, m = v.shape
    d = (v - v.mean(axis=0, keepdims=True)).reshape(n * c, m)
    cov = d.T @ d / d.shape[0]
    w, e = np.linalg.eigh(cov.astype(np.float64))
    shape = e[:, -1].astype(np.float32)
    info["explained"] = float(w[-1] / max(w.sum(), 1e-30))
    p = v @ shape
    # the shape is defined up to sign: orient it so that a larger projection means more activity
    energy = (v * v).sum(axis=2)
    if float(((p - p.mean()) * (energy - energy.mean())).sum()) < 0:
        shape, p = -shape, -p
    info["shape"] = shape.tolist()
    return p.astype(np.float32), info
