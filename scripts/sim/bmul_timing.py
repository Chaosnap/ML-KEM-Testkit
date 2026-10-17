#!/usr/bin/env python3
"""Measure the Decaps pipeline windows from a cycle-accurate RTL run.

Parses the instruction timeline (`+trace=`) and BMUL datapath dump
(`+bmul=`) that ``testbench/core_sim/tb_core.sv`` writes, and prints, in
core-clock cycles after ``start`` (the same cycles ``trig_out`` is high and
``CYCLE_COUNT`` counts):

* the three decryption BMUL windows (``ŝᵢ ∘ ûᵢ``) -- the CPA target, to
  compare against ``config.BMUL_WINDOWS``;
* the three decryption NTT windows and the decryption INTT window, to fill
  ``config.NTT_WINDOWS_CYCLES`` / ``INTT_WINDOW_CYCLES`` (CLAUDE.md gap #2);
* every other BMUL in Decaps (the re-encryption path), so a trace feature
  outside the decryption windows can be named instead of mistaken for a
  misplaced target (e.g. a TVLA peak in the FO re-encryption).

It does not touch the hardware. With no arguments it builds the Verilator model,
runs two Decaps through it and analyses the dumps; given a ``+trace=`` (and
optionally ``+bmul=``) file it analyses those instead. Re-run after any
RTL/microcode change and paste its "config.py" block into ``cpa_toolkit/config.py``,
exactly as the comment on ``BMUL_WINDOWS`` says.

    make sim-bmul-timing                                   # or:
    python scripts/sim/bmul_timing.py                      # build, run, analyse
    python scripts/sim/bmul_timing.py trace.txt bmul.txt   # analyse existing dumps
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from collections import defaultdict

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
SIM_DIR = os.path.join(ROOT, "testbench", "core_sim")
SIM_BINARY = os.path.join(SIM_DIR, "obj_dir", "Vtb_core")

# op codes (mlkem_poly_alu.sv OP_*): 0 NTT, 1 INTT, 2 BMUL, 3 ADD, 4 SUB.
ALU_OP = {0: "NTT", 1: "INTT", 2: "BMUL", 3: "ADD", 4: "SUB"}
# The microcode slot the three decryption products ŝᵢ∘ûᵢ accumulate into
# (gen_mlkem_ucode.py: S_ACC); their NTTs land in S_T. Re-encryption reuses
# other slots, so "decryption" is the first INTT's worth of operations.


def simulate(workdir: str, n_jobs: int = 2) -> tuple[str, str]:
    """Build the model if needed, run ``n_jobs`` Decaps, return the ``(trace, bmul)`` dump paths."""
    sys.path.insert(0, os.path.join(ROOT, "cpa_toolkit"))
    import testvectors as TV

    r = subprocess.run(["make", "-C", SIM_DIR], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("Verilator build failed:\n%s%s" % (r.stdout, r.stderr))
    vs = TV.generate("cpa", n_jobs, bytes(range(64)), "valid")
    jobs = os.path.join(workdir, "jobs.txt")
    with open(jobs, "w") as f:
        f.write("%d\n" % vs.n)
        for i in range(vs.n):
            data = vs.dk + vs.ct[i].tobytes()
            f.write("2 768 %d\n%s\n" % (len(data), " ".join("%02x" % b for b in data)))
    trace, bmul = os.path.join(workdir, "trace.txt"), os.path.join(workdir, "bmul.txt")
    r = subprocess.run([SIM_BINARY, "+jobs=" + jobs, "+out=" + os.path.join(workdir, "res.txt"),
                        "+trace=" + trace, "+bmul=" + bmul], capture_output=True, text=True)
    if r.returncode != 0 or "TB_CORE DONE" not in r.stdout:
        raise SystemExit("simulation failed:\n%s%s" % (r.stdout, r.stderr))
    return trace, bmul


def parse_trace(path: str) -> dict[int, list]:
    """``job -> [(cycle, kind, fields...)]``: ``I`` pc/op, ``A`` ALU start."""
    jobs: dict[int, list] = defaultdict(list)
    with open(path) as f:
        for line in f:
            p = line.split()
            if not p:
                continue
            if p[0] == "I":
                jobs[int(p[1])].append((int(p[2]), "I", int(p[3]), int(p[4])))
            elif p[0] == "A":
                jobs[int(p[1])].append((int(p[2]), "A", int(p[3]), int(p[4]),
                                        int(p[5]), int(p[6]), int(p[7])))
    return jobs


def alu_windows(events: list) -> list[dict]:
    """One row per ALU op: its start cycle, op name, slots, and the cycle the
    next instruction's read begins (= a tight upper bound on when it ends)."""
    starts = [e for e in events if e[1] == "A"]
    instrs = sorted(c for c, k, *_ in events if k == "I")
    out = []
    for cyc, _, op, sa, sb, sc, acc in starts:
        nxt = next((c for c in instrs if c > cyc), None)
        out.append({"start": cyc, "op": ALU_OP.get(op, str(op)), "sa": sa, "sb": sb,
                    "sc": sc, "acc": acc, "next_instr": nxt})
    return out


