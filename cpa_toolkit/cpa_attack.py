#!/usr/bin/env python3
"""CPA attack against ML-KEM-768 BaseMul in K-PKE.Decrypt.

Leakage model: ``HD(R_prev, BaseMul(u_hat, s_hat_guess) mod q)`` -- see
``mlkem_ref.py: PairModel`` for the exact hypothesis construction and
``docs/ATTACK_MODEL.md`` for the registers this targets in
``hdl/core/mlkem_poly_alu.sv`` (P1/P2 there; this script attacks the
accumulated-word register, "P2/acc" + "word" write-back, since that is
what a real probe sees -- the 24-bit RAM word written back each BMUL
cycle). The attacker knows every ciphertext ``c`` (hence ``u_hat``, public)
and recovers ``s_hat`` (the decapsulation key) one 12-bit coefficient pair
at a time, each independent of the other 127 under this model.

Usage::

    python cpa_attack.py FILE.hdf5                              # unknown-key (the real attack)
    python cpa_attack.py FILE.hdf5 --dk-ref REF.bin               # oracle-assisted evaluation
    python cpa_attack.py FILE.hdf5 --dk-ref REF.bin --curve         # + key-rank-vs-N, MTD

``--dk-ref`` is **oracle-assisted**: it scores the attack's own guesses
against the campaign's real key (``testvectors.py export <set> --format dk``:
the vector set a campaign was captured with holds its key pair), purely for
evaluation/reporting. The
guesses themselves are never constructed with it -- see
``mlkem_ref.py: PairModel`` and ``docs/ATTACK_MODEL.md`` section 5.4: without
``--dk-ref`` every hypothesis is built only from already-recovered guesses
(``recovered``, updated in place as coefficients are attacked) and the
known, public ciphertext-derived ``u_hat``; the true secret coefficients are
never read. Every printed/JSON result says plainly whether it is
oracle-assisted; an oracle-assisted run is not a blind attack and must not
be reported as one.

Without ``--dk-ref`` the script still runs the full attack and reports its
own best guess per coefficient plus the correlation peaks, so you can judge
"is there a confident recovery" without a ground truth -- this is the actual
attack a real adversary would run.
"""

from __future__ import annotations

import argparse
import json
import sys

import numpy as np

import config as C
import mlkem_ref as R
from hdf5_trace_store import TraceReader

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    HAVE_MPL = True
except ImportError:
    HAVE_MPL = False


def pearson_matrix(hyp: np.ndarray, traces: np.ndarray) -> np.ndarray:
    """rho[g, t] for hypotheses ``(N, G)`` and traces ``(N, T)``.

    Standard CPA correlation (Brier, Clavier, Olivier, CHES 2004): both
    sides standardized, then one matrix product -- O(N*G*T), the only
    acceptable cost at G=4096 candidates x thousands of samples.
    """
    n = hyp.shape[0]
    h = (hyp - hyp.mean(axis=0, keepdims=True)) / (hyp.std(axis=0, keepdims=True) + 1e-12)
    t = (traces - traces.mean(axis=0, keepdims=True)) / (traces.std(axis=0, keepdims=True) + 1e-12)
    return (h.T @ t) / n


def attack_coefficient(model: R.PairModel, target: str, rprev: str, traces: np.ndarray,
                        candidates: np.ndarray) -> dict:
    """CPA over one secret coefficient; returns peak correlation per candidate."""
    hyp = model.hypotheses(target, rprev, candidates)  # (N, G)
    corr = pearson_matrix(hyp, traces)                  # (G, T)
    peak_per_cand = np.max(np.abs(corr), axis=1)
    peak_sample = np.argmax(np.abs(corr), axis=1)
    order = np.argsort(peak_per_cand)[::-1]
    true_rank = None
    if model.true_value in candidates:
        g_idx = int(np.where(candidates == model.true_value)[0][0])
        true_rank = int(np.where(order == g_idx)[0][0]) + 1
    return {
        "poly": model.poly, "coeff": model.j, "target": target, "rprev": rprev,
        "best_guess": int(candidates[order[0]]),
        "best_corr": float(peak_per_cand[order[0]]),
        "best_sample": int(peak_sample[order[0]]),
        "true_value": model.true_value, "true_rank": true_rank,
        "true_corr": float(peak_per_cand[int(np.where(candidates == model.true_value)[0][0])])
                     if model.true_value in candidates else None,
        "corr_curve": corr[order[0]],  # kept only in memory for plotting; not JSON-serialised
        "candidates": candidates, "peak_per_cand": peak_per_cand,
    }


