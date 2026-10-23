"""Parse captured traces: find where the Decaps starts in every trace.

The BMUL windows sit at fixed *core cycles* after the Decaps start
(config.BMUL_WINDOWS). Where that start lands in a trace is not known from the
trigger setting alone: the record start, the trigger jitter, the probe delay and
the real core clock all move it. So the start is *measured* in every trace, from
the trig_out marker channel the scope records next to the power channel
(trig_out = STATUS.busy: high from the first to the last cycle of the Decaps):

* the marker is digitised at the midpoint of its low / high levels (5th and
  99.5th percentile; a short moving average rejects ringing, as in the bench
  scripts of sakura_trace_example/);
* the first rising edge is the Decaps start of that trace (``rise``); the first
  falling edge after it, when the record reaches it, is the end (``fall``);
* with ``fall`` and CYCLE_COUNT known, ``(fall - rise) / CYCLE_COUNT`` is the
  measured number of samples per core cycle -- the real clock, not the nominal
  206.25 MHz. Otherwise the nominal clock is used and the result says so.

A BMUL window of a trace is then ``rise + cycle * samples_per_cycle``: every
trace is cut at its own start (alignment), and the GUI draws windows only for
parsed traces. Without a marker channel, ``pattern_parse`` aligns the traces to
each other by cross-correlation with a reference trace (relative alignment; the
absolute start stays the trigger position).

Results are stored with the traces: in a campaign file as the ``/parse`` group;
for any other H5 file as a ``<file>.parse.npz`` sidecar (``save`` / ``load``).

``describe_h5`` / ``read_rows`` open any H5 file with 2-D trace datasets -- the
toolkit's campaigns (``/traces``, ``/marker``) and per-channel capture folders
(``C2_traces.h5`` + ``C4_traces.h5`` with ``trace_data_set``).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

import config as C

#: Moving-average length (samples) applied to the marker only, against ringing.
DEFAULT_SMOOTH = 9
#: The measured clock may differ this much from config.CORE_MHZ before it is rejected
#: (a wrong CYCLE_COUNT or a misread edge, not a real clock).
CLOCK_TOLERANCE = 0.05
#: Minimum low-to-high span of a marker, as a fraction of its full range, to count as a logic signal.
MIN_SPAN_FRACTION = 0.25
#: A marker's logic swing must be this many times its sample-to-sample noise.
MIN_SNR = 10.0
#: More rising edges than this in one record is noise, not trig_out (it rises once per Decaps).
MAX_EDGES = 20


# --------------------------------------------------------------------------- one marker waveform
def digitise(marker: np.ndarray, smooth: int = DEFAULT_SMOOTH, threshold: float | None = None) -> dict:
    """Logic levels, threshold and the boolean high/low state of one marker waveform."""
    m = np.asarray(marker, dtype=np.float64).reshape(-1)
    if m.size < 4 or not np.isfinite(m).all():
        raise ValueError("the marker waveform is empty or not finite")
    low, high = np.percentile(m, [5, 99.5])
    full = float(m.max() - m.min())
    if high - low < MIN_SPAN_FRACTION * full:      # a pulse shorter than 0.5 % of the record
        low, high = np.percentile(m, [0.5, 100])
    if threshold is None:
        threshold = (low + high) / 2
    # sample-to-sample noise (robust): a logic marker spans many times its noise, plain noise does not
    noise = float(np.median(np.abs(np.diff(m)))) / 0.6745 / np.sqrt(2)
    if high - low < max(1e-9, MIN_SNR * noise):
        return {"low": float(low), "high": float(high), "threshold": float(threshold), "state": None}
    k = max(1, int(smooth))
    sm = m
    if k > 1:
        sm = np.convolve(m, np.ones(k) / k, mode="same")
        h = k // 2                                  # the zero padding pulls both ends to 0: keep them raw
        sm[:h], sm[m.size - h:] = m[:h], m[m.size - h:]
    return {"low": float(low), "high": float(high), "threshold": float(threshold), "state": sm >= threshold}


def parse_marker(marker: np.ndarray, smooth: int = DEFAULT_SMOOTH, threshold: float | None = None,
                 max_edges: int = MAX_EDGES) -> dict:
    """Decaps start / end of one trace from its marker waveform.

    Returns ``{rise, fall, ok, reason, low, high, threshold}``; ``fall`` is -1 when the
    record ends before the pulse does (the usual case: the Decaps is ~128 us long).
    """
    d = digitise(marker, smooth, threshold)
    out = {"rise": -1, "fall": -1, "ok": False, "reason": "", "low": d["low"], "high": d["high"],
           "threshold": d["threshold"]}
    st = d["state"]
    if st is None:
        out["reason"] = "the marker does not change (no probe, or it is high or low for the whole record)"
        return out
    if st[0]:
        out["reason"] = "marker is high at sample 0: the record starts after the Decaps start"
        return out
    rises = np.flatnonzero(~st[:-1] & st[1:]) + 1
    if rises.size > max_edges:
        out["reason"] = "noisy marker: %d rising edges (no probe on trig_out?)" % rises.size
        return out
    if not rises.size:
        out["reason"] = "no rising edge in the record"
        return out
    rise = int(rises[0])
    falls = np.flatnonzero(st[:-1] & ~st[1:]) + 1
    falls = falls[falls > rise]
    out.update(rise=rise, fall=int(falls[0]) if falls.size else -1, ok=True)
    return out


# --------------------------------------------------------------------------- many traces
def summarise(rise: np.ndarray, fall: np.ndarray, ok: np.ndarray, cycles: np.ndarray | None,
              rate: float, core_mhz: float = C.CORE_MHZ) -> dict:
    """Samples per cycle (measured when possible), clock and jitter of a parsed set."""
    rise, fall, ok = np.asarray(rise), np.asarray(fall), np.asarray(ok, dtype=bool)
    nominal = rate / (core_mhz * 1e6) if rate > 0 else 1.0
    spc, source, clock = nominal, "nominal", core_mhz
    if cycles is not None:
        cyc = np.asarray(cycles, dtype=np.float64)
        sel = ok & (fall > rise) & (cyc > 0)
        if sel.sum():
            meas = float(np.median((fall[sel] - rise[sel]) / cyc[sel]))
            if abs(meas / nominal - 1) <= CLOCK_TOLERANCE or rate <= 0:
                spc, source = meas, "measured"
                clock = rate / meas / 1e6 if rate > 0 else core_mhz
    r = rise[ok]
    return {"n": int(rise.size), "n_ok": int(ok.sum()), "samples_per_cycle": float(spc), "spc_source": source,
            "clock_mhz": float(clock), "nominal_clock_mhz": float(core_mhz),
            "rise_median": int(np.median(r)) if r.size else -1,
            "rise_min": int(r.min()) if r.size else -1, "rise_max": int(r.max()) if r.size else -1,
            "rise_std": float(r.std()) if r.size else 0.0,
            "n_fall": int((ok & (fall > rise)).sum()),
            "width_median": int(np.median((fall - rise)[ok & (fall > rise)])) if (ok & (fall > rise)).any() else -1}


def parse_markers(markers, cycles=None, rate: float = 0.0, smooth: int = DEFAULT_SMOOTH,
                  threshold: float | None = None, progress_cb=None, n: int | None = None) -> dict:
    """Parse the first ``n`` (default all) rows of ``markers`` (an ``(N, S)`` array or an
    h5py dataset, read in blocks)."""
    n = int(markers.shape[0]) if n is None else min(int(n), int(markers.shape[0]))
    rise, fall = np.full(n, -1, dtype=np.int64), np.full(n, -1, dtype=np.int64)
    ok = np.zeros(n, dtype=bool)
    reasons: dict[str, int] = {}
    levels = []
    step = 256
    for s in range(0, n, step):
        block = np.asarray(markers[s:min(n, s + step)], dtype=np.float64)
        for k, row in enumerate(block):
            p = parse_marker(row, smooth, threshold)
            i = s + k
            rise[i], fall[i], ok[i] = p["rise"], p["fall"], p["ok"]
            if not p["ok"]:
                reasons[p["reason"]] = reasons.get(p["reason"], 0) + 1
            elif len(levels) < 64:
                levels.append((p["low"], p["high"], p["threshold"]))
        if progress_cb:
            progress_cb(min(n, s + step), n)
    out = {"method": "marker", "rise": rise, "fall": fall, "ok": ok, "smooth": int(smooth),
           "threshold": None if threshold is None else float(threshold),
           "failures": reasons, "sample_rate_hz": float(rate), "created_unix": time.time()}
    if levels:
        lv = np.median(np.asarray(levels), axis=0)
        out["levels"] = {"low": float(lv[0]), "high": float(lv[1]), "threshold": float(lv[2])}
    out.update(summarise(rise, fall, ok, cycles, rate))
    return out


def pattern_parse(traces, reference: int = 0, window: tuple[int, int] | None = None, max_shift: int = 200,
                  trigger_sample: int = 0, rate: float = 0.0, progress_cb=None) -> dict:
    """Relative alignment without a marker: the shift of every trace against one reference
    trace that maximises their correlation inside ``window`` (samples).

    ``rise`` becomes ``trigger_sample + shift``: the traces line up with each other, but
    the absolute Decaps start is only as good as the trigger position.
    """
    n, s = int(traces.shape[0]), int(traces.shape[1])
    a, b = window if window else (max_shift, min(s - max_shift, max_shift + 4000))
    a, b = max(max_shift, int(a)), min(s - max_shift, int(b))
    if b - a < 16:
        raise ValueError("the pattern window is too short for this record (%d samples)" % s)
    ref = np.asarray(traces[reference], dtype=np.float64)[a:b]
    ref = (ref - ref.mean()) / (ref.std() or 1.0)
    shifts = np.zeros(n, dtype=np.int64)
    score = np.zeros(n)
    lags = np.arange(-max_shift, max_shift + 1)
    step = 128
    for s0 in range(0, n, step):
        block = np.asarray(traces[s0:s0 + step], dtype=np.float64)
        for k, row in enumerate(block):
            best, best_l = -2.0, 0
            for lag in lags[:: max(1, max_shift // 50)]:          # coarse, then fine around the best
                seg = row[a + lag:b + lag]
                c = float(np.dot((seg - seg.mean()) / (seg.std() or 1.0), ref)) / ref.size
                if c > best:
                    best, best_l = c, int(lag)
            for lag in range(max(-max_shift, best_l - 50), min(max_shift, best_l + 50) + 1):
                seg = row[a + lag:b + lag]
                c = float(np.dot((seg - seg.mean()) / (seg.std() or 1.0), ref)) / ref.size
                if c > best:
                    best, best_l = c, lag
            shifts[s0 + k], score[s0 + k] = best_l, best
        if progress_cb:
            progress_cb(min(n, s0 + step), n)
    ok = score > 0.5
    rise = trigger_sample + shifts
    out = {"method": "pattern", "rise": rise, "fall": np.full(n, -1, dtype=np.int64), "ok": ok,
           "score": score, "window": [int(a), int(b)], "reference": int(reference), "max_shift": int(max_shift),
           "sample_rate_hz": float(rate), "created_unix": time.time(),
           "failures": {"correlation below 0.5": int((~ok).sum())} if (~ok).any() else {}}
    out.update(summarise(rise, out["fall"], ok, None, rate))
    return out


# --------------------------------------------------------------------------- windows from a parse
def cycle_offset(cycle: float, spc: float) -> int:
    """Samples from the Decaps start to core cycle ``cycle``."""
    return int(round(cycle * spc))


def windows_for(rise: int, spc: float, n_samples: int, slack: int = C.TRIGGER_SLACK_CYCLES) -> list[tuple[int, int]]:
    """BMUL windows (with slack) of a trace whose Decaps starts at sample ``rise``, clipped."""
    out = []
    for c0, c1 in C.BMUL_WINDOWS:
        s0 = min(n_samples, max(0, rise + cycle_offset(c0 - slack, spc)))
        s1 = min(n_samples, max(0, rise + cycle_offset(c1 + slack, spc)))
        out.append((s0, max(s0, s1)))
    return out


def aligned_window(traces: np.ndarray, rise: np.ndarray, ok: np.ndarray, spc: float, c0: float, c1: float):
    """The same core-cycle span ``c0 .. c1`` cut out of every trace at its own Decaps start.

    Returns ``(matrix, rows)``: ``matrix`` ``(len(rows), W)`` float32 and the row indices
    that were usable (parsed and the span inside the record).
    """
    n, s = traces.shape
    off0, width = cycle_offset(c0, spc), max(1, cycle_offset(c1, spc) - cycle_offset(c0, spc))
    start = np.asarray(rise, dtype=np.int64) + off0
    rows = np.flatnonzero(np.asarray(ok, dtype=bool) & (start >= 0) & (start + width <= s))
    if not rows.size:
        return np.zeros((0, width), dtype=np.float32), rows
    idx = start[rows, None] + np.arange(width)[None, :]
    return np.take_along_axis(np.asarray(traces)[rows], idx, axis=1).astype(np.float32), rows


def aligned_traces(traces: np.ndarray, rise: np.ndarray, ok: np.ndarray):
    """Whole traces shifted so every Decaps start sits at the same index.

    Returns ``(matrix, rows, t0)``: the common span of all usable rows, and the index of
    the Decaps start in it.
    """
    rise = np.asarray(rise, dtype=np.int64)
    rows = np.flatnonzero(np.asarray(ok, dtype=bool) & (rise >= 0))
    if not rows.size:
        return np.zeros((0, 0), dtype=np.float32), rows, 0
    s = traces.shape[1]
    pre = int(rise[rows].min())
    post = int((s - rise[rows]).min())
    idx = (rise[rows] - pre)[:, None] + np.arange(pre + post)[None, :]
    return np.take_along_axis(np.asarray(traces)[rows], idx, axis=1).astype(np.float32), rows, pre


# --------------------------------------------------------------------------- storage
_ARRAYS = ("rise", "fall", "ok", "score")


def sidecar(path) -> Path:
    return Path(str(path) + ".parse.npz")


def _is_campaign(f) -> bool:
    return "metadata" in f and "traces" in f


def save(path, result: dict) -> str:
    """Store a parse result with the traces it belongs to; returns where."""
    import h5py
    path = Path(path)
    scalars = {k: v for k, v in result.items() if k not in _ARRAYS}
    try:
        with h5py.File(str(path), "r+") as f:
            if _is_campaign(f):
                if "parse" in f:
                    del f["parse"]
                g = f.create_group("parse")
                for k in _ARRAYS:
                    if k in result:
                        g.create_dataset(k, data=np.asarray(result[k]))
                g.attrs["json"] = json.dumps(scalars, default=float)
                return "%s:/parse" % path.name
    except OSError:
        pass                                   # read-only or open elsewhere: use the sidecar
    np.savez(sidecar(path), **{k: np.asarray(result[k]) for k in _ARRAYS if k in result},
             json=np.array(json.dumps(scalars, default=float)))
    return sidecar(path).name


def load(path) -> dict | None:
    """The stored parse result of a trace file, or None."""
    import h5py
    path = Path(path)
    try:
        with h5py.File(str(path), "r") as f:
            if "parse" in f and "json" in f["parse"].attrs:
                g = f["parse"]
                out = json.loads(str(g.attrs["json"]))
                for k in _ARRAYS:
                    if k in g:
                        out[k] = g[k][:]
                return out
    except OSError:
        return None
    sc = sidecar(path)
    if sc.is_file():
        with np.load(sc, allow_pickle=False) as z:
            out = json.loads(str(z["json"]))
            for k in _ARRAYS:
                if k in z:
                    out[k] = z[k]
        return out
    return None


def public(result: dict | None) -> dict | None:
    """A parse result without its per-trace arrays (for JSON)."""
    if not result:
        return None
    return {k: v for k, v in result.items() if k not in _ARRAYS}


# --------------------------------------------------------------------------- any H5 file
def _datasets(f) -> list[dict]:
    import h5py
    out = []

    def visit(name, obj):
        if isinstance(obj, h5py.Dataset) and obj.ndim == 2 and obj.shape[0] >= 1 and obj.shape[1] >= 64 \
                and obj.dtype.kind in "fiu" and not name.startswith("parse"):
            out.append({"name": name, "shape": [int(x) for x in obj.shape], "dtype": str(obj.dtype)})
    f.visititems(visit)
    # trace datasets first: by name, then by row length
    return sorted(out, key=lambda d: ("trace" not in d["name"].lower(), -d["shape"][1]))


def _attr(v):
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    if isinstance(v, np.generic):
        return v.item()
    if isinstance(v, np.ndarray):
        return v.tolist() if v.size <= 16 else "array %s" % (v.shape,)
    return v if isinstance(v, (str, int, float, bool)) or v is None else str(v)


def describe_h5(path) -> dict:
    """What a trace H5 file holds: trace datasets, rows, sample rate, likely power / marker."""
    import h5py
    path = Path(path)
    with h5py.File(str(path), "r") as f:
        campaign = _is_campaign(f)
        sets = _datasets(f)
        attrs = {k: _attr(v) for k, v in f.attrs.items()}
        rate = 0.0
        n_rows = None
        if campaign:
            md = f["metadata"].attrs
            rate = float(md.get("sample_rate", 0.0) or 0.0)
            n_rows = int(md.get("n_completed", f["traces"].shape[0]))
            attrs.update({k: _attr(v) for k, v in md.items() if k not in ("scope", "board", "recipe")})
        else:
            for key in ("sample_rate_hz", "saved_sample_rate_hz", "sample_rate"):
                if key in f.attrs:
                    rate = float(f.attrs[key])
                    break
            if not rate and "sample_rate_hz" in f and f["sample_rate_hz"].ndim == 1 and f["sample_rate_hz"].size:
                rate = float(f["sample_rate_hz"][0])
            if "committed_rows" in f.attrs:
                n_rows = int(f.attrs["committed_rows"])
    power = "traces" if campaign else (sets[0]["name"] if sets else None)
    marker = "marker" if campaign and any(d["name"] == "marker" for d in sets) else None
    return {"path": str(path), "name": path.name, "kind": "campaign" if campaign else "external",
            "datasets": sets, "power": power, "marker": marker, "sample_rate_hz": rate, "n_rows": n_rows,
            "attrs": attrs, "siblings": siblings(path), "parse": public(load(path))}


def siblings(path) -> list[dict]:
    """Other H5 files in the same folder (per-channel capture folders: C2 power, C4 marker)."""
    import h5py
    path = Path(path)
    out = []
    for p in sorted(path.parent.glob("*.h5")) + sorted(path.parent.glob("*.hdf5")):
        if p.resolve() == path.resolve():
            continue
        try:
            with h5py.File(str(p), "r") as f:
                sets = _datasets(f)
                ch = _attr(f.attrs.get("capture_channel", "")) if "capture_channel" in f.attrs else ""
        except OSError:
            continue
        if sets:
            out.append({"path": str(p), "name": p.name, "channel": ch, "datasets": sets})
    return out


def open_rows(path, dataset: str):
    """``(h5py.File, dataset)`` -- the caller closes the file."""
    import h5py
    f = h5py.File(str(path), "r")
    if dataset not in f:
        f.close()
        raise ValueError("%s has no dataset %s" % (Path(path).name, dataset))
    return f, f[dataset]


def rows_available(path, dataset: str) -> int:
    import h5py
    with h5py.File(str(path), "r") as f:
        n = int(f[dataset].shape[0])
        if _is_campaign(f):
            n = min(n, int(f["metadata"].attrs.get("n_completed", n)))
        elif "committed_rows" in f.attrs:
            n = min(n, int(f.attrs["committed_rows"]))
    return n


def scan(folder, limit: int = 400) -> list[dict]:
    """H5 trace files under ``folder`` (two levels deep), newest first."""
    folder = Path(folder).expanduser()
    if not folder.is_dir():
        raise ValueError("no such folder: %s" % folder)
    found = []
    for pattern in ("*.h5", "*.hdf5", "*/*.h5", "*/*.hdf5", "*/*/*.h5", "*/*/*.hdf5"):
        for p in folder.glob(pattern):
            if ".trash" in p.parts:
                continue
            found.append(p)
    found = sorted(set(found), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    return [{"path": str(p), "name": p.name, "folder": str(p.parent.relative_to(folder)) if p.parent != folder else "",
             "size_bytes": p.stat().st_size, "mtime": p.stat().st_mtime} for p in found]