def bmul_spans(path: str) -> dict[int, list[tuple[int, int]]]:
    """``job -> [(first_cycle, last_cycle)]`` for each contiguous BMUL run in
    the datapath dump (the pipeline is active, one run per BMUL instruction)."""
    rows: dict[int, list[int]] = defaultdict(list)
    with open(path) as f:
        for line in f:
            p = line.split()
            if p and p[0] == "B":
                rows[int(p[1])].append(int(p[2]))
    spans: dict[int, list[tuple[int, int]]] = {}
    for job, cycles in rows.items():
        cycles.sort()
        runs, s, prev = [], cycles[0], cycles[0]
        for c in cycles[1:]:
            if c != prev + 1:
                runs.append((s, prev))
                s = c
            prev = c
        runs.append((s, prev))
        spans[job] = runs
    return spans


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("trace", nargs="?", help="a +trace= file (instruction timeline); omit to run the model")
    ap.add_argument("bmul", nargs="?", help="the +bmul= file (BMUL datapath), optional")
    ap.add_argument("--slack", type=int, default=40,
                    help="cycles added each side when printing config windows (TRIGGER_SLACK_CYCLES)")
    args = ap.parse_args()

    tmp = None
    if not args.trace:
        tmp = tempfile.TemporaryDirectory(prefix="bmul_timing_")
        print("building and running the Verilator model (2 Decaps) ...")
        args.trace, args.bmul = simulate(tmp.name)

    jobs = parse_trace(args.trace)
    if not jobs:
        sys.exit("no instruction timeline in %s (run the model with +trace=)" % args.trace)
    job = sorted(jobs)[0]
    wins = alu_windows(jobs[job])

    ntts = [w for w in wins if w["op"] == "NTT"]
    bmuls = [w for w in wins if w["op"] == "BMUL"]
    intts = [w for w in wins if w["op"] == "INTT"]

    # Decryption = everything up to and including the first INTT (ŝᵀ∘û then
    # INTT). The three decryption BMULs are the three before that INTT.
    first_intt = intts[0]["start"] if intts else 10 ** 9
    dec_bmul = [w for w in bmuls if w["start"] < first_intt]
    dec_ntt = [w for w in ntts if w["start"] < first_intt]

    spans = bmul_spans(args.bmul)[job] if args.bmul else None

    def win_of(w: dict) -> tuple[int, int]:
        """A measured (start, end) for an ALU op: the datapath span when we have
        it (BMUL), else start..next-instruction."""
        if spans is not None and w["op"] == "BMUL":
            for a, b in spans:
                if abs(a - w["start"]) <= 3:
                    return a, b
        return w["start"], (w["next_instr"] or w["start"])

    print("# Decaps pipeline windows, core cycles after start (job %d of %s)" % (job, args.trace))
    print("# core clock 206.25 MHz; trig_out high / CYCLE_COUNT count these cycles.\n")

    print("Decryption ŝᵀ∘û (the CPA target):")
    bmul_ranges = []
    for i, w in enumerate(dec_ntt):
        a, b = win_of(w)
        print("  NTT  û%d       cycles %5d - %5d  (len %4d)" % (i, a, b, b - a))
    for i, w in enumerate(dec_bmul):
        a, b = win_of(w)
        bmul_ranges.append((a, b))
        print("  BMUL ŝ%d∘û%d%s cycles %5d - %5d  (len %4d)"
              % (i, i, " acc" if w["acc"] else "    ", a, b, b - a))
    if intts:
        a, b = win_of(intts[0])
        print("  INTT Σŝᵢ∘ûᵢ   cycles %5d - %5d  (len %4d)" % (a, b, b - a))

    print("\nconfig.py (copy in after an RTL/microcode change, re-measured here):")
    print("  BMUL_WINDOWS = %s" % bmul_ranges)
    print("  BMUL_READ_CYCLES = %s" % [w["start"] + 2 for w in dec_bmul])
    if dec_ntt:
        ntt_ranges = [win_of(w) for w in dec_ntt]
        print("  NTT_WINDOWS_CYCLES = %s" % [(a, b) for a, b in ntt_ranges])
    if intts:
        a, b = win_of(intts[0])
        print("  INTT_WINDOW_CYCLES = %s" % ((a, b),))

    other_bmul = [w for w in bmuls if w["start"] >= first_intt]
    if other_bmul:
        print("\nRe-encryption BMULs (NOT CPA targets -- ŝ is not an operand here; the")
        print("FO re-encrypt recomputes A∘r̂ and t̂∘r̂, so a trace feature here is the")
        print("re-encryption / message leakage, not the decryption of ŝ):")
        for w in other_bmul:
            a, b = win_of(w)
            print("  BMUL slot %d∘%d -> %d%s cycles %5d - %5d" % (w["sa"], w["sb"], w["sc"],
                  " acc" if w["acc"] else "    ", a, b))

    if len(jobs) > 1:
        other = sorted(jobs)[1]
        w2 = [win_of(w) for w in alu_windows(jobs[other]) if w["op"] == "BMUL"][:3]
        same = w2 == bmul_ranges
        print("\ncycle-exact across jobs: %s (job %d BMUL windows %s)"
              % ("yes" if same else "NO -- core is not constant-time!", other,
                 "match" if same else w2))


if __name__ == "__main__":
    main()