def _require_cpa(r) -> None:
    """CPA needs distinct ciphertexts; a TVLA campaign repeats one for half its rows."""
    if r.mode != "cpa":
        raise SystemExit("%s is a %s campaign -- CPA needs a CPA campaign; use tvla_analysis.py "
                         "(or the GUI's Run TVLA) for this file" % (r.path.name, r.mode.upper()))


def run_attack(h5_path: str, target: str, rprev: str, max_traces: int, plot_dir: str | None,
                dk_ref: bytes | None) -> dict:
    with TraceReader(h5_path) as r:
        _require_cpa(r)
        n = min(r.n_completed, max_traces) if max_traces else r.n_completed
        ok = r.ok[:n]
        if not np.all(ok):
            print("note: %d/%d traces failed an integrity check and are excluded"
                  % (int((~ok).sum()), n), file=sys.stderr)
        traces_full = r.traces[:n][ok]
        cts = r.ciphertexts[:n][ok]
        meta = r.metadata
        dk_hash_in_file = r.dk_hash
        sample_rate = r.sample_rate
    n_ok = traces_full.shape[0]
    if n_ok < 20:
        raise SystemExit("only %d usable traces; CPA on a single coefficient typically needs "
                         "hundreds to tens of thousands depending on SNR -- capture more" % n_ok)

    if dk_ref is not None:
        import hashlib
        if hashlib.sha3_256(dk_ref).digest() != dk_hash_in_file:
            raise SystemExit("--dk-ref does not match the key this file was captured under "
                             "(SHA3-256(dk) mismatch) -- wrong file or wrong key")
        s_hat = R.s_hat_from_dk(dk_ref)
    else:
        s_hat = None

    u_hat = R.u_hat_from_ct(cts)  # (n_ok, 3, 256)
    bmul_windows = json.loads(meta.get("bmul_windows_cycles", "null"))

    results = []
    recovered = np.zeros((3, 256), dtype=np.int64)
    for poly in range(R.K):
        # Known-key evaluation uses the true s_hat of the OTHER 255
        # coefficients (see mlkem_ref.PairModel docstring); for a from-
        # scratch attack with no dk-ref, substitute the already-recovered
        # guesses so far, in ascending coefficient order, exactly as a real
        # sequential CPA on this target would have to.
        traces = slice_window(traces_full, poly, bmul_windows, sample_rate)
        # `recovered` (not a copy) so that PairModel.s, a view into it, picks
        # up each coefficient's guess as soon as it is written below -- the
        # second coefficient of a pair (w, e=1) then sees the first one's
        # just-made guess instead of a stale 0. The first coefficient of
        # each pair still has no better guess for its partner than 0: that
        # chicken-and-egg weakness is inherent to guessing one coefficient
        # at a time and is not fixed by this (see README caveats).
        model_src = s_hat if s_hat is not None else recovered
        model = R.PairModel(u_hat, model_src, poly, 0)  # j reset per coefficient below
        # Candidates are coefficient VALUES mod q (0..3328, canonical ML-KEM
        # range), never a function of the register's bit width -- TARGETS[target]
        # is only the width used inside hamming_distance() to score R_prev vs X.
        candidates = np.arange(R.Q, dtype=np.int64)
        for j in range(256):
            model.j, model.w, model.e = j, j >> 1, j & 1
            model.true_value = int(s_hat[poly][j]) if s_hat is not None else -1
            res = attack_coefficient(model, target, rprev, traces, candidates)
            recovered[poly, j] = res["best_guess"]
            results.append({k: v for k, v in res.items() if k not in ("corr_curve", "candidates", "peak_per_cand")})
            if plot_dir and j < 4:  # a handful of example plots, not all 256*3
                plot_coefficient(res, poly, j, plot_dir)

    n_scored = sum(1 for r_ in results if r_["true_rank"] is not None)
    summary = {
        "file": h5_path, "n_traces_used": n_ok, "target": target, "rprev": rprev,
        "oracle_assisted": s_hat is not None,  # True <=> --dk-ref given; see module docstring
        "known_key_eval": s_hat is not None,  # deprecated alias, kept for older report consumers
        "coefficients_attacked": len(results),
        "results": results,
    }
    if n_scored:
        ranks = [r_["true_rank"] for r_ in results if r_["true_rank"] is not None]
        corrs = [r_["true_corr"] for r_ in results if r_["true_corr"] is not None]
        summary["rank1_fraction"] = sum(1 for rk in ranks if rk == 1) / len(ranks)
        summary["median_true_rank"] = float(np.median(ranks))
        summary["mean_true_corr"] = float(np.mean(corrs))
        summary["n_candidates"] = int(R.Q)
        summary["chance_rank_mean"] = float(R.Q / 2)
    return summary


