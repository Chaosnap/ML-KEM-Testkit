"""Leakage assessment of a captured trace set.

Welch's t-test between the fixed and random classes, computed per sample
point. This is the measured counterpart of the simulated TVLA in
``scripts/tvla_sim.py``: the same statistic, applied to real traces instead
of Hamming distances taken from a VCD.

Reading the result
------------------

``|t| > 4.5`` at a sample point means the mean power at that point depends
on the input, i.e. first-order leakage. The threshold comes from a
confidence of about 99.999% at a single point, but a trace has thousands of
points, so with pure noise the largest ``|t|`` over a whole trace is already
about ``sqrt(2 ln N)`` (roughly 4.3 for 10 000 points). A single sample just
above 4.5 is therefore not evidence of anything; a run of adjacent points
well above it is.

For that reason :func:`assess` reports the noise floor alongside the result,
and the recommended control is to capture a *fixed-versus-fixed* set with the
same setup (``--fixed-vs-fixed``): both classes get identical inputs, so any
real leakage is impossible by construction, and whatever the test reports is
the false-positive level of the measurement setup itself. A campaign whose
control already shows large ``|t|`` has a setup problem - trigger jitter,
drift, or the two classes not being properly interleaved - and its main
result cannot be interpreted.

For countermeasure work the quantity to track between versions is not the
peak ``|t|`` alone, which grows as ``sqrt(N)`` with the number of traces, but
the number of traces needed before leakage crosses the threshold. The
``--detection-curve`` option of :func:`assess` records ``max|t|`` as a
function of the trace count for exactly that comparison.
"""

from __future__ import annotations

import math

import numpy as np

#: Conventional TVLA threshold on |t|.
THRESHOLD = 4.5


