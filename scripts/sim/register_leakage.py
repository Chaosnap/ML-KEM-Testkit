#!/usr/bin/env python3
"""Which flops of the BMUL datapath carry the data-dependent switching, in the RTL.

An EM probe (or a shunt) sees the *sum* of everything that toggles at a clock
edge. A hypothesis about one register is therefore diluted by every other flop
switching in the same cycle: a perfect Hamming-distance model of register ``r``
reaches ``rho = corr(HD_r, HD_total)`` over traces. This script measures that in
the cycle-accurate RTL simulation (``testbench/core_sim``, ``+hd=``) for every
register of ``bmul_regs.svh``, over the three decryption BMULs, and groups the
registers by what they are made of (DSP48 operand / product registers, block
RAM output registers, fabric), because a near-field probe over one part of the
die sees mostly one group.

It also splits out registers that only hold public data (``u_hat``): Decaps is
re-run on the same ciphertexts with an unrelated ``s_hat``, and a register whose
toggling never changes is public and can say nothing about the key.

This is the **noise-free, equal-weight Hamming-distance ceiling**: no probe, no
transfer function, no measurement noise. It ranks where to look; it does not
predict a measured correlation. Pipeline depth is deliberately not reported
here (words issue every 2 cycles, so a cycle offset is ambiguous for a deep
register): ``model_vs_rtl.py`` finds the cycle at which each *model* lines up.

    python scripts/sim/register_leakage.py                 # 200 ciphertexts
    python scripts/sim/register_leakage.py -n 400 --csv regs.csv
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass

import numpy as np

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "cpa_toolkit"))

import config as C  # noqa: E402
import mlkem768 as KEM  # noqa: E402
import testvectors as TV  # noqa: E402

SIM_DIR = os.path.join(ROOT, "testbench", "core_sim")
SIM_BINARY = os.path.join(SIM_DIR, "obj_dir", "Vtb_core")

#: Rows of a BMUL (0 = the cycle before the first read T) examined: steady state, away from the edges.
ROWS = (2 * 10 + 1, 2 * 100 + 1)
#: Decryption BMULs end before this busy cycle (docs/ATTACK_MODEL.md 4.3).
DECRYPT_UNTIL = 4800
#: Rows kept per BMUL in a parsed run (128 words x 2 cycles + pipeline drain).
SPAN = 128 * 2 + 28


@dataclass
class Run:
    """The dump of one simulation: ``hd[job, bmul, row, reg]`` toggle counts."""
    names: list[str]
    bits: list[int]
    hd: np.ndarray           # (jobs, 3, SPAN, regs) uint8; row 0 is the cycle before the first read T
    t0: list[int]            # busy cycle of the first read (T) of each decryption BMUL


def build_model() -> None:
    """Build the Verilator model if needed (the acvp_test.py flow)."""
    r = subprocess.run(["make", "-C", SIM_DIR], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("Verilator build failed:\n%s%s" % (r.stdout, r.stderr))


def simulate(dk: bytes, cts: np.ndarray, workdir: str, tag: str) -> Run:
    """Decaps every ciphertext in ``cts`` ``(N, 1088)`` under ``dk`` and parse the dump."""
    jobs, res = os.path.join(workdir, tag + "_jobs.txt"), os.path.join(workdir, tag + "_res.txt")
    hd, tr = os.path.join(workdir, tag + "_hd.txt"), os.path.join(workdir, tag + "_trace.txt")
    with open(jobs, "w") as f:
        f.write("%d\n" % len(cts))
        for ct in cts:
            data = dk + bytes(ct.tobytes())
            f.write("2 768 %d\n%s\n" % (len(data), " ".join("%02x" % b for b in data)))
    r = subprocess.run([SIM_BINARY, "+jobs=" + jobs, "+out=" + res, "+hd=" + hd, "+trace=" + tr,
                        "+until=%d" % DECRYPT_UNTIL], capture_output=True, text=True)
    if r.returncode != 0 or "TB_CORE DONE" not in r.stdout:
        raise SystemExit("simulation failed:\n%s%s" % (r.stdout, r.stderr))
    return parse(hd, tr)


def parse(hd_path: str, trace_path: str) -> Run:
    """Read a ``+hd=`` dump and the ``+trace=`` timeline of the same run."""
    names, bits, rows = [], [], {}
    with open(hd_path) as f:
        for line in f:
            p = line.split()
            if p[0] == "REG":
                names.append(p[2])
                bits.append(int(p[3]))
            elif p[0] == "H":
                rows.setdefault(int(p[1]), []).append((int(p[2]), np.array(p[3:], dtype=np.uint8)))
    starts: list[int] = []
    with open(trace_path) as f:
        for line in f:
            p = line.split()
            if p and p[0] == "A" and p[1] == "0" and p[3] == "2":   # job 0, ALU op BMUL
                starts.append(int(p[2]))
    starts = starts[:3]
    if len(starts) < 3:
        raise SystemExit("did not find the three decryption BMULs in the timeline")
    t0 = [s + 2 for s in starts]          # alu start -> first RAM read two cycles later (T)
    out = np.zeros((len(rows), 3, SPAN, len(names)), dtype=np.uint8)
    for job, lst in rows.items():
        for cyc, vec in lst:
            for b, t in enumerate(t0):
                row = cyc - (t - 1)
                if 0 <= row < SPAN:
                    out[job, b, row] = vec
    return Run(names, bits, out, t0)


def physical_class(name: str) -> str:
    """What a register is built from: DSP48 operand/product registers (``mlkem_modmul`` stages 1-3 map to
    the DSP's A/B, M and P registers), block RAM output registers, or fabric flip-flops."""
    if name.startswith("ram."):
        return "bram"
    if name.split(".")[1] in ("a0", "b0", "p1", "p2"):
        return "dsp"
    return "fabric"


def corr_over_traces(x: np.ndarray, y: np.ndarray) -> float:
    """Pearson correlation over axis 0, 0 when either side is constant."""
    x = x - x.mean()
    y = y - y.mean()
    d = np.sqrt((x * x).sum() * (y * y).sum())
    return float((x * y).sum() / d) if d > 0 else 0.0


def total_toggles(run: Run, subset: str = "all") -> np.ndarray:
    """Summed toggles per (job, bmul, row) of the registers in ``subset`` (all / dsp / bram / fabric)."""
    keep = [i for i, n in enumerate(run.names) if subset == "all" or physical_class(n) == subset]
    return run.hd[..., keep].astype(np.float64).sum(axis=-1)


def analyse(a: Run, b: Run, subset: str = "all") -> list[dict]:
    """Per register: public or key-dependent, toggles per cycle, and how well a perfect HD model of it
    correlates with the summed toggling of ``subset`` (mean / best over the steady-state rows).

    ``a`` and ``b`` are the same ciphertexts under two different keys.
    """
    r0, r1 = ROWS
    total = total_toggles(a, subset)[:, :, r0:r1]                 # (job, bmul, rows)
    out = []
    for r, name in enumerate(a.names):
        x = a.hd[:, :, r0:r1, r].astype(np.float64)
        public = bool(np.array_equal(a.hd[:, :, r0:r1, r], b.hd[:, :, r0:r1, r]))
        rho = np.array([corr_over_traces(x[:, bm, row], total[:, bm, row])
                        for bm in range(3) for row in range(r1 - r0)])
        out.append({"reg": name, "bits": a.bits[r], "class": physical_class(name), "public": public,
                    "toggles": float(x.mean()), "rho_mean": float(np.abs(rho).mean()),
                    "rho_best": float(np.abs(rho).max())})
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-n", "--traces", type=int, default=200, help="ciphertexts to simulate")
    ap.add_argument("--seed", default="", help="hex seed of the vector set (default: fixed, reproducible)")
    ap.add_argument("--subset", default="all", choices=("all", "dsp", "bram", "fabric"),
                    help="what the probe sees: the summed toggling of this group only")
    ap.add_argument("--csv", default="", help="write the per-register table here")
    ap.add_argument("--top", type=int, default=20, help="rows to print")
    args = ap.parse_args()

    build_model()
    seed = bytes.fromhex(args.seed) if args.seed else bytes(range(64))
    vs = TV.generate("cpa", args.traces, seed, "valid")
    other_dk = KEM.keygen(bytes(range(100, 132)), bytes(range(200, 232)))[1]
    with tempfile.TemporaryDirectory(prefix="reg_leak_") as tmp:
        print("simulating %d Decaps x 2 keys ..." % args.traces)
        a = simulate(vs.dk, vs.ct, tmp, "a")
        b = simulate(other_dk, vs.ct, tmp, "b")
    rows = analyse(a, b, args.subset)

    print("\nToggling by group (equal weight per flop; share of all toggles in the BMUL window):")
    tot = sum(r["toggles"] for r in rows)
    for cls in ("dsp", "bram", "fabric"):
        g = [r for r in rows if r["class"] == cls]
        print("  %-7s %3d registers %5d bits  %5.1f%% of the toggling" % (
            cls, len(g), sum(r["bits"] for r in g), 100 * sum(r["toggles"] for r in g) / tot))
    pub = [r for r in rows if r["public"]]
    print("  public-only registers (identical under another key): %s" % (", ".join(r["reg"] for r in pub) or "none"))

    keyed = sorted((r for r in rows if not r["public"]), key=lambda r: -r["rho_mean"])
    print("\nRegisters ranked by corr(HD of the register, summed toggling of '%s'), mean |rho| over rows:\n" % args.subset)
    print("%-14s %-7s %5s %9s %8s %8s" % ("register", "group", "bits", "toggles", "mean|rho|", "best"))
    for r in keyed[:args.top]:
        print("%-14s %-7s %5d %9.1f %8.3f %8.3f" % (r["reg"], r["class"], r["bits"], r["toggles"], r["rho_mean"], r["rho_best"]))
    print("\nA single flop group a perfect model can name reaches |rho| of only a few tenths even here (no noise);")
    print("rho = 0.1 needs ~%d traces, 0.05 ~%d, 0.02 ~%d for a rank-1 coefficient (n ~ 28/rho^2)."
          % (28 / 0.1 ** 2, 28 / 0.05 ** 2, 28 / 0.02 ** 2))
    if args.csv:
        with open(args.csv, "w") as f:
            f.write("register,group,bits,public,toggles,rho_mean,rho_best\n")
            for r in rows:
                f.write("%s,%s,%d,%d,%.2f,%.4f,%.4f\n" % (r["reg"], r["class"], r["bits"], r["public"], r["toggles"],
                                                         r["rho_mean"], r["rho_best"]))
        print("table: %s" % args.csv)


if __name__ == "__main__":
    main()
