#!/usr/bin/env python3
"""TVLA (Test Vector Leakage Assessment) over a real or synthetic HDF5 campaign.

Fixed-vs-random Welch's t-test (Goodwill, Jun, Jaffe, Rohatgi, *A Testing
Methodology for Side-Channel Resistance Validation*, NIST NIAT 2011), the
SAME statistic and threshold as ``pkg/sca/tvla.go: WelchTTest`` /
``scripts/tvla_sim.py`` (simulation-level TVLA) -- this script is their
*measured* counterpart: same test, traces from a real scope (or a synthetic
dry-run) instead of a VCD waveform. See ``docs/TVLA_SIM.md`` for the full
methodology and ``docs/TVLA_SIM.md`` section 10 for how the two relate.

TVLA answers a different question from CPA: not "can an attacker recover
the key" but "does *any* measurable quantity depend on the input at all" --
no key hypothesis, no leakage model, just: are the fixed-class and
random-class trace distributions distinguishable. |t| > 4.5 at a sample
means yes, with > 99.999% confidence at that one sample (multiple-testing
caveats apply -- see docs/TVLA_SIM.md section 1.4).

Usage::

    python tvla_analysis.py FILE.hdf5                  # full report
    python tvla_analysis.py FILE.hdf5 -o report.json     # + JSON dump
    python tvla_analysis.py FILE.hdf5 --csv t.csv         # + per-sample CSV
"""

from __future__ import annotations

import argparse
import json

import numpy as np

from hdf5_trace_store import TraceReader

LEAKAGE_THRESHOLD = 4.5  # pkg/sca/tvla.go: LeakageThreshold


def welch_t(fixed: np.ndarray, random: np.ndarray) -> np.ndarray:
    """Per-sample Welch's t-statistic between two (n, samples) trace sets.

    Identical formula to ``pkg/sca/tvla.go: welchT`` -- unbiased variance
    (ddof=1), ``t = (meanF - meanR) / sqrt(varF/nF + varR/nR)``. Vectorised
    over samples with numpy instead of Go's per-sample loop; same statistic.
    """
    nf, nr = fixed.shape[0], random.shape[0]
    mean_f, mean_r = fixed.mean(axis=0), random.mean(axis=0)
    var_f, var_r = fixed.var(axis=0, ddof=1), random.var(axis=0, ddof=1)
    denom = np.sqrt(var_f / nf + var_r / nr)
    with np.errstate(divide="ignore", invalid="ignore"):
        t = (mean_f - mean_r) / denom
    # denom == 0 (both classes exactly constant at this sample): any mean
    # difference is certain leakage (+-inf), no difference is 0 -- matches
    # pkg/sca/tvla.go's welchT fallback exactly.
    zero_denom = denom == 0
    t = np.where(zero_denom & (mean_f != mean_r), np.sign(mean_f - mean_r) * np.inf, t)
    t = np.where(zero_denom & (mean_f == mean_r), 0.0, t)
    return t


def default_checkpoints(n_max: int) -> list[int]:
    """A handful of roughly log-spaced trace-count checkpoints, up to ``n_max``.

    Same idea as ``cpa_attack.default_checkpoints`` (kept as a separate, tiny
    copy rather than a cross-import: TVLA and CPA checkpointing have no
    other reason to be coupled, and this is three lines).
    """
    fracs = (0.05, 0.1, 0.2, 0.35, 0.5, 0.75, 1.0)
    return sorted({min(n_max, max(4, int(round(n_max * f)))) for f in fracs})


