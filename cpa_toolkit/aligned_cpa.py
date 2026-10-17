"""Row-aligned CPA: a hypothesis per clock cycle, summed at the cycles the schedule puts them.

``cpa_attack.attack_coefficient`` correlates one hypothesis with every sample of the BMUL window
and keeps the best sample. That throws away what the RTL tells us: the pipeline holds data of the
attacked word in ~17 consecutive cycles (``mlkem_pipeline.py``), and each of them carries an
independent piece of evidence. A :class:`models.PipelineModel` gives one hypothesis per cycle
``k`` after the word's read ``T``; here each is correlated with the trace feature of *its own*
cycle ``T_BMUL + 2 w + k + m`` (``m``: a small search over the integer cycle offset between the
trigger edge and sample 0, which only the setup knows) and the correlations are added. For the
true key every term is positive; for a wrong guess they are noise, which adds in quadrature
(sqrt(K) instead of K), so a model with K rows gains about sqrt(K) in effective correlation.

The features are one value per core clock cycle (``cycle_features.py``).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import numpy as np

import config as C
import cycle_features as CF

#: Integer cycle offsets between the nominal schedule and the trace that are tried.
DEFAULT_OFFSETS = tuple(range(-6, 7))
#: Cycles kept before / after the BMUL's own cycles, so every offset of the search stays in the window.
PAD = 14
#: Candidates per hypothesis block (the hypotheses of a block fit the CPU cache).
CHUNK = 256
#: Cycles from the first read to the last row of the last word (2 * 127 + 20) plus the drain.
BMUL_SPAN = 2 * 128 + 30


def standardize(x: np.ndarray) -> np.ndarray:
    """Columns to zero mean and unit variance (float32); a constant column becomes all zero."""
    x = np.asarray(x, dtype=np.float32)
    x = x - x.mean(axis=0, keepdims=True)
    sd = x.std(axis=0, keepdims=True)
    return x / np.where(sd > 0, sd, np.inf)


def window(poly: int) -> tuple[int, int]:
    """``(first absolute cycle, number of cycles)`` of the feature window of decryption BMUL ``poly``."""
    return C.BMUL_READ_CYCLES[poly] - PAD, BMUL_SPAN + 2 * PAD


def decrypt_phase(traces: np.ndarray, rate_hz: float) -> float:
    """The clock phase of the record, from the whole decryption (all three BMULs and the NTTs between them)."""
    return CF.estimate_phase(traces, rate_hz, C.BMUL_READ_CYCLES[0], C.BMUL_READ_CYCLES[2] - C.BMUL_READ_CYCLES[0] + BMUL_SPAN)


def poly_features(traces: np.ndarray, rate_hz: float, poly: int, mode: str, phase: float) -> tuple[np.ndarray, int, dict]:
    """``(Z, first_cycle, info)``: standardized per-cycle features of decryption BMUL ``poly``."""
    first, n = window(poly)
    need = int((first + n + 1) * CF.samples_per_cycle(rate_hz)) + 2
    if traces.shape[1] < need:
        raise SystemExit("BMUL %d needs %.1f us of record after the trigger (%d samples at %.4g Sa/s); the traces have "
                         "%d" % (poly, need / rate_hz * 1e6, need, rate_hz, traces.shape[1]))
    f, info = CF.features(traces, rate_hz, first, n, mode, phase=phase)
    return standardize(f), first, info


def _chunk(model, guess, cand, Z, base_col, offsets, causal):
    h = model.row_hypotheses(guess, cand, causal)
    n = Z.shape[0]
    s = np.zeros((len(cand), len(offsets)), dtype=np.float32)
    for k, hk in h.items():
        cols = base_col + k + offsets
        ok = (cols >= 0) & (cols < Z.shape[1])
        if ok.any():
            s[:, ok] += standardize(hk).T @ Z[:, cols[ok]] / n
    return s, len(h)


def aligned_scores(model, guess, Z: np.ndarray, first_cycle: int, t0: int, offsets, candidates: np.ndarray,
                   causal: bool, pool: ThreadPoolExecutor | None = None) -> tuple[np.ndarray, int]:
    """``(S, K)``: ``S[g, m]`` the sum over the model's rows of the correlation of the hypothesis for
    candidate ``g`` with the feature of its cycle at offset ``offsets[m]``; ``K`` is the number of rows.

    ``t0``: the cycle of the first read of the BMUL (``config.BMUL_READ_CYCLES``); ``guess``: a
    ``models.Guess`` positioned on the attacked coefficient.
    """
    offsets = np.asarray(offsets, dtype=np.int64)
    base_col = t0 + 2 * (guess.j >> 1) - first_cycle
    chunks = [candidates[i:i + CHUNK] for i in range(0, len(candidates), CHUNK)]
    run = lambda c: _chunk(model, guess, c, Z, base_col, offsets, causal)
    parts = list(pool.map(run, chunks)) if pool is not None and len(chunks) > 1 else [run(c) for c in chunks]
    return np.concatenate([p[0] for p in parts], axis=0), parts[0][1]


def attack_coefficient(model, guess, Z, first_cycle, t0, offsets, candidates, causal, pool=None) -> dict:
    """Row-aligned CPA over one secret coefficient; the same fields as ``cpa_attack.attack_coefficient``.

    ``best_corr`` / ``true_corr`` are the mean over the model's rows of the correlation (so they are
    comparable with a single-sample |rho|); ``best_sample`` is the cycle offset of the best guess.
    """
    s, k = aligned_scores(model, guess, Z, first_cycle, t0, offsets, candidates, causal, pool)
    per = s.max(axis=1)                                   # best offset of every candidate
    order = np.argsort(per)[::-1]
    true_rank = true_corr = None
    if guess.true_value in candidates:
        gi = int(np.where(candidates == guess.true_value)[0][0])
        true_rank = int(np.where(order == gi)[0][0]) + 1
        true_corr = float(per[gi]) / max(k, 1)
    best = int(order[0])
    return {"poly": guess.poly, "coeff": guess.j, "target": model.register, "rprev": "",
            "best_guess": int(candidates[best]), "best_corr": float(per[best]) / max(k, 1),
            "best_sample": int(np.asarray(offsets)[int(s[best].argmax())]),
            "true_value": guess.true_value, "true_rank": true_rank, "true_corr": true_corr,
            "candidates": candidates, "peak_per_cand": per / max(k, 1), "rows": k}


def row_correlations(model, guess, Z, first_cycle, t0, offset: int, values, causal: bool) -> dict[int, np.ndarray]:
    """``{row: (len(values),)}`` correlation of each row's hypothesis with its cycle, at one cycle ``offset``.

    For the GUI's coefficient inspector (one curve over the rows for the best and for the true guess).
    """
    values = np.asarray(values, dtype=np.int64)
    h = model.row_hypotheses(guess, values, causal)
    base_col = t0 + 2 * (guess.j >> 1) - first_cycle + int(offset)
    out = {}
    for k, hk in sorted(h.items()):
        col = base_col + k
        if 0 <= col < Z.shape[1]:
            out[k] = (standardize(hk).T @ Z[:, col] / Z.shape[0]).astype(np.float64)
    return out
