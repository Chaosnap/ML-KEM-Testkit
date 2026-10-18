#!/usr/bin/env python3
"""CPA attack against ML-KEM-768 BaseMul in K-PKE.Decrypt.

The leakage model is chosen by name from ``models/`` (one file per model,
``--model``; default ``hd_basemul_word``: ``HD(R_prev, {c1, c0})`` of the
24-bit RAM word written back each BMUL cycle, since that is what a real
probe sees). ``models/base.py: Guess`` builds the register values a
hypothesis is made of; ``docs/ATTACK_MODEL.md`` names the registers in
``hdl/core/mlkem_poly_alu.sv``. The attacker knows every ciphertext ``c``
(hence ``u_hat``, public) and recovers ``s_hat`` (the decapsulation key) one
12-bit coefficient at a time.

Usage::

    python cpa_attack.py --list-models                          # the models in models/
    python cpa_attack.py FILE.hdf5                              # unknown-key (the real attack)
    python cpa_attack.py FILE.hdf5 --model hd_scalar --rprev zero
    python cpa_attack.py FILE.hdf5 --model pipe_all               # schedule-aware, row-aligned (per-cycle features)
    python cpa_attack.py FILE.hdf5 --model pipe_all --features energy --offsets -3:3
    python cpa_attack.py FILE.hdf5 --dk-ref REF.bin               # oracle-assisted evaluation
    python cpa_attack.py FILE.hdf5 --dk-ref REF.bin --curve         # + key-rank-vs-N, MTD

``--dk-ref`` is **oracle-assisted**: it scores the attack's own guesses
against the campaign's real key (``testvectors.py export <set> --format dk``:
the vector set a campaign was captured with holds its key pair), purely for
evaluation/reporting. The
guesses themselves are never constructed with it -- see
``models/base.py: Guess`` and ``docs/ATTACK_MODEL.md`` section 5.4: without
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

Two kinds of model. The ``hd_*`` / ``hw_*`` models name one register and are correlated with every
sample of the BMUL window (the best sample wins). The ``pipe_*`` models (:class:`models.PipelineModel`)
follow the RTL schedule: a hypothesis per clock cycle, correlated with the trace feature of its own
cycle and summed (``aligned_cpa.py``); they need the sample rate of the campaign to turn the trace into
one feature per cycle (``--features``), and search a small integer cycle offset (``--offsets``).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

import numpy as np

import aligned_cpa as AC
import config as C
import cycle_features as CF
import mlkem_ref as R
import models as LM
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


def informative(hyp: np.ndarray) -> bool:
    """Can a CPA over these hypotheses ``(N, G)`` tell the candidates apart at all?

    Correlation is invariant to a per-candidate shift and scale, so a coefficient carries
    information only if, after centring each candidate's column over the traces, the columns
    still differ. Two ways a campaign makes that impossible, both silent otherwise:

    * the hypothesis does not depend on the guess (every column identical) -- e.g. a chosen
      ciphertext whose u_hat is zero for this polynomial multiplies the guess by zero;
    * the hypothesis is constant over the traces (every column centres to zero), so there is
      nothing for the measured variation to correlate with.

    Without this, such a coefficient still gets a "best guess" and a true-key rank -- a rank
    drawn uniformly at random, which reads exactly like a failed attack on a sound campaign.
    """
    d = np.asarray(hyp, dtype=np.float64)
    d = d - d.mean(axis=0, keepdims=True)
    return bool(d.shape[1] > 1 and np.ptp(d, axis=1).max() > 1e-9)


def attack_coefficient(model: LM.LeakageModel, guess: LM.Guess, rprev: str, traces: np.ndarray,
                        candidates: np.ndarray) -> dict:
    """CPA over one secret coefficient; returns peak correlation per candidate."""
    hyp = model.hypotheses(guess, rprev, candidates)   # (N, G)
    if not informative(hyp):
        return {"poly": guess.poly, "coeff": guess.j, "target": model.register, "rprev": rprev,
                "informative": False, "best_guess": None, "best_corr": 0.0, "best_sample": None,
                "true_value": guess.true_value, "true_rank": None, "true_corr": None,
                "corr_curve": np.zeros(traces.shape[1]), "candidates": candidates,
                "peak_per_cand": np.zeros(len(candidates))}
    corr = pearson_matrix(hyp, traces)                  # (G, T)
    peak_per_cand = np.max(np.abs(corr), axis=1)
    peak_sample = np.argmax(np.abs(corr), axis=1)
    order = np.argsort(peak_per_cand)[::-1]
    true_rank = None
    if guess.true_value in candidates:
        g_idx = int(np.where(candidates == guess.true_value)[0][0])
        true_rank = int(np.where(order == g_idx)[0][0]) + 1
    return {
        "poly": guess.poly, "coeff": guess.j, "target": model.register, "rprev": rprev,
        "informative": True,
        "best_guess": int(candidates[order[0]]),
        "best_corr": float(peak_per_cand[order[0]]),
        "best_sample": int(peak_sample[order[0]]),
        "true_value": guess.true_value, "true_rank": true_rank,
        "true_corr": float(peak_per_cand[int(np.where(candidates == guess.true_value)[0][0])])
                     if guess.true_value in candidates else None,
        "corr_curve": corr[order[0]],  # kept only in memory for plotting; not JSON-serialised
        "candidates": candidates, "peak_per_cand": peak_per_cand,
    }


def _require_cpa(r) -> None:
    """CPA needs distinct ciphertexts; a TVLA campaign repeats one for half its rows."""
    if r.mode != "cpa":
        raise SystemExit("%s is a %s campaign -- CPA needs a CPA campaign; use tvla_analysis.py "
                         "(or the GUI's Run TVLA) for this file" % (r.path.name, r.mode.upper()))


def _model(model, rprev: str) -> tuple[LM.LeakageModel, str]:
    """A model (name or object) and the R_prev it runs with."""
    m = LM.get(model) if isinstance(model, str) else model
    return m, m.resolve_rprev(rprev)


def parse_offsets(text) -> tuple:
    """Cycle offsets to search: ``"-6:6"``, ``"3"``, ``"-2,0,2"``, a list/tuple of ints, or empty for the default."""
    if text in (None, "", ()):
        return AC.DEFAULT_OFFSETS
    if isinstance(text, (list, tuple)):
        return tuple(int(x) for x in text)
    text = str(text).strip()
    if ":" in text:
        a, b = text.split(":", 1)
        a, b = int(a), int(b)
        if a > b:
            raise ValueError("offsets %r: the range runs from low to high" % text)
        return tuple(range(a, b + 1))
    return tuple(int(x) for x in text.split(",") if x.strip())


def feature_mode(model, features: str) -> str:
    """The per-cycle feature mode of an aligned model: ``features`` or, for ``"auto"`` / ``"raw"``, the default."""
    if features in ("", "auto"):
        return "pca"
    if features not in CF.MODES:
        raise SystemExit("%s works on one feature per clock cycle: --features %s (not %r)"
                         % (model.name, "|".join(CF.MODES), features))
    return features


def _workers() -> int:
    return max(1, min(8, os.cpu_count() or 1))


def _run_aligned(h5_path, model, max_traces, plot_dir, dk_ref, features, offsets, progress_cb, coeffs) -> dict:
    """The 768-coefficient attack with a :class:`models.PipelineModel` (row-aligned, per-cycle features)."""
    mode, offsets = feature_mode(model, features), parse_offsets(offsets)
    with TraceReader(h5_path) as r:
        _require_cpa(r)
        n = min(r.n_completed, max_traces) if max_traces else r.n_completed
        ok = r.ok[:n]
        if not np.all(ok):
            print("note: %d/%d traces failed an integrity check and are excluded"
                  % (int((~ok).sum()), n), file=sys.stderr)
        traces_full = r.traces[:n][ok]
        cts = r.ciphertexts[:n][ok]
        dk_hash_in_file = r.dk_hash
        rate = r.sample_rate
    n_ok = traces_full.shape[0]
    if n_ok < 20:
        raise SystemExit("only %d usable traces; capture more" % n_ok)
    if rate <= 0:
        raise SystemExit("%s needs the sample rate of the campaign (the scope did not report one): it turns the "
                         "trace into one feature per core clock cycle" % model.name)
    s_hat = None
    if dk_ref is not None:
        import hashlib
        if hashlib.sha3_256(dk_ref).digest() != dk_hash_in_file:
            raise SystemExit("--dk-ref does not match the key this file was captured under "
                             "(SHA3-256(dk) mismatch) -- wrong file or wrong key")
        s_hat = R.s_hat_from_dk(dk_ref)
    u_hat = R.u_hat_from_ct(cts)
    causal = s_hat is None              # blind: only what the attack has recovered so far may enter a hypothesis
    phase = AC.decrypt_phase(traces_full, rate)
    candidates = np.arange(R.Q, dtype=np.int64)
    results, offs, infos = [], [], []
    n_coeffs = 256 if coeffs <= 0 else min(256, coeffs)
    recovered = np.zeros((R.K, 256), dtype=np.int64)
    with ThreadPoolExecutor(_workers()) as pool:
        for poly in range(R.K):
            Z, first, info = AC.poly_features(traces_full, rate, poly, mode, phase)
            infos.append(info)
            guess = LM.Guess(u_hat, s_hat if s_hat is not None else recovered, poly)
            for j in range(n_coeffs):
                guess.at(j)
                guess.true_value = int(s_hat[poly][j]) if s_hat is not None else -1
                res = AC.attack_coefficient(model, guess, Z, first, C.BMUL_READ_CYCLES[poly], offsets, candidates,
                                            causal, pool)
                if res["best_guess"] is not None:
                    recovered[poly, j] = res["best_guess"]
                    offs.append(res["best_sample"])
                results.append({k: v for k, v in res.items() if k not in ("candidates", "peak_per_cand")})
                if progress_cb:
                    progress_cb(len(results), R.K * n_coeffs)
    hist = {int(o): int(c) for o, c in zip(*np.unique(offs, return_counts=True))} if offs else {}
    # A best offset piling up on the first or last value searched means the real alignment is
    # outside the range: the search is railing, not converging, and every row is then read from
    # the wrong cycle. Say so rather than reporting the edge as if it were the answer.
    railed = ""
    if hist:
        top = max(hist, key=lambda o: hist[o])
        if top in (min(offsets), max(offsets)) and hist[top] > 0.25 * sum(hist.values()):
            railed = ("the best offset is %+d, the edge of the searched range %+d..%+d, for %d%% of the "
                      "coefficients -- the real alignment is outside it: widen --offsets"
                      % (top, min(offsets), max(offsets), round(100 * hist[top] / sum(hist.values()))))
    summary = {
        "file": h5_path, "n_traces_used": n_ok, "model": model.name, "model_title": model.title,
        "target": model.register, "rprev": "", "oracle_assisted": s_hat is not None,
        "known_key_eval": s_hat is not None, "coefficients_attacked": len(results),
        "coefficients_per_poly": n_coeffs, "results": results,
        "features": {"mode": mode, "phase_samples": infos[0]["phase"], "samples_per_cycle": infos[0]["samples_per_cycle"],
                     "offsets_searched": list(offsets), "offset_histogram": hist, "unit": "row",
                     "offsets_railed": railed, "rows": results[0]["rows"] if results else 0},
    }
    _score_summary(summary, results, s_hat is not None)
    return summary


def _score_summary(summary: dict, results: list, scored: bool) -> None:
    """The rank statistics of an oracle-assisted run, added to ``summary`` in place.

    Coefficients the campaign cannot discriminate (:func:`informative`) are counted separately
    and left out of every statistic: they are not failures of the attack, they are questions
    this set of ciphertexts never asked.
    """
    dead = [r_ for r_ in results if not r_.get("informative", True)]
    summary["n_informative"] = len(results) - len(dead)
    summary["n_uninformative"] = len(dead)
    if dead:
        per_poly = {}
        for r_ in dead:
            per_poly[r_["poly"]] = per_poly.get(r_["poly"], 0) + 1
        summary["uninformative_per_poly"] = per_poly
    n_scored = sum(1 for r_ in results if r_["true_rank"] is not None)
    if scored and n_scored:
        ranks = [r_["true_rank"] for r_ in results if r_["true_rank"] is not None]
        corrs = [r_["true_corr"] for r_ in results if r_["true_corr"] is not None]
        summary["rank1_fraction"] = sum(1 for rk in ranks if rk == 1) / len(ranks)
        summary["median_true_rank"] = float(np.median(ranks))
        summary["mean_true_corr"] = float(np.mean(corrs))
        summary["n_candidates"] = int(R.Q)
        summary["chance_rank_mean"] = float(R.Q / 2)


def run_attack(h5_path: str, model, rprev: str, max_traces: int, plot_dir: str | None,
                dk_ref: bytes | None, features: str = "auto", offsets=None, progress_cb=None,
                coeffs: int = 0) -> dict:
    """The full 768-coefficient attack with ``model`` (a name in ``models/``).

    ``features`` / ``offsets`` only matter for a ``pipe_*`` model (see the module docstring);
    ``progress_cb(done, total)`` is called after every coefficient; ``coeffs`` > 0 attacks only the
    first ``coeffs`` coefficients of each polynomial (a quick look -- the summary says so).
    """
    model, rprev = _model(model, rprev)
    if model.aligned:
        return _run_aligned(h5_path, model, max_traces, plot_dir, dk_ref, features, offsets, progress_cb, coeffs)
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
        # coefficients (see models/base.py: Guess); for a from-
        # scratch attack with no dk-ref, substitute the already-recovered
        # guesses so far, in ascending coefficient order, exactly as a real
        # sequential CPA on this target would have to.
        traces = slice_window(traces_full, poly, bmul_windows, sample_rate)
        # `recovered` (not a copy) so that Guess.s, a view into it, picks
        # up each coefficient's guess as soon as it is written below -- the
        # second coefficient of a pair (w, e=1) then sees the first one's
        # just-made guess instead of a stale 0. The first coefficient of
        # each pair still has no better guess for its partner than 0: that
        # chicken-and-egg weakness is inherent to guessing one coefficient
        # at a time and is not fixed by this (see README caveats).
        guess = LM.Guess(u_hat, s_hat if s_hat is not None else recovered, poly)
        # Candidates are coefficient VALUES mod q (0..3328, canonical ML-KEM
        # range), never a function of the register's bit width.
        candidates = np.arange(R.Q, dtype=np.int64)
        for j in range(256 if coeffs <= 0 else min(256, coeffs)):
            guess.at(j)
            guess.true_value = int(s_hat[poly][j]) if s_hat is not None else -1
            res = attack_coefficient(model, guess, rprev, traces, candidates)
            recovered[poly, j] = res["best_guess"]
            results.append({k: v for k, v in res.items() if k not in ("corr_curve", "candidates", "peak_per_cand")})
            if plot_dir and j < 4:  # a handful of example plots, not all 256*3
                plot_coefficient(res, poly, j, plot_dir)
            if progress_cb:
                progress_cb(len(results), R.K * (256 if coeffs <= 0 else min(256, coeffs)))

    summary = {
        "file": h5_path, "n_traces_used": n_ok, "model": model.name, "model_title": model.title,
        "target": model.register, "rprev": rprev,
        "oracle_assisted": s_hat is not None,  # True <=> --dk-ref given; see module docstring
        "known_key_eval": s_hat is not None,  # deprecated alias, kept for older report consumers
        "coefficients_attacked": len(results),
        "coefficients_per_poly": 256 if coeffs <= 0 else min(256, coeffs),
        "results": results,
    }
    _score_summary(summary, results, s_hat is not None)
    return summary


def _curve_aligned(h5_path, model, poly, coeff, max_traces, dk_ref, features, offsets) -> dict:
    """The coefficient inspector for a pipeline model: the correlation of each row's hypothesis with its
    cycle (at the best cycle offset), for the best guess and, when the key is known, for the true value."""
    mode, offsets = feature_mode(model, features), parse_offsets(offsets)
    with TraceReader(h5_path) as r:
        _require_cpa(r)
        n = min(r.n_completed, max_traces) if max_traces else r.n_completed
        ok = r.ok[:n]
        traces_full = r.traces[:n][ok]
        cts = r.ciphertexts[:n][ok]
        dk_hash_in_file = r.dk_hash
        rate = r.sample_rate
    n_ok = traces_full.shape[0]
    if n_ok < 20:
        raise SystemExit("only %d usable traces; need at least 20" % n_ok)
    if rate <= 0:
        raise SystemExit("%s needs the sample rate of the campaign" % model.name)
    s_hat = None
    if dk_ref is not None:
        import hashlib
        if hashlib.sha3_256(dk_ref).digest() != dk_hash_in_file:
            raise SystemExit("--dk-ref does not match the key this file was captured under")
        s_hat = R.s_hat_from_dk(dk_ref)
    u_hat = R.u_hat_from_ct(cts)
    Z, first, _info = AC.poly_features(traces_full, rate, poly, mode, AC.decrypt_phase(traces_full, rate))
    t0 = C.BMUL_READ_CYCLES[poly]
    guess = LM.Guess(u_hat, s_hat if s_hat is not None else np.zeros((R.K, 256), dtype=np.int64), poly, coeff)
    candidates = np.arange(R.Q, dtype=np.int64)
    causal = s_hat is None
    with ThreadPoolExecutor(_workers()) as pool:
        sc, k = AC.aligned_scores(model, guess, Z, first, t0, offsets, candidates, causal, pool)
    per = sc.max(axis=1)
    best = int(np.argmax(per))
    off_best = int(np.asarray(offsets)[int(sc[best].argmax())])
    cur = AC.row_correlations(model, guess, Z, first, t0, off_best, [best], causal)
    rows = sorted(cur)
    out = {
        "poly": poly, "coeff": coeff, "model": model.name, "target": model.register, "rprev": "",
        "n_traces": n_ok, "n_samples": len(rows), "unit": "row", "rows": rows, "offset": off_best,
        "best_guess": int(candidates[best]), "best_corr": float(per[best]) / max(k, 1),
        "best_curve": [round(float(cur[r][0]), 5) for r in rows],
        "oracle_assisted": s_hat is not None,
    }
    if s_hat is not None:
        true_idx = int(guess.true_value)
        off_true = int(np.asarray(offsets)[int(sc[true_idx].argmax())])
        tc = AC.row_correlations(model, guess, Z, first, t0, off_true, [true_idx], causal)
        out["true_value"] = true_idx
        out["true_rank"] = int(np.where(np.argsort(per)[::-1] == true_idx)[0][0]) + 1
        out["true_curve"] = [round(float(tc[r][0]), 5) if r in tc else 0.0 for r in rows]
    return out


def correlation_curve_for_coefficient(h5_path: str, model, rprev: str, poly: int, coeff: int,
                                       max_traces: int, dk_ref: bytes | None, features: str = "auto",
                                       offsets=None) -> dict:
    """Full per-sample correlation curve for ONE coefficient -- for the GUI's
    "Correlation vs. Time" chart. Fast (one coefficient, not all 768): this
    is a preview of a single guess in isolation, not a step of the
    sequential attack, so its pair-partner/accumulator is modelled as 0
    when no --dk-ref is given (same convention a from-scratch attack would
    use for the very first coefficient of a pair -- see models/base.py: Guess
    and cpa_toolkit/README.md section 7 point 4 on the "chicken-and-egg" limitation).
    """
    model, rprev = _model(model, rprev)
    if not 0 <= poly < R.K:
        raise ValueError("poly must be 0..%d" % (R.K - 1))
    if not 0 <= coeff < 256:
        raise ValueError("coeff must be 0..255")
    if model.aligned:
        return _curve_aligned(h5_path, model, poly, coeff, max_traces, dk_ref, features, offsets)
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

    guess = LM.Guess(u_hat, s_hat if s_hat is not None else np.zeros((R.K, 256), dtype=np.int64), poly, coeff)
    candidates = np.arange(R.Q, dtype=np.int64)
    hyp = model.hypotheses(guess, rprev, candidates)
    corr = pearson_matrix(hyp, traces)                 # (Q, T)
    peak_per_cand = np.max(np.abs(corr), axis=1)
    best_idx = int(np.argmax(peak_per_cand))

    out = {
        "poly": poly, "coeff": coeff, "model": model.name, "target": model.register, "rprev": rprev,
        "n_traces": n_ok, "n_samples": int(traces.shape[1]),
        "best_guess": int(candidates[best_idx]),
        "best_corr": float(peak_per_cand[best_idx]),
        "best_curve": [round(float(v), 5) for v in corr[best_idx]],
        "oracle_assisted": s_hat is not None,
    }
    if s_hat is not None:
        true_idx = int(guess.true_value)  # candidates[i] == i, so the value IS the index
        out["true_value"] = true_idx
        out["true_rank"] = int(np.where(np.argsort(peak_per_cand)[::-1] == true_idx)[0][0]) + 1
        out["true_curve"] = [round(float(v), 5) for v in corr[true_idx]]
    return out


def default_checkpoints(n_max: int) -> list[int]:
    """A handful of trace-count checkpoints for --curve, roughly log-spaced."""
    fracs = (0.05, 0.1, 0.2, 0.35, 0.5, 0.75, 1.0)
    pts = sorted({min(n_max, max(20, int(round(n_max * f)))) for f in fracs})
    return pts


def run_curve(h5_path: str, model, rprev: str, max_traces: int, dk_ref: bytes,
              checkpoints: list[int] | None = None, features: str = "auto", offsets=None) -> dict:
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
    model, rprev = _model(model, rprev)
    with TraceReader(h5_path) as r:
        _require_cpa(r)
        n_avail = min(r.n_completed, max_traces) if max_traces else r.n_completed
    checkpoints = checkpoints or default_checkpoints(n_avail)
    rows, mtd = [], None
    for n in checkpoints:
        s = run_attack(h5_path, model, rprev, n, None, dk_ref, features, offsets)
        row = {
            "n_traces": s["n_traces_used"],
            "rank1_fraction": s.get("rank1_fraction", 0.0),
            "median_rank": s.get("median_true_rank", float("nan")),
            "mean_true_corr": s.get("mean_true_corr", float("nan")),
        }
        rows.append(row)
        if mtd is None and row["rank1_fraction"] > 0.5:
            mtd = row["n_traces"]
    return {"oracle_assisted": True, "model": model.name, "target": model.register, "rprev": rprev,
            "checkpoints": rows, "mtd": mtd}


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
    ap.add_argument("file", nargs="?", help="HDF5 campaign file from acquire_cpa.py")
    ap.add_argument("--list-models", action="store_true", help="list the leakage models in models/ and exit")
    ap.add_argument("--model", default=C.CPA_MODEL, choices=LM.names() + sorted(LM.ALIASES),
                     help="leakage model, a file in models/ (default from config.py's CPA_MODEL: %s)"
                          % C.CPA_MODEL)
    ap.add_argument("--rprev", default="", choices=[""] + list(LM.RPREVS),
                     help="R_prev, one the model accepts (default: config.py's CPA_RPREV for the default "
                          "model, else the model's first). Options: %s"
                          % "; ".join("%s = %s" % kv for kv in LM.RPREVS.items()))
    ap.add_argument("--max-traces", type=int, default=0, help="0 = use every trace in the file")
    ap.add_argument("--coeffs", type=int, default=0,
                     help="attack only the first N coefficients of each polynomial (0 = all 256): a quick look at "
                          "a campaign or a model before committing to the full 768")
    ap.add_argument("--features", default="auto", choices=("auto",) + CF.MODES,
                     help="pipe_* models: how a trace becomes one value per core clock cycle (default: pca = the "
                          "probe's per-cycle waveform learned from the traces; energy / abs need no shape)")
    ap.add_argument("--offsets", default="",
                     help="pipe_* models: integer cycle offsets between the schedule and the trace to search, "
                          "'-6:6' (default) or a single value once known (printed with the result)")
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

    if args.list_models:
        for m in LM.scan()[0]:
            print("%-18s %-4s %-8s R_prev: %-22s %s" % (m.name, m.family, m.register,
                                                         " / ".join(m.rprevs) or "-", m.equation))
        return
    if not args.file:
        ap.error("the campaign file is required")
    model = LM.get(args.model)
    rprev = args.rprev or (C.CPA_RPREV if model.name == LM.resolve(C.CPA_MODEL) and C.CPA_RPREV in model.rprevs else "")
    rprev = model.resolve_rprev(rprev)

    dk_ref = open(args.dk_ref, "rb").read() if args.dk_ref else None
    if args.curve and dk_ref is None:
        raise SystemExit("--curve needs --dk-ref (oracle-assisted evaluation): there is no way "
                         "to know when the attack has succeeded without a ground-truth key")

    def progress(done, total):
        if done % 64 == 0 or done == total:
            print("  %d / %d coefficients" % (done, total), file=sys.stderr)

    summary = run_attack(args.file, model, rprev, args.max_traces, args.plots or None, dk_ref, args.features,
                         args.offsets, progress, args.coeffs)

    print("file:            %s" % summary["file"])
    print("traces used:     %d" % summary["n_traces_used"])
    print("model:           %s  (register %s, R_prev %s)" % (model.name, model.register, rprev or "-"))
    print("                 %s" % model.equation)
    print("coefficients:    %d (of 768)%s" % (summary["coefficients_attacked"],
          "" if summary["coefficients_per_poly"] == 256 else "   <-- partial run (--coeffs)"))
    dead = summary.get("n_uninformative", 0)
    if dead:
        per = summary.get("uninformative_per_poly", {})
        print("\n################################################################")
        print("# %d of %d COEFFICIENTS CARRY NO KEY INFORMATION IN THIS CAMPAIGN" % (dead, len(summary["results"])))
        print("# per polynomial: %s" % ", ".join("s%d: %d" % (p, n) for p, n in sorted(per.items())))
        print("# For these the hypothesis does not change with the guess (or not between")
        print("# traces), so no model can rank the true value above chance. This is the")
        print("# ciphertexts, not the attack: a --strategy structured set leaves u_hat")
        print("# exactly zero for the polynomials it does not target. Capture with")
        print("# --strategy valid to attack the whole key.")
        print("################################################################")
    ft = summary.get("features")
    if ft:
        top = sorted(ft["offset_histogram"].items(), key=lambda kv: -kv[1])[:3]
        print("features:        %s, one per core clock cycle (%.2f samples/cycle, cell starts at sample %.2f)"
              % (ft["mode"], ft["samples_per_cycle"], ft["phase_samples"]))
        print("cycle offsets:   best guess per coefficient at offset %s of %d searched -- a pile-up at one offset "
              "means the alignment is real; rerun with --offsets <that value>"
              % (", ".join("%+d (%d)" % kv for kv in top), len(ft["offsets_searched"])))
        if ft.get("offsets_railed"):
            print("WARNING:         %s" % ft["offsets_railed"])
    best = [r_["best_corr"] for r_ in summary["results"]]
    if best:
        # the largest |rho| of pure noise over G guesses x T samples (x offsets) for n traces
        t = ft["rows"] if ft else 1
        trials = R.Q * (len(ft["offsets_searched"]) if ft else 4400)
        print("peak |rho|:      mean %.3f, max %.3f   (noise alone gives about %.3f at this trace count%s)"
              % (np.mean(best), np.max(best), np.sqrt(2 * np.log(trials) / (summary["n_traces_used"] * t)),
                 ", averaged over %d rows" % t if ft else ""))
    if summary["oracle_assisted"]:
        print("\n################################################################")
        print("# ORACLE-ASSISTED EVALUATION (--dk-ref given)")
        print("# This scores the attack against a real key for reporting only.")
        print("# It is NOT a blind attack result -- do not present it as one.")
        print("################################################################")
        print("rank-1 fraction:    %.1f%%  (of the %d coefficients this campaign can discriminate)"
              % (100 * summary["rank1_fraction"], summary.get("n_informative", len(summary["results"]))))
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
        curve = run_curve(args.file, model, rprev, args.max_traces, dk_ref, features=args.features, offsets=args.offsets)
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