def correlation_curve_for_coefficient(h5_path: str, target: str, rprev: str, poly: int, coeff: int,
                                       max_traces: int, dk_ref: bytes | None) -> dict:
    """Full per-sample correlation curve for ONE coefficient -- for the GUI's
    "Correlation vs. Time" chart. Fast (one coefficient, not all 768): this
    is a preview of a single guess in isolation, not a step of the
    sequential attack, so its pair-partner/accumulator is modelled as 0
    when no --dk-ref is given (same convention a from-scratch attack would
    use for the very first coefficient of a pair -- see PairModel docstring
    and cpa_toolkit/README.md section 7 point 4 on the "chicken-and-egg" limitation).
    """
    if not 0 <= poly < R.K:
        raise ValueError("poly must be 0..%d" % (R.K - 1))
    if not 0 <= coeff < 256:
        raise ValueError("coeff must be 0..255")
    with TraceReader(h5_path) as r:
        _require_cpa(r)
        n = min(r.n_completed, max_traces) if max_traces else r.n_completed
        ok = r.ok[:n]
        traces_full = r.traces[:n][ok]
        cts = r.ciphertexts[:n][ok]
        meta = r.metadata
        dk_hash_in_file = r.dk_hash
        sample_rate = r.sample_rate
    n_ok = traces_full.shape[0]
    if n_ok < 20:
        raise SystemExit("only %d usable traces; need at least 20" % n_ok)

    s_hat = None
    if dk_ref is not None:
        import hashlib
        if hashlib.sha3_256(dk_ref).digest() != dk_hash_in_file:
            raise SystemExit("--dk-ref does not match the key this file was captured under")
        s_hat = R.s_hat_from_dk(dk_ref)

    u_hat = R.u_hat_from_ct(cts)
    bmul_windows = json.loads(meta.get("bmul_windows_cycles", "null"))
    traces = slice_window(traces_full, poly, bmul_windows, sample_rate)

    model_src = s_hat if s_hat is not None else np.zeros((R.K, 256), dtype=np.int64)
    model = R.PairModel(u_hat, model_src, poly, coeff)
    candidates = np.arange(R.Q, dtype=np.int64)
    hyp = model.hypotheses(target, rprev, candidates)
    corr = pearson_matrix(hyp, traces)                 # (Q, T)
    peak_per_cand = np.max(np.abs(corr), axis=1)
    best_idx = int(np.argmax(peak_per_cand))

    out = {
        "poly": poly, "coeff": coeff, "target": target, "rprev": rprev,
        "n_traces": n_ok, "n_samples": int(traces.shape[1]),
        "best_guess": int(candidates[best_idx]),
        "best_corr": float(peak_per_cand[best_idx]),
        "best_curve": [round(float(v), 5) for v in corr[best_idx]],
        "oracle_assisted": s_hat is not None,
    }
    if s_hat is not None:
        true_idx = int(model.true_value)  # candidates[i] == i, so the value IS the index
        out["true_value"] = true_idx
        out["true_rank"] = int(np.where(np.argsort(peak_per_cand)[::-1] == true_idx)[0][0]) + 1
        out["true_curve"] = [round(float(v), 5) for v in corr[true_idx]]
    return out


def default_checkpoints(n_max: int) -> list[int]:
    """A handful of trace-count checkpoints for --curve, roughly log-spaced."""
    fracs = (0.05, 0.1, 0.2, 0.35, 0.5, 0.75, 1.0)
    pts = sorted({min(n_max, max(20, int(round(n_max * f)))) for f in fracs})
    return pts


def run_curve(h5_path: str, target: str, rprev: str, max_traces: int, dk_ref: bytes,
              checkpoints: list[int] | None = None) -> dict:
    """Key rank / success rate vs. trace count, oracle-assisted only.

    Re-runs run_attack() at each checkpoint (recomputing the correlation at
    that trace count -- there is no shortcut that reuses work across
    checkpoints without a much larger rewrite, and this is an offline
    analysis tool, not a real-time one). MTD ("minimum traces to
    disclosure") is the smallest checkpoint at which more than half the
    attacked coefficients rank the true value first; ``None`` if that never
    happens within ``max_traces``. Needs --dk-ref: there is no way to know
    when the attack has "succeeded" without a ground truth to check against.
    """
    with TraceReader(h5_path) as r:
        _require_cpa(r)
        n_avail = min(r.n_completed, max_traces) if max_traces else r.n_completed
    checkpoints = checkpoints or default_checkpoints(n_avail)
    rows, mtd = [], None
    for n in checkpoints:
        s = run_attack(h5_path, target, rprev, n, None, dk_ref)
        row = {
            "n_traces": s["n_traces_used"],
            "rank1_fraction": s.get("rank1_fraction", 0.0),
            "median_rank": s.get("median_true_rank", float("nan")),
            "mean_true_corr": s.get("mean_true_corr", float("nan")),
        }
        rows.append(row)
        if mtd is None and row["rank1_fraction"] > 0.5:
            mtd = row["n_traces"]
    return {"oracle_assisted": True, "target": target, "rprev": rprev, "checkpoints": rows, "mtd": mtd}


