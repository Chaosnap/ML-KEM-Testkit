#!/usr/bin/env python3
"""Simple coefficient-level CPA against the ML-KEM-768 BaseMul of K-PKE.Decrypt.

This replaces the earlier model catalogue (``models/``, row-aligned pipeline attacks) with the
plain recipe the public ML-KEM CPA code bases share:

* for a public NTT-domain coefficient ``u`` (from the known ciphertext) and every secret
  candidate ``g`` in ``0 .. 3328``, the hypothesis is ``HW(u * g mod q)``
  (anshupriyan/My-CPA-code-analysis ``Source_code.py``, Chaosnap/CorrelationPowerAnalysis ``CPA.py``);
* Pearson correlation between every hypothesis and every sample of the trace window, as one
  matrix product (``Correlate`` / ``numerator = t_centered.T @ h_centered`` in both repos);
* the candidate with the largest ``|rho|`` anywhere in the window wins; its peak position is the
  point of interest (POI);
* a top-10 list per coefficient and a *stability* vote: sum ``rho^2`` over windows of 3, 5, 7 and
  9 samples around the POI and count in how many of the four windows a candidate is in the top 10
  (anshupriyan ``Source_code.py`` phases 2-4, ``CPA.py`` ``window_radii``).

Two hypotheses, each a real register of the multiplier (``hdl/core/mlkem_poly_alu.sv``,
``mlkem_modmul.sv``; ``T`` = the cycle the RAM read address of the word holding the coefficient is
issued, ``T = BMUL_READ_CYCLES[poly] + 2 * (coeff // 2)``, the operands reach the multiplier at
``T + 3``):

    hw_product_raw   HW(u * g)       24 bit, DSP product register P       cycle T + 5 / T + 6
    hw_product_mod   HW(u * g mod q) 12 bit, Barrett output r / p00, p11  cycle T + 10 / T + 11

Both coefficients of a pair (``2w``, ``2w + 1``) are multiplied in the same cycle by the two
multipliers, so the partner coefficient is algorithmic noise for the other one. Hypotheses depend
only on ``u`` and ``g``: no partner and no earlier recovery enter, so the attack is blind by
construction; the true key (``--dk-ref``) is used only to *rank* the guesses afterwards.

The predicted cycle of every coefficient is printed next to where its correlation peak actually
is: peaks that pile up at a fixed offset from the prediction show the traces are aligned and
the leakage is real; peaks scattered over the window show noise.

Usage::

    python cpa_attack.py --list-models
    python cpa_attack.py FILE.hdf5                              # blind, first 8 coefficients of each polynomial
    python cpa_attack.py FILE.hdf5 --coeffs 0-15 --polys 0
    python cpa_attack.py FILE.hdf5 --dk-ref dk.bin              # + rank of the true key
    python cpa_attack.py FILE.hdf5 --search gate --margin 10    # only around the predicted cycle
    python cpa_attack.py FILE.hdf5 --out report.json --csv results.csv
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys

import numpy as np

import acquire_cpa as A
import config as C
import mlkem_ref as R
from hdf5_trace_store import TraceReader

Q, K = R.Q, R.K

#: Window radii (samples) of the stability vote: windows of 3, 5, 7 and 9 samples around the POI.
WINDOW_RADII = (1, 2, 3, 4)
TOP_N = 10
GUESS_CHUNK = 512

_HW12 = np.array([bin(i).count("1") for i in range(4096)], dtype=np.float32)


def hw24(x: np.ndarray) -> np.ndarray:
    """Hamming weight of the low 24 bits."""
    x = np.asarray(x, dtype=np.int64)
    return _HW12[x & 0xFFF] + _HW12[(x >> 12) & 0xFFF]


class Model:
    """One hypothesis: ``leak(u * g)`` and where in the pipeline it lives."""

    def __init__(self, name, title, equation, register, bits, cycles, source, leak, limitations):
        self.name, self.title, self.equation, self.register = name, title, equation, register
        self.bits, self.cycles, self.source, self.leak, self.limitations = bits, cycles, source, leak, limitations

    def hypotheses(self, u: np.ndarray, guesses: np.ndarray) -> np.ndarray:
        """``(N, G)`` float32 Hamming weights for ``u`` ``(N,)`` and candidates ``guesses`` ``(G,)``."""
        return self.leak(np.asarray(u, dtype=np.int64)[:, None] * np.asarray(guesses, dtype=np.int64)[None, :])

    def describe(self) -> dict:
        return {"name": self.name, "title": self.title, "equation": self.equation, "register": self.register,
                "bits": self.bits, "cycles_after_read": list(self.cycles), "source": self.source,
                "limitations": self.limitations}


MODELS: dict[str, Model] = {m.name: m for m in (
    Model("hw_product_mod", "HW of the reduced product", "HW( u[j] * g mod q )",
          "r (Barrett output) -> p00 / p11", 12, (10, 11),
          "anshupriyan Source_code.py, Chaosnap CPA.py (model HW)",
          lambda x: _HW12[x % Q],
          "Two multipliers finish a pair in the same cycle, so the partner coefficient adds noise of the "
          "same size as the signal; needs many traces (thousands). u[j] = 0 carries no information."),
    Model("hw_product_raw", "HW of the raw product", "HW( u[j] * g )",
          "DSP product register P (24 bit)", 24, (5, 6),
          "same recipe, one stage earlier (mlkem_modmul.sv p1 / p2)",
          hw24,
          "Raw 24-bit product before the Barrett reduction: more bits toggle, so a stronger signal than the "
          "reduced product when the DSP register is what the probe sees. The same pair noise applies."),
)}
DEFAULT_MODEL = "hw_product_mod"


def get_model(name: str) -> Model:
    if name not in MODELS:
        raise ValueError("unknown model %r (available: %s)" % (name, ", ".join(MODELS)))
    return MODELS[name]


def list_models() -> list[dict]:
    return [m.describe() for m in MODELS.values()]


# --------------------------------------------------------------------------- statistics
def noise_floor_corr(n_traces: int, trials: int) -> float:
    """|rho| that pure noise reaches at least once in ``trials`` independent comparisons
    (candidates x samples), for ``n_traces`` traces: the level a peak must clear to mean anything."""
    return float(np.sqrt(2 * np.log(max(trials, 2)) / max(n_traces, 1)))


def standardize(x: np.ndarray) -> np.ndarray:
    """Column-wise zero mean / unit variance, float32 (constant columns become 0)."""
    x = np.asarray(x, dtype=np.float32)
    x = x - x.mean(axis=0, keepdims=True)
    sd = x.std(axis=0, keepdims=True)
    return np.divide(x, sd, out=np.zeros_like(x), where=sd > 1e-12)


def correlate(hyp: np.ndarray, tz: np.ndarray) -> np.ndarray:
    """Pearson rho ``(G, T)`` for hypotheses ``(N, G)`` and standardized traces ``(N, T)``."""
    with np.errstate(all="ignore"):     # macOS Accelerate raises spurious FP flags in float32 matmul
        rho = (standardize(hyp).T @ tz) / tz.shape[0]
    return np.nan_to_num(rho, nan=0.0, posinf=0.0, neginf=0.0)


def sample_to_cycle(sample: int, rate: float, start_s: float) -> float:
    """Core clock cycle after the trigger that sample ``sample`` falls in."""
    sr = rate if rate > 0 else C.CORE_MHZ * 1e6
    return (sample / sr + (start_s if rate > 0 else 0.0)) * C.CORE_MHZ * 1e6


def predicted_cycles(model: Model, poly: int, coeff: int) -> tuple[int, int]:
    """First and last core cycle at which the hypothesised register holds this coefficient's product."""
    t0 = C.BMUL_READ_CYCLES[poly] + 2 * (coeff >> 1)
    return t0 + model.cycles[0], t0 + model.cycles[-1]