def detection_curve(h5_path: str, max_traces: int = 0, checkpoints: list[int] | None = None) -> dict:
    """max|t| vs. trace count, and "traces to detection" -- the Results
    Viewer's TVLA chart needs this to show when |t| first crosses the
    LEAKAGE_THRESHOLD, not just the final value at the full trace count.

    Recomputes Welch's t at each checkpoint from the same (fixed, random)
    arrays, cheap at TVLA's typical sample counts (no per-coefficient search
    like CPA's --curve, just one t-test per checkpoint).
    """
    with TraceReader(h5_path) as r:
        if r.mode != "tvla":
            raise SystemExit("%s is a %r-mode file, not tvla" % (h5_path, r.mode))
        n_avail = min(r.n_completed, max_traces) if max_traces else r.n_completed
        ok = r.ok[:n_avail]
        traces = r.traces[:n_avail][ok]
        cls = r.classes[:n_avail][ok]
    fixed_all, random_all = traces[cls == 0], traces[cls == 1]
    checkpoints = checkpoints or default_checkpoints(min(len(fixed_all), len(random_all)))
    rows, traces_to_detection = [], None
    for n in checkpoints:
        f, rnd = fixed_all[:n], random_all[:n]
        if len(f) < 2 or len(rnd) < 2:
            continue
        t = welch_t(f, rnd)
        max_abs_t = float(np.max(np.abs(t)))
        rows.append({"n_traces": n, "max_abs_t": max_abs_t,
                     "leakage_detected": max_abs_t > LEAKAGE_THRESHOLD})
        if traces_to_detection is None and max_abs_t > LEAKAGE_THRESHOLD:
            traces_to_detection = n
    return {"checkpoints": rows, "traces_to_detection": traces_to_detection,
            "leakage_threshold": LEAKAGE_THRESHOLD}