def welch_t(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Welch's t per column between two sets of traces."""
    na, nb = a.shape[0], b.shape[0]
    if na < 2 or nb < 2:
        return np.zeros(a.shape[1])
    ma, mb = a.mean(axis=0), b.mean(axis=0)
    va, vb = a.var(axis=0, ddof=1), b.var(axis=0, ddof=1)
    denom = np.sqrt(va / na + vb / nb)
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(denom > 0, (ma - mb) / denom, 0.0)
    return np.nan_to_num(t)


def noise_floor(n_samples: int) -> float:
    """Expected largest |t| over ``n_samples`` points under the null."""
    return math.sqrt(2 * math.log(max(n_samples, 2)))


def assess(ts, detection_curve: bool = False, steps: int = 20) -> dict:
    """Run the t-test over a :class:`~tracelab.storage.TraceSet`.

    :param ts: a loaded trace set.
    :param detection_curve: also compute ``max|t|`` against trace count.
    """
    traces = np.asarray(ts.traces, dtype=np.float64)
    classes = np.asarray(ts.classes)
    if traces.shape[0] < 4:
        raise SystemExit("need at least 4 traces, have %d" % traces.shape[0])

    fixed, random = traces[classes == 0], traces[classes == 1]
    if len(fixed) < 2 or len(random) < 2:
        raise SystemExit(
            "need at least 2 traces per class, have %d fixed and %d random"
            % (len(fixed), len(random))
        )

    t = welch_t(fixed, random)
    a = np.abs(t)
    k = int(np.argmax(a))
    floor = noise_floor(len(t))
    leaky = a > THRESHOLD

    result = {
        "traces": int(traces.shape[0]),
        "fixed": int(len(fixed)),
        "random": int(len(random)),
        "samples": int(traces.shape[1]),
        "max_abs_t": float(a[k]),
        "max_at": k,
        "noise_floor": floor,
        "leaky_samples": int(leaky.sum()),
        "leaky_pct": 100.0 * float(leaky.mean()),
        "leakage": bool(a[k] > THRESHOLD and leaky.sum() > 1),
        "t": t,
        "mean_fixed": fixed.mean(axis=0),
        "mean_random": random.mean(axis=0),
    }

    if detection_curve:
        curve = []
        order = np.arange(len(traces))
        lo = min(32, max(8, len(traces) // 10))
        for n in np.unique(np.linspace(lo, len(traces), steps).astype(int)):
            sel = order[:n]
            f, r = traces[sel][classes[sel] == 0], traces[sel][classes[sel] == 1]
            if len(f) >= 2 and len(r) >= 2:
                curve.append((int(n), float(np.abs(welch_t(f, r)).max())))
        result["detection_curve"] = curve
        result["traces_to_disclosure"] = traces_to_disclosure(curve)
    return result


def traces_to_disclosure(curve: list) -> int:
    """Smallest trace count from which ``max|t|`` stays above the threshold.

    A single crossing means little: with few traces ``max|t|`` fluctuates and
    can exceed 4.5 by chance. Requiring every later point to stay above it
    gives the number usually quoted when comparing a protected design with an
    unprotected one. Returns 0 if the curve never settles above the threshold.
    """
    settled = 0
    for n, m in reversed(curve):
        if m <= THRESHOLD:
            break
        settled = n
    return settled


def summary(res: dict) -> str:
    """One-screen report of :func:`assess`."""
    lines = [
        "traces        %d (%d fixed, %d random), %d samples"
        % (res["traces"], res["fixed"], res["random"], res["samples"]),
        "max |t|       %.1f at sample %d" % (res["max_abs_t"], res["max_at"]),
        "noise floor   %.2f  (expected max |t| of pure noise over %d samples)"
        % (res["noise_floor"], res["samples"]),
        "above %.1f     %d samples (%.2f%%)"
        % (THRESHOLD, res["leaky_samples"], res["leaky_pct"]),
    ]
    if res["leakage"]:
        lines.append("RESULT        first-order leakage detected")
    elif res["max_abs_t"] > THRESHOLD:
        lines.append("RESULT        isolated samples above the threshold: not conclusive")
    else:
        lines.append("RESULT        no leakage detected at this trace count")
    if res.get("detection_curve"):
        ttd = res.get("traces_to_disclosure", 0)
        lines.append(
            "detection     |t| stays above %.1f from %s traces on"
            % (THRESHOLD, ttd if ttd else "(never, up to %d)" % res["traces"])
        )
    return "\n".join(lines)


def plot(res: dict, path: str, title: str = "") -> str:
    """Write a two-panel figure: mean traces and the t-curve."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    n_panels = 3 if res.get("detection_curve") else 2
    fig, axes = plt.subplots(n_panels, 1, figsize=(13, 3.0 * n_panels))

    ax = axes[0]
    ax.plot(res["mean_fixed"], lw=0.5, color="black", label="fixed")
    ax.plot(res["mean_random"], lw=0.5, color="#1f77b4", alpha=0.8, label="random")
    ax.set_ylabel("mean sample")
    ax.legend(fontsize=8)
    ax.set_title(title or "Measured TVLA")

    ax = axes[1]
    ax.plot(res["t"], lw=0.5, color="#d62728")
    ax.axhline(THRESHOLD, color="red", ls="--", lw=0.8)
    ax.axhline(-THRESHOLD, color="red", ls="--", lw=0.8)
    ax.axhline(res["noise_floor"], color="gray", ls=":", lw=0.8)
    ax.axhline(-res["noise_floor"], color="gray", ls=":", lw=0.8)
    ax.set_ylabel("Welch t")
    ax.set_xlabel("sample")
    ax.text(
        0.995,
        0.95,
        "max |t| %.1f, %.2f%% of samples > %.1f"
        % (res["max_abs_t"], res["leaky_pct"], THRESHOLD),
        transform=ax.transAxes,
        ha="right",
        va="top",
        fontsize=8,
        bbox=dict(fc="white", alpha=0.85, lw=0),
    )

    if res.get("detection_curve"):
        ax = axes[2]
        xs = [n for n, _ in res["detection_curve"]]
        ys = [m for _, m in res["detection_curve"]]
        ax.plot(xs, ys, marker="o", ms=3, lw=1, color="#2ca02c")
        ax.axhline(THRESHOLD, color="red", ls="--", lw=0.8)
        ax.set_xlabel("traces")
        ax.set_ylabel("max |t|")
        ttd = res.get("traces_to_disclosure", 0)
        if ttd:
            ax.axvline(ttd, color="black", ls="-.", lw=0.8)
            ax.annotate("stays above %.1f\nfrom %d traces" % (THRESHOLD, ttd),
                        xy=(ttd, THRESHOLD), xytext=(6, 10), textcoords="offset points",
                        fontsize=7)
        ax.set_title("Detection curve", fontsize=9)

    fig.tight_layout()
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path