def search_range(model: Model, poly: int, coeff: int, search: str, margin: int, delay: float,
                 rate: float, start_s: float, n_samples: int) -> tuple[int, int]:
    """Samples ``[s0, s1)`` of the trace the attack looks at for one coefficient: the whole BMUL
    window of its polynomial (``full``) or the predicted cycle +- ``margin`` (``gate``)."""
    if search == "full":
        return A.window_sample_ranges(rate, n_samples, start_s if rate > 0 else 0.0)[poly]
    sr = rate if rate > 0 else C.CORE_MHZ * 1e6
    st = start_s if rate > 0 else 0.0
    c0, c1 = predicted_cycles(model, poly, coeff)
    s0 = max(0, A.cycle_to_sample(c0 + delay - margin, sr, st))
    s1 = min(n_samples, A.cycle_to_sample(c1 + delay + margin, sr, st) + 1)
    return s0, max(s0, s1)


# --------------------------------------------------------------------------- one coefficient
def attack_coefficient(model: Model, u: np.ndarray, tz: np.ndarray, true_value: int | None = None,
                       want_full: bool = False) -> dict:
    """CPA over one secret coefficient.

    ``u`` ``(N,)`` public coefficients, ``tz`` ``(N, T)`` standardized trace window. Returns the best
    guess, its peak and position, the top-10 list with the stability vote and, when ``true_value`` is
    given, the rank of the true key (by peak ``|rho|``). ``want_full`` also returns every candidate's
    peak (``peak_per_guess``) and the correlation of the best guess over time (``curve``).
    """
    n, t = tz.shape
    guesses = np.arange(Q, dtype=np.int64)
    out = {"informative": False, "best_guess": None, "best_corr": 0.0, "best_sign": 0, "best_sample": None,
           "second_corr": 0.0, "top": [], "votes_best": 0, "true_value": true_value, "true_rank": None,
           "true_corr": None, "true_sample": None}
    if t == 0 or n < 8 or np.unique(u).size < 2:     # nothing varies: every hypothesis is constant
        return out
    peak = np.zeros(Q, dtype=np.float32)
    peak_t = np.zeros(Q, dtype=np.int64)
    peak_signed = np.zeros(Q, dtype=np.float32)
    for a in range(0, Q, GUESS_CHUNK):
        g = guesses[a:a + GUESS_CHUNK]
        rho = correlate(model.hypotheses(u, g), tz)
        ab = np.abs(rho)
        pt = ab.argmax(axis=1)
        rows = np.arange(len(g))
        peak[a:a + len(g)], peak_t[a:a + len(g)], peak_signed[a:a + len(g)] = ab[rows, pt], pt, rho[rows, pt]
    order = np.argsort(-peak, kind="stable")
    best = int(order[0])
    out.update(informative=bool(peak[best] > 0), best_guess=best, best_corr=float(peak[best]),
               best_sign=int(np.sign(peak_signed[best])), best_sample=int(peak_t[best]),
               second_corr=float(peak[order[1]]))
    # stability: sum of rho^2 over 3 / 5 / 7 / 9 samples around the POI, top-10 of each, vote per candidate
    poi = int(peak_t[best])
    votes = np.zeros(Q, dtype=np.int64)
    for r in WINDOW_RADII:
        lo, hi = max(0, poi - r), min(t, poi + r + 1)
        score = np.empty(Q, dtype=np.float32)
        for a in range(0, Q, GUESS_CHUNK):
            g = guesses[a:a + GUESS_CHUNK]
            score[a:a + len(g)] = (correlate(model.hypotheses(u, g), tz[:, lo:hi]) ** 2).sum(axis=1)
        votes[np.argsort(-score, kind="stable")[:TOP_N]] += 1
    out["votes_best"] = int(votes[best])
    out["top"] = [{"guess": int(g), "corr": float(peak_signed[g]), "sample": int(peak_t[g]), "votes": int(votes[g])}
                  for g in order[:TOP_N]]
    if true_value is not None and 0 <= true_value < Q:
        out["true_rank"] = int(np.where(order == true_value)[0][0]) + 1
        out["true_corr"] = float(peak[true_value])
        out["true_sample"] = int(peak_t[true_value])
    if want_full:
        out["peak_per_guess"] = peak
        out["curve"] = correlate(model.hypotheses(u, np.array([best])), tz)[0]
        if true_value is not None:
            out["true_curve"] = correlate(model.hypotheses(u, np.array([true_value])), tz)[0]
    return out