def chart_data(h5_path: str, max_traces: int = 0, max_points: int = 2000) -> dict:
    """Everything the Results Viewer's TVLA chart needs in one call: the full
    (optionally decimated) t-curve, the fixed/random mean traces, and the
    detection curve -- all from real per-sample arrays read out of the HDF5
    file, never fabricated (see README.md's "no hardcoded/fake results").

    Decimation is plain strided subsampling (``max_points`` cap), applied
    identically to every series returned so they stay aligned on the same
    (possibly-subsampled) sample-index axis; the chart label says so.
    """
    result = run_tvla(h5_path, max_traces)
    t_values = result.pop("t_values")
    with TraceReader(h5_path) as r:
        n = min(r.n_completed, max_traces) if max_traces else r.n_completed
        ok = r.ok[:n]
        traces = r.traces[:n][ok]
        cls = r.classes[:n][ok]
        sample_rate = r.sample_rate
    fixed_mean = traces[cls == 0].mean(axis=0) if (cls == 0).any() else np.zeros_like(t_values)
    random_mean = traces[cls == 1].mean(axis=0) if (cls == 1).any() else np.zeros_like(t_values)

    n_samples = t_values.shape[0]
    stride = max(1, n_samples // max_points)
    idx = np.arange(0, n_samples, stride)
    decimated = stride > 1

    detection = detection_curve(h5_path, max_traces)
    # +-inf (certain leakage: zero variance in both classes, means differ --
    # see welch_t()'s docstring) has no JSON representation; clamp to a
    # value far outside any real |t| so the chart still shows "pinned off
    # the top/bottom of the plot", which is the correct visual for "infinite
    # confidence", rather than silently dropping the point or erroring.
    result["t_curve"] = [round(float(v), 5) if np.isfinite(v) else (1e9 if v > 0 else -1e9)
                          for v in t_values[idx]]
    result["fixed_mean_curve"] = [round(float(v), 5) for v in fixed_mean[idx]]
    result["random_mean_curve"] = [round(float(v), 5) for v in random_mean[idx]]
    result["sample_indices"] = idx.tolist()
    result["decimated"] = decimated
    result["sample_rate_hz"] = sample_rate
    result["detection_curve"] = detection["checkpoints"]
    result["traces_to_detection"] = detection["traces_to_detection"]
    return result


def run_tvla(h5_path: str, max_traces: int = 0) -> dict:
    with TraceReader(h5_path) as r:
        if r.mode != "tvla":
            raise SystemExit("%s is a %r-mode file, not tvla -- generate one with "
                             "acquire_cpa.py tvla-dry-run / tvla-run" % (h5_path, r.mode))
        n = min(r.n_completed, max_traces) if max_traces else r.n_completed
        ok = r.ok[:n]
        traces = r.traces[:n][ok]
        cls = r.classes[:n][ok]
        simulated = r.simulated
        meta = r.metadata
    if not np.all(ok):
        import sys
        print("note: %d/%d traces failed an integrity check and are excluded"
              % (int((~ok).sum()), n), file=sys.stderr)

    fixed, random = traces[cls == 0], traces[cls == 1]
    if len(fixed) < 2 or len(random) < 2:
        raise SystemExit("need at least 2 traces per class, got %d fixed / %d random"
                         % (len(fixed), len(random)))

    t = welch_t(fixed, random)
    # welch_t() leaves no NaNs (the only 0/0 case is explicitly zeroed), so
    # the only non-finite values possible are +-inf (certain leakage: zero
    # variance in both classes, means differ) -- those must dominate the
    # max, not be excluded from it, matching pkg/sca/tvla.go's semantics.
    abs_t = np.abs(t)
    max_abs_t = float(np.max(abs_t))
    max_abs_t_idx = int(np.argmax(abs_t))
    leakage = bool(max_abs_t > LEAKAGE_THRESHOLD)

    return {
        "file": h5_path, "simulated": simulated, "n_fixed": len(fixed), "n_random": len(random),
        "n_samples": t.shape[0], "max_abs_t": max_abs_t, "max_abs_t_sample": max_abs_t_idx,
        "leakage_threshold": LEAKAGE_THRESHOLD, "leakage_detected": leakage,
        "metadata": meta, "t_values": t,  # t_values kept only in memory for CSV/plot export
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", help="HDF5 campaign file from acquire_cpa.py tvla-dry-run / tvla-run")
    ap.add_argument("--max-traces", type=int, default=0, help="0 = use every trace in the file")
    ap.add_argument("--csv", default="", help="write per-sample t-values to this CSV")
    ap.add_argument("-o", "--out", default="", help="write the JSON summary here")
    args = ap.parse_args()

    result = run_tvla(args.file, args.max_traces)
    t_values = result.pop("t_values")

    print("file:           %s" % result["file"])
    print("simulated:      %s%s" % (result["simulated"],
          "  <-- SOFTWARE VALIDATION ONLY, not a real-hardware result" if result["simulated"] else ""))
    print("traces:         %d fixed / %d random" % (result["n_fixed"], result["n_random"]))
    print("max |t|:        %.2f at sample %d  (threshold %.1f)"
          % (result["max_abs_t"], result["max_abs_t_sample"], result["leakage_threshold"]))
    print("leakage:        %s" % ("DETECTED" if result["leakage_detected"] else "not detected"))
    if result["leakage_detected"]:
        print("\n=> fixed-class and random-class traces are statistically distinguishable: "
              "the core's power consumption depends on the input. This says *that* it leaks, "
              "not *what* an attacker could recover from it -- see cpa_attack.py for key recovery.")
    else:
        print("\n=> no leakage detected at this trace count/threshold. This does NOT prove the "
              "core is safe -- more traces, a different window, or a different measurement "
              "setup could still find something (see README.md section 6 for the same caveats "
              "that apply to CPA results).")

    if args.csv:
        with open(args.csv, "w") as f:
            f.write("sample,t\n")
            for i, v in enumerate(t_values):
                f.write("%d,%.6f\n" % (i, v))
        print("\nper-sample t-values: %s" % args.csv)

    if args.out:
        with open(args.out, "w") as f:
            json.dump(result, f, indent=2, default=lambda o: int(o) if isinstance(o, np.integer) else str(o))
        print("full report: %s" % args.out)


if __name__ == "__main__":
    main()