def write_results_csv(results: list[dict], path: str) -> None:
    import csv
    fields = ["poly", "coeff", "best_guess", "best_corr", "best_sample",
              "true_value", "true_rank", "true_corr"]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in results:
            w.writerow({k: row.get(k) for k in fields})


def write_curve_csv(curve: dict, path: str) -> None:
    import csv
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["n_traces", "rank1_fraction", "median_rank", "mean_true_corr"])
        w.writeheader()
        for row in curve["checkpoints"]:
            w.writerow(row)


def slice_window(traces: np.ndarray, poly: int, bmul_windows, sample_rate: float) -> np.ndarray:
    """Cut the samples belonging to BMUL ``poly`` out of each full trace.

    If ``bmul_windows`` metadata is missing (older file), falls back to the
    whole trace -- slower and noisier, but still correct: BaseMul's own
    samples dominate variance only inside the true window, so CPA run on
    the full trace mainly costs sensitivity, not correctness.
    """
    if not bmul_windows:
        return traces
    import config as C
    sr = sample_rate if sample_rate > 0 else C.CORE_MHZ * 1e6
    c0, c1 = bmul_windows[poly]
    s0 = max(0, int(round(c0 / C.CORE_MHZ / 1e6 * sr)) - C.TRIGGER_SLACK_CYCLES)
    s1 = min(traces.shape[1], int(round(c1 / C.CORE_MHZ / 1e6 * sr)) + C.TRIGGER_SLACK_CYCLES)
    if s1 - s0 < 2:
        need_us = (c1 + C.TRIGGER_SLACK_CYCLES) / C.CORE_MHZ
        raise SystemExit("BMUL %d (core cycles %d-%d) lies outside the captured traces (%d samples = "
                         "%.2f us at %.4g Sa/s): capture at least %.1f us after the trigger"
                         % (poly, c0, c1, traces.shape[1], traces.shape[1] / sr * 1e6, sr, need_us))
    return traces[:, s0:s1]