# --------------------------------------------------------------------------- whole campaign
def parse_range(text, upper: int) -> list[int]:
    """``"0-7"``, ``"0,3,5"``, ``"8"`` (= the first 8), ``""`` or ``0`` (= all ``upper``) -> indices."""
    if text in (None, "", 0, "0"):
        return list(range(upper))
    if isinstance(text, (list, tuple)):
        return [int(x) for x in text]
    text = str(text).strip()
    if "-" in text or "," in text:
        out = []
        for part in text.split(","):
            part = part.strip()
            if "-" in part:
                a, b = part.split("-", 1)
                out += list(range(int(a), int(b) + 1))
            elif part:
                out.append(int(part))
        bad = [i for i in out if not 0 <= i < upper]
        if bad:
            raise ValueError("indices %s outside 0..%d" % (bad, upper - 1))
        return out
    return list(range(min(int(text), upper)))


def parse_polys(text) -> list[int]:
    """Polynomials to attack: ``"0-2"``, ``"0,2"`` or ``"1"`` (a list, not a count); empty = all three."""
    if text in (None, "", ()):
        return list(range(K))
    if isinstance(text, (list, tuple)):
        out = [int(x) for x in text]
    else:
        out = []
        for part in str(text).split(","):
            part = part.strip()
            if "-" in part:
                a, b = part.split("-", 1)
                out += list(range(int(a), int(b) + 1))
            elif part:
                out.append(int(part))
    if not out or any(not 0 <= i < K for i in out):
        raise ValueError("polynomials %r outside 0..%d" % (text, K - 1))
    return out


