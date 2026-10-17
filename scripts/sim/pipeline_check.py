#!/usr/bin/env python3
"""Does ``mlkem_pipeline.py`` reproduce the RTL's register toggling exactly?

Runs Decaps on N ciphertexts in the Verilator model (``tb_core.sv`` +hd=) and,
for every register the software twin covers (both multipliers' stage registers
and the registers that copy their output stream), compares the twin's Hamming
distance with the toggle count the RTL produced, at every interior row of the
three decryption BMULs. Any mismatch means the twin (or the schedule it encodes)
differs from the hardware and no hypothesis built on it can be trusted.

    python scripts/sim/pipeline_check.py            # exit status 1 on any mismatch
    python scripts/sim/pipeline_check.py -n 60
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

import mlkem_pipeline as MP  # noqa: E402
import mlkem_ref as R  # noqa: E402
import register_leakage as RL  # noqa: E402
import testvectors as TV  # noqa: E402

#: Words checked: far from both ends of the BMUL, where the pipeline holds neighbours on every stage.
WORDS = range(8, 100)


def twin_hd(reg_parts, u_poly: np.ndarray, s_poly: np.ndarray, w: int, phase: str) -> np.ndarray:
    """The twin's toggle count of one register in the ``enter`` or ``leave`` transition of word ``w``: ``(N,)``."""
    total = 0
    ops = MP.WordOps(u_poly, s_poly, w)
    for m, field, _d in reg_parts:
        stream = ops.mul0 if m == 0 else ops.mul1
        x, y = (stream("prev"), stream("cur")) if phase == "enter" else (stream("cur"), stream("next"))
        total = total + MP.transition_hd((m, field, 0), x, y)
    return np.asarray(total).reshape(-1)


def check(run: RL.Run, u: np.ndarray, s: np.ndarray) -> dict[str, int]:
    """``register -> number of (poly, word, phase) cases where the twin and the RTL differ``."""
    bad = {}
    idx = {n: i for i, n in enumerate(run.names)}
    for name, parts in MP.registers().items():
        d = parts[0][2]
        n_bad = 0
        for poly in range(3):
            for w in WORDS:
                for phase, off in (("enter", 3), ("leave", 4)):
                    row = 2 * w + 1 + off + d
                    rtl = run.hd[:, poly, row, idx[name]].astype(np.int64)
                    if not np.array_equal(rtl, twin_hd(parts, u[:, poly], s[poly], w, phase)):
                        n_bad += 1
        bad[name] = n_bad
    return bad


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-n", "--traces", type=int, default=40)
    args = ap.parse_args()
    RL.build_model()
    vs = TV.generate("cpa", args.traces, bytes(range(64)), "valid")
    with tempfile.TemporaryDirectory(prefix="pipe_chk_") as tmp:
        run = RL.simulate(vs.dk, vs.ct, tmp, "a")
    bad = check(run, R.u_hat_from_ct(vs.ct), R.s_hat_from_dk(vs.dk))
    cases = 3 * len(WORDS) * 2
    for name, n in bad.items():
        print("%-14s %s" % (name, "exact (%d cases)" % cases if n == 0 else "%d of %d cases differ" % (n, cases)))
    if any(bad.values()):
        raise SystemExit(1)
    print("\nthe twin reproduces the RTL toggle count of all %d registers at every checked row" % len(bad))


if __name__ == "__main__":
    main()
