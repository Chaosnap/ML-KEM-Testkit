#!/usr/bin/env python3
"""Do the CPA leakage models line up with what the RTL actually toggles?

``models/`` states, for every model, which register it hypothesises and in
which pipeline cycle it is valid (``pipeline_stage``). Those numbers were read
off ``mlkem_poly_alu.sv``. This script checks them against the cycle-accurate
simulation: Decaps is run on N ciphertexts with a fixed key, the toggle count
of every BMUL-datapath flop is recorded per clock cycle (``tb_core.sv`` +hd=),
and the summed toggling plays the role of the measured trace -- one sample per
core clock cycle, noise-free, equal weight per flop (the Hamming-distance
ceiling; see ``register_leakage.py``). Then, exactly as ``cpa_attack.py`` does:

* **alignment** -- for the *true* key, the correlation of a model's hypothesis
  with that signal at every cycle ``k`` after the read of the attacked word
  (``T``, the cycle its RAM read address is issued). The peak says where the
  model's register really is; a flat profile says the model is not in the
  hardware at all.
* **attack** (``--attack``) -- the full 3329-candidate CPA over the BMUL
  window, with Gaussian noise added to the simulated trace, rank-1 rate and
  median rank for a number of traces. Shows how many traces a model needs
  *relative to the others* at a given SNR; the absolute number on a real probe
  depends on how much of the chip's noise the probe adds.

    python scripts/sim/model_vs_rtl.py                    # alignment of every model
    python scripts/sim/model_vs_rtl.py --subset dsp       # as if the probe only saw the DSP48s
    python scripts/sim/model_vs_rtl.py --attack --snr 0.5 -n 400
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "cpa_toolkit"))

import mlkem_ref as R  # noqa: E402
import models as LM  # noqa: E402
import register_leakage as RL  # noqa: E402
import testvectors as TV  # noqa: E402

K_MAX = 26
WORDS = (10, 100)     # attacked words: steady state, every offset inside the window


def profile(model, rprev: str, u_hat: np.ndarray, s_hat: np.ndarray, sig: np.ndarray) -> np.ndarray:
    """Mean correlation of the true-key hypothesis with ``sig`` ``(N, 3, rows)``, per offset ``k`` after T.

    Returns ``(K_MAX + 1,)``, averaged over every attacked coefficient of the three BMULs.
    """
    acc = np.zeros(K_MAX + 1)
    n = 0
    for poly in range(3):
        g = LM.Guess(u_hat, s_hat, poly)
        for j in range(2 * WORDS[0], 2 * WORDS[1]):
            g.at(j)
            h = model.hypotheses(g, rprev, np.array([s_hat[poly, j]]))[:, 0].astype(np.float64)
            w = j >> 1
            for k in range(K_MAX + 1):
                acc[k] += RL.corr_over_traces(h, sig[:, poly, 2 * w + 1 + k])
            n += 1
    return acc / n


def attack(model, rprev: str, u_hat: np.ndarray, s_hat: np.ndarray, sig: np.ndarray, snr: float,
           coeffs: int, rng: np.random.Generator) -> tuple[float, float]:
    """Rank-1 fraction and median true rank of the full CPA on the noisy simulated window.

    ``snr`` is the ratio of the signal's standard deviation to the noise's, per sample.
    """
    import cpa_attack as CA
    cand = np.arange(R.Q, dtype=np.int64)
    ranks = []
    for poly in range(3):
        win = sig[:, poly, 1:RL.SPAN - 1]                               # the whole BMUL window, one sample per cycle
        noise = rng.normal(0.0, win.std() / snr, win.shape)
        traces = (win + noise).astype(np.float32)
        g = LM.Guess(u_hat, s_hat, poly)
        for j in rng.choice(np.arange(2 * WORDS[0], 2 * WORDS[1]), coeffs, replace=False):
            g.at(int(j))
            g.true_value = int(s_hat[poly, j])
            ranks.append(CA.attack_coefficient(model, g, rprev, traces, cand)["true_rank"])
    ranks = np.array(ranks)
    return float((ranks == 1).mean()), float(np.median(ranks))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-n", "--traces", type=int, default=300, help="ciphertexts to simulate")
    ap.add_argument("--subset", default="all", choices=("all", "dsp", "bram", "fabric"),
                    help="the flop group the 'probe' sees (default: all of them)")
    ap.add_argument("--attack", action="store_true", help="also run the full CPA on the noisy simulated window")
    ap.add_argument("--snr", type=float, default=0.5, help="--attack: signal std / noise std per sample")
    ap.add_argument("--coeffs", type=int, default=12, help="--attack: coefficients attacked per polynomial")
    ap.add_argument("--models", nargs="*", default=[], help="only these model files (default: all)")
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()

    RL.build_model()
    vs = TV.generate("cpa", args.traces, bytes(range(64)), "valid")
    with tempfile.TemporaryDirectory(prefix="model_rtl_") as tmp:
        print("simulating %d Decaps ..." % args.traces)
        run = RL.simulate(vs.dk, vs.ct, tmp, "a")
    sig = RL.total_toggles(run, args.subset)                 # (N, 3, rows): one sample per core cycle
    u_hat, s_hat = R.u_hat_from_ct(vs.ct), R.s_hat_from_dk(vs.dk)
    rng = np.random.default_rng(args.seed)

    chosen = [LM.get(n) for n in (args.models or LM.names())]
    print("\nTrue-key correlation with the simulated toggling of '%s' flops, by cycle k after the word's read T" % args.subset)
    print("(mean over %d coefficients x 3 BMULs; models state their stage in `pipeline_stage`)\n" % (3 * 2 * (WORDS[1] - WORDS[0])))
    print("%-18s %-10s %5s %7s   profile k=0..%d (|rho|, 0.1 per step)" % ("model", "R_prev", "k*", "rho*", K_MAX))
    rows = []
    for m in chosen:
        for rp in (m.rprevs or ("",)):
            prof = profile(m, rp, u_hat, s_hat, sig)
            k = int(np.argmax(np.abs(prof)))
            bar = "".join(" .:-=+*#%@"[min(9, int(abs(v) / 0.1 * 9 + 0.5))] for v in prof)
            print("%-18s %-10s %5d %7.3f   %s" % (m.name, rp or "-", k, prof[k], bar))
            rows.append((m, rp, prof[k]))
    if args.attack:
        print("\nFull CPA (3329 candidates) on the simulated window + noise, SNR %.2f, %d traces, %d coefficients/poly:\n"
              % (args.snr, args.traces, args.coeffs))
        print("%-18s %-10s %8s %12s" % ("model", "R_prev", "rank-1", "median rank"))
        for m, rp, _ in rows:
            r1, med = attack(m, rp, u_hat, s_hat, sig, args.snr, args.coeffs, rng)
            print("%-18s %-10s %7.0f%% %12.0f" % (m.name, rp or "-", 100 * r1, med))


if __name__ == "__main__":
    main()