def load_campaign(h5_path: str, max_traces: int = 0) -> dict:
    """The traces and public inputs of a CPA campaign: only traces that passed their integrity check."""
    with TraceReader(h5_path) as r:
        if r.mode != "cpa":
            raise SystemExit("%s is a %s campaign -- CPA needs a CPA campaign (use tvla_analysis.py for TVLA)"
                             % (r.path.name, r.mode.upper()))
        n = min(r.n_completed, max_traces) if max_traces else r.n_completed
        ok = r.ok[:n]
        traces = np.asarray(r.traces[:n][ok], dtype=np.float32)
        cts = r.ciphertexts[:n][ok]
        info = {"rate": r.sample_rate, "start": r.record_start_s, "dk_hash": r.dk_hash, "n_failed": int((~ok).sum())}
    if traces.shape[0] < 20:
        raise SystemExit("only %d usable traces; capture more" % traces.shape[0])
    info["u_hat"] = R.u_hat_from_ct(cts)
    info["traces"] = traces
    return info


def true_key(dk_ref: bytes | None, dk_hash: bytes) -> np.ndarray | None:
    if dk_ref is None:
        return None
    if hashlib.sha3_256(dk_ref).digest() != dk_hash:
        raise SystemExit("the dk does not match the key this file was captured under (SHA3-256(dk) mismatch)")
    return R.s_hat_from_dk(dk_ref)


def run_attack(h5_path: str, model: str = DEFAULT_MODEL, max_traces: int = 0, dk_ref: bytes | None = None,
               polys="0-2", coeffs="8", search: str = "full", margin: int = 10, delay: float = 0.0,
               progress_cb=None) -> dict:
    """CPA over the chosen coefficients; returns the report dict (JSON-serialisable)."""
    m = get_model(model)
    if search not in ("full", "gate"):
        raise ValueError("search must be 'full' or 'gate'")
    d = load_campaign(h5_path, max_traces)
    rate, start = d["rate"], d["start"]
    start_s = start or 0.0
    traces, u_hat = d["traces"], d["u_hat"]
    n, n_samp = traces.shape
    s_hat = true_key(dk_ref, d["dk_hash"])
    poly_list = parse_polys(polys)
    coeff_list = parse_range(coeffs, 256)
    results, total, searched = [], len(poly_list) * len(coeff_list), []
    for poly in poly_list:
        w0, w1 = A.window_sample_ranges(rate, n_samp, start_s if rate > 0 else 0.0)[poly]
        if w1 <= w0:
            raise SystemExit("BMUL %d is outside the record (%.2f .. %.2f us after the trigger) -- see Acquisition > "
                             "Check trigger" % (poly, start_s * 1e6, (start_s + n_samp / max(rate, 1)) * 1e6))
        tzw = standardize(traces[:, w0:w1])      # the BMUL window of this polynomial, standardised once
        for j in coeff_list:
            if search == "full":
                s0, s1, tz = w0, w1, tzw
            else:
                s0, s1 = search_range(m, poly, j, search, margin, delay, rate, start_s, n_samp)
                tz = standardize(traces[:, s0:s1])
            res = attack_coefficient(m, u_hat[:, poly, j], tz, int(s_hat[poly][j]) if s_hat is not None else None)
            pc = predicted_cycles(m, poly, j)
            res.update(poly=poly, coeff=j, search_samples=[int(s0), int(s1)], predicted_cycle=pc[0] + delay,
                       n_search_samples=int(s1 - s0))
            if res["best_sample"] is not None:
                bs = s0 + res["best_sample"]
                res["best_sample"] = int(bs)
                res["best_time_us"] = round(((bs / rate) + start_s) * 1e6, 4) if rate else None
                res["best_cycle"] = round(sample_to_cycle(bs, rate, start_s), 2)
                res["offset_cycles"] = round(res["best_cycle"] - (pc[0] + delay), 2)
                for t_ in res["top"]:
                    t_["sample"] = int(s0 + t_["sample"])
                if res["true_sample"] is not None:
                    res["true_sample"] = int(s0 + res["true_sample"])
            searched.append(s1 - s0)
            results.append(res)
            if progress_cb:
                progress_cb(len(results), total)
    summary = {"file": str(h5_path), "model": m.name, "model_info": m.describe(), "search": search, "margin_cycles": margin,
               "delay_cycles": delay, "polys": poly_list, "coeff_indices": coeff_list,
               "n_traces_used": n, "n_traces_failed_check": d["n_failed"], "n_samples": n_samp,
               "sample_rate_hz": rate, "record_start_s": start, "oracle_assisted": s_hat is not None,
               "n_candidates": Q, "results": results,
               "noise_floor_corr": noise_floor_corr(n, int(Q * max(float(np.mean(searched)) if searched else 1, 1)))}
    _summarise(summary)
    return summary