def plot_coefficient(res: dict, poly: int, j: int, out_dir: str) -> None:
    if not HAVE_MPL:
        return
    import os
    os.makedirs(out_dir, exist_ok=True)
    fig, ax = plt.subplots(figsize=(7, 3))
    ax.plot(res["corr_curve"], lw=0.8)
    title = "poly %d, coeff %d -- best guess %d" % (poly, j, res["best_guess"])
    if res["true_rank"] is not None:
        title += " (true rank %d, true corr %.3f)" % (res["true_rank"], res["true_corr"])
    ax.set_title(title, fontsize=9)
    ax.set_xlabel("sample (within BMUL window)")
    ax.set_ylabel("correlation")
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "poly%d_coeff%03d.png" % (poly, j)), dpi=110)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", help="HDF5 campaign file from acquire_cpa.py")
    ap.add_argument("--model", default="", choices=[""] + list(R.MODEL_PRESETS),
                     help="named model preset (overrides --target/--rprev): %s"
                          % "; ".join("%s = %s" % (k, v["desc"]) for k, v in R.MODEL_PRESETS.items()))
    ap.add_argument("--target", default=C.CPA_TARGET, choices=list(R.TARGETS),
                     help="which register the hypothesis models (default from config.py's "
                          "CPA_TARGET, settable from the GUI's CPA Analysis tab: %s)" % C.CPA_TARGET)
    ap.add_argument("--rprev", default=C.CPA_RPREV, choices=list(R.RPREVS),
                     help="R_prev model (default from config.py's CPA_RPREV: %s). All options: %s"
                          % (C.CPA_RPREV, "; ".join("%s = %s" % kv for kv in R.RPREVS.items())))
    ap.add_argument("--max-traces", type=int, default=0, help="0 = use every trace in the file")
    ap.add_argument("--dk-ref", default="",
                     help="path to a dk binary: turns this into an ORACLE-ASSISTED evaluation "
                          "(scores the attack's own guesses against a real key; never used to "
                          "construct the guesses themselves -- see module docstring)")
    ap.add_argument("--curve", action="store_true",
                     help="also compute key-rank/success-rate vs. trace count and MTD "
                          "(needs --dk-ref: oracle-assisted only)")
    ap.add_argument("--plots", default="", help="directory for a few example correlation plots")
    ap.add_argument("--csv", default="", help="write the per-coefficient results table here")
    ap.add_argument("--curve-csv", default="", help="write the --curve checkpoints here")
    ap.add_argument("-o", "--out", default="", help="write the full JSON report here")
    args = ap.parse_args()

    target, rprev = args.target, args.rprev
    if args.model:
        p = R.MODEL_PRESETS[args.model]
        target, rprev = p["target"], p["rprev"]

    dk_ref = open(args.dk_ref, "rb").read() if args.dk_ref else None
    if args.curve and dk_ref is None:
        raise SystemExit("--curve needs --dk-ref (oracle-assisted evaluation): there is no way "
                         "to know when the attack has succeeded without a ground-truth key")

    summary = run_attack(args.file, target, rprev, args.max_traces, args.plots or None, dk_ref)

    print("file:            %s" % summary["file"])
    print("traces used:     %d" % summary["n_traces_used"])
    print("model:           target=%s rprev=%s%s" % (summary["target"], summary["rprev"],
          "  (--model %s)" % args.model if args.model else ""))
    print("coefficients:    %d (of 768)" % summary["coefficients_attacked"])
    if summary["oracle_assisted"]:
        print("\n################################################################")
        print("# ORACLE-ASSISTED EVALUATION (--dk-ref given)")
        print("# This scores the attack against a real key for reporting only.")
        print("# It is NOT a blind attack result -- do not present it as one.")
        print("################################################################")
        print("rank-1 fraction:    %.1f%%  (fraction of coefficients where the true key scored "
              "highest correlation)" % (100 * summary["rank1_fraction"]))
        print("median true rank:   %.0f  of %d candidates (chance ~= %.0f)"
              % (summary["median_true_rank"], summary["n_candidates"], summary["chance_rank_mean"]))
        print("mean |rho| at true key: %.4f" % summary["mean_true_corr"])
        if summary["rank1_fraction"] > 0.5:
            print("\n=> the modelled leakage recovers the key from these traces: "
                  "the implementation (as captured) is NOT resistant to this attack.")
        elif summary["median_true_rank"] > 0.8 * summary["chance_rank_mean"]:
            print("\n=> ranks are close to chance: no confident recovery under this model with "
                  "this many traces. This does NOT prove security -- see the caveats in README.md "
                  "(try more traces, other targets/rprev, higher-order analysis).")
        else:
            print("\n=> partial leakage (better than chance, not full recovery) -- try more traces "
                  "before concluding either way.")
    else:
        print("\nUnknown-key attack (no --dk-ref): every hypothesis above was built only from "
              "already-recovered guesses and the public ciphertext -- this is the actual attack "
              "a real adversary would run. Without a reference key this is NOT a success/fail "
              "verdict, only what the attack recovered under the stated model.")

    if args.curve:
        curve = run_curve(args.file, target, rprev, args.max_traces, dk_ref)
        print("\n-- key rank / success rate vs. trace count (oracle-assisted) --")
        print("%10s  %12s  %12s  %10s" % ("n_traces", "rank1_frac", "median_rank", "mean|rho|"))
        for row in curve["checkpoints"]:
            print("%10d  %11.1f%%  %12.0f  %10.4f"
                  % (row["n_traces"], 100 * row["rank1_fraction"], row["median_rank"], row["mean_true_corr"]))
        print("MTD (minimum traces to disclosure, rank1_fraction > 50%%): %s"
              % (curve["mtd"] if curve["mtd"] is not None else "not reached within max_traces"))
        if args.curve_csv:
            write_curve_csv(curve, args.curve_csv)
            print("curve CSV: %s" % args.curve_csv)
        summary["curve"] = curve

    if args.csv:
        write_results_csv(summary["results"], args.csv)
        print("\nper-coefficient CSV: %s" % args.csv)

    if args.out:
        with open(args.out, "w") as f:
            json.dump(summary, f, indent=2, default=lambda o: int(o) if isinstance(o, np.integer) else str(o))
        print("full report: %s" % args.out)


if __name__ == "__main__":
    main()