def _summarise(s: dict) -> None:
    rows = [r for r in s["results"] if r["informative"]]
    s["n_uninformative"] = len(s["results"]) - len(rows)
    best = [r["best_corr"] for r in rows]
    s["mean_best_corr"] = float(np.mean(best)) if best else 0.0
    s["max_best_corr"] = float(np.max(best)) if best else 0.0
    floor = s["noise_floor_corr"]
    s["n_above_floor"] = int(sum(1 for b in best if b > floor))
    s["n_stable"] = int(sum(1 for r in rows if r["votes_best"] == len(WINDOW_RADII)))
    # alignment: where do the confident peaks sit relative to the prediction?
    offs = [r["offset_cycles"] for r in rows if r["best_corr"] > floor and "offset_cycles" in r]
    s["alignment"] = {"n": len(offs), "median_offset_cycles": float(np.median(offs)) if offs else None,
                      "spread_cycles": float(np.subtract(*np.percentile(offs, [75, 25]))) if offs else None,
                      "offsets": [round(float(o), 2) for o in offs]}
    scored = [r for r in rows if r["true_rank"] is not None]
    if scored:
        ranks = [r["true_rank"] for r in scored]
        s["rank1_fraction"] = float(np.mean([x == 1 for x in ranks]))
        s["top10_fraction"] = float(np.mean([x <= TOP_N for x in ranks]))
        s["median_true_rank"] = float(np.median(ranks))
        s["chance_rank_mean"] = (Q + 1) / 2
        s["mean_true_corr"] = float(np.mean([r["true_corr"] for r in scored]))


# --------------------------------------------------------------------------- inspector (one coefficient, in detail)
def coefficient_detail(h5_path: str, model: str, poly: int, coeff: int, max_traces: int = 0,
                       dk_ref: bytes | None = None, search: str = "full", margin: int = 10, delay: float = 0.0,
                       checkpoints: int = 14, heat_rows: int = 24, heat_cols: int = 360) -> dict:
    """Everything the GUI draws for one coefficient: correlation over time (best guess and true key),
    every candidate's peak, a heat map of the top candidates, and the convergence with the trace count."""
    m = get_model(model)
    d = load_campaign(h5_path, max_traces)
    rate, start_s = d["rate"], d["start"] or 0.0
    traces, u = d["traces"], d["u_hat"][:, poly, coeff]
    n, n_samp = traces.shape
    s_hat = true_key(dk_ref, d["dk_hash"])
    tv = int(s_hat[poly][coeff]) if s_hat is not None else None
    s0, s1 = search_range(m, poly, coeff, search, margin, delay, rate, start_s, n_samp)
    tz = standardize(traces[:, s0:s1])
    res = attack_coefficient(m, u, tz, tv, want_full=True)
    xs = [round(((s0 + i) / rate + start_s) * 1e6, 5) if rate else s0 + i for i in range(tz.shape[1])]
    pc = predicted_cycles(m, poly, coeff)
    det = {"poly": poly, "coeff": coeff, "model": m.name, "n_traces": n, "x": xs, "x_unit": "us" if rate else "sample",
           "sample_offset": int(s0),
           "predicted_us": [round((pc[0] + delay) / C.CORE_MHZ, 4), round((pc[1] + delay) / C.CORE_MHZ, 4)],
           "predicted_cycle": [pc[0] + delay, pc[1] + delay], "true_value": tv, "informative": res["informative"],
           "noise_floor_corr": noise_floor_corr(n, Q * max(tz.shape[1], 1)), "best_guess": res["best_guess"],
           "best_corr": res["best_corr"], "top": res["top"], "true_rank": res["true_rank"],
           "true_corr": res["true_corr"], "votes_best": res["votes_best"]}
    if not res["informative"]:
        return det
    for t_ in det["top"]:
        t_["time_us"] = round(((s0 + t_["sample"]) / rate + start_s) * 1e6, 4) if rate else None
    det["curve"] = np.round(res["curve"], 4).tolist()
    if tv is not None:
        det["true_curve"] = np.round(res["true_curve"], 4).tolist()
    pp = res["peak_per_guess"]
    det["peak_per_guess"] = np.round(pp, 4).tolist()
    # heat map: the top candidates (+ the true key) x time, max-pooled to at most heat_cols columns
    order = np.argsort(-pp, kind="stable")[:heat_rows].tolist()
    if tv is not None and tv not in order:
        order.append(tv)
    rho = np.abs(correlate(m.hypotheses(u, np.array(order)), tz))
    t = rho.shape[1]
    if t > heat_cols:
        edges = np.linspace(0, t, heat_cols + 1).astype(int)
        rho = np.stack([rho[:, a:max(b, a + 1)].max(axis=1) for a, b in zip(edges[:-1], edges[1:])], axis=1)
        hx = [xs[min(a, t - 1)] for a in edges[:-1]]
    else:
        hx = xs
    det["heat"] = {"guesses": order, "x": hx, "z": np.round(rho, 3).tolist()}
    # convergence around the POI (cheap: a few samples, all candidates, growing trace count)
    poi = int(np.argmax(np.abs(res["curve"])))
    lo, hi = max(0, poi - 4), min(tz.shape[1], poi + 5)
    ref = tv if tv is not None else res["best_guess"]
    conv = []
    grid = sorted({int(x) for x in np.geomspace(max(20, n // 40), n, checkpoints)})
    for k in grid:
        rk = np.abs(correlate(m.hypotheses(u[:k], np.arange(Q)), standardize(tz[:k, lo:hi]))).max(axis=1)
        conv.append({"n_traces": k, "ref": float(rk[ref]), "best_other": float(np.delete(rk, ref).max()),
                     "rank": int((rk > rk[ref]).sum()) + 1, "floor": noise_floor_corr(k, Q * (hi - lo))})
    det["convergence"] = {"reference": "true key" if tv is not None else "best guess", "ref_guess": int(ref),
                          "points": conv}
    return det


# --------------------------------------------------------------------------- output
def write_results_csv(results: list[dict], path: str) -> None:
    cols = ["poly", "coeff", "informative", "best_guess", "best_corr", "best_sign", "second_corr", "best_sample",
            "best_time_us", "best_cycle", "predicted_cycle", "offset_cycles", "votes_best", "true_value",
            "true_rank", "true_corr"]
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols + ["top10 (guess:|rho|:votes)"])
        for r in results:
            w.writerow([r.get(c, "") for c in cols] + [" ".join("%d:%.3f:%d/4" % (t["guess"], abs(t["corr"]), t["votes"])
                                                                for t in r["top"])])


def print_report(s: dict, out=sys.stdout) -> None:
    m = s["model_info"]
    print("\n========== ML-KEM CPA RESULTS ==========", file=out)
    print("model %s: %s   (%s, cycles T+%s)" % (m["name"], m["equation"], m["register"],
                                                  "/".join(str(c) for c in m["cycles_after_read"])), file=out)
    print("traces %d   search %s%s   noise floor |rho| ~ %.3f%s" % (
        s["n_traces_used"], s["search"], " +-%d cycles" % s["margin_cycles"] if s["search"] == "gate" else "",
        s["noise_floor_corr"], "   (oracle-assisted ranking)" if s["oracle_assisted"] else "   (blind)"), file=out)
    hdr = "%-5s %-5s %-5s %-7s %-9s %-8s %-8s %-6s" % ("poly", "coef", "best", "|rho|", "peak us", "cycle", "predict", "votes")
    if s["oracle_assisted"]:
        hdr += " %-6s %-5s" % ("true", "rank")
    print(hdr, file=out)
    for r in s["results"]:
        if not r["informative"]:
            print("%-5d %-5d (no key information: u constant or too few traces)" % (r["poly"], r["coeff"]), file=out)
            continue
        line = "%-5d %-5d %-5d %-7.4f %-9s %-8s %-8s %d/%d" % (
            r["poly"], r["coeff"], r["best_guess"], r["best_corr"], r.get("best_time_us"), r.get("best_cycle"),
            r["predicted_cycle"], r["votes_best"], len(WINDOW_RADII))
        if s["oracle_assisted"]:
            line += "   %-6s %-5s" % (r["true_value"], r["true_rank"])
        print(line, file=out)
        print("      top-10: " + "  ".join("%d(%.3f,%d/4)" % (t["guess"], abs(t["corr"]), t["votes"]) for t in r["top"]), file=out)
    print("-" * 78, file=out)
    print("peaks above the noise floor: %d of %d   stable (4/4 windows): %d" % (
        s["n_above_floor"], len(s["results"]) - s["n_uninformative"], s["n_stable"]), file=out)
    al = s["alignment"]
    if al["n"]:
        print("alignment: confident peaks sit %+.1f cycles from the prediction (IQR %.1f cycles) -- a small IQR means "
              "the traces are aligned and the leakage is real" % (al["median_offset_cycles"], al["spread_cycles"]), file=out)
    else:
        print("alignment: no peak above the noise floor", file=out)
    if s["oracle_assisted"]:
        print("true key: rank-1 %.0f%%, top-10 %.0f%%, median rank %.0f of %d (chance ~ %.0f)" % (
            100 * s.get("rank1_fraction", 0), 100 * s.get("top10_fraction", 0), s.get("median_true_rank", 0), Q,
            s.get("chance_rank_mean", 0)), file=out)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("file", nargs="?", help="CPA campaign (.hdf5)")
    p.add_argument("--list-models", action="store_true")
    p.add_argument("--model", default=DEFAULT_MODEL, choices=list(MODELS))
    p.add_argument("--polys", default="0-2", help="polynomials to attack, e.g. 0-2 or 0,2 (default all three)")
    p.add_argument("--coeffs", default="8", help="coefficients per polynomial: N = the first N, 0 = all 256, or 0-15 / 3,7")
    p.add_argument("--max-traces", type=int, default=0, help="0 = all")
    p.add_argument("--search", choices=("full", "gate"), default="full",
                   help="full = the whole BMUL window (finds the peak wherever it is), gate = only around the predicted cycle")
    p.add_argument("--margin", type=int, default=10, help="gate half-width in core cycles")
    p.add_argument("--delay", type=float, default=0.0, help="shift the predicted cycle (probe / amplifier delay), core cycles")
    p.add_argument("--dk-ref", help="2400-byte dk file: ranks the true key (evaluation only, never used by the attack)")
    p.add_argument("--out", help="write the report as JSON")
    p.add_argument("--csv", help="write the per-coefficient table as CSV")
    a = p.parse_args()
    if a.list_models:
        for m in MODELS.values():
            print("%-16s %s\n    %s  [%s]" % (m.name, m.title, m.equation, m.register))
        return
    if not a.file:
        p.error("a campaign file is required")
    dk = open(a.dk_ref, "rb").read() if a.dk_ref else None

    def progress(done, total):
        print("\r  coefficient %d / %d" % (done, total), end="", file=sys.stderr, flush=True)
    s = run_attack(a.file, a.model, a.max_traces, dk, a.polys, a.coeffs, a.search, a.margin, a.delay, progress)
    print(file=sys.stderr)
    print_report(s)
    if a.out:
        with open(a.out, "w") as f:
            json.dump(s, f, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    if a.csv:
        write_results_csv(s["results"], a.csv)


if __name__ == "__main__":
    main()
