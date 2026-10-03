#!/usr/bin/env python3
"""One docs/REFINE_LOG.md row from a reports/stepN directory.

Reads (all written by scripts/vivado_reports.tcl / profile_ucode.py):
  utilization_u_mlkem.rpt   report_utilization -cells [get_cells u_mlkem]
  summary.txt               WNS at the 10 ns constraint
  profile_ucode.json        Decaps-768 mean CYCLE_COUNT
  fmax_5ns/summary.txt      optional: Fmax from the 5 ns run

ENS = Slice + 100 * DSP + 200 * BRAM  (BRAM in RAMB36 equivalents)
ATP = ENS * cycles / f   (f = core clock, default 206.0 MHz; ATP in ENS * ms)

Usage:
  python3 scripts/refine_row.py reports/step0 --step 0 --acvp 60/60
"""

import argparse
import json
import os
import re


def util(path):
    """{name: used} for the rows of the summary tables."""
    vals = {}
    with open(path) as f:
        for line in f:
            m = re.match(r"\|\s*([A-Za-z0-9 _/\-\*]+?)\s*\|\s*([0-9.]+)\s*\|", line)
            if m and m.group(1) not in vals:
                vals[m.group(1).rstrip("* ")] = float(m.group(2))
    return vals


def kv(path):
    with open(path) as f:
        return {k: v for k, v in (line.split(None, 1) for line in f if line.strip())}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dir")
    ap.add_argument("--step", required=True)
    ap.add_argument("--acvp", default="60/60")
    ap.add_argument("--mhz", type=float, default=206.0, help="core clock (MHz)")
    args = ap.parse_args()

    u = util(os.path.join(args.dir, "utilization_u_mlkem.rpt"))
    lut, ff = int(u["Slice LUTs"]), int(u["Slice Registers"])
    slc, dsp = int(u["Slice"]), int(u["DSPs"])
    bram = u["Block RAM Tile"]
    s = kv(os.path.join(args.dir, "summary.txt"))
    wns = float(s.get("core_wns_ns", s["wns_ns"]))
    with open(os.path.join(args.dir, "profile_ucode.json")) as f:
        prof = {p["op"]: p for p in json.load(f)}
    cyc = prof["decaps"]["cycles_mean"]
    ens = slc + 100 * dsp + 200 * bram
    atp = ens * cyc / (args.mhz * 1e3)
    fmax = ""
    fp = os.path.join(args.dir, "fmax_5ns", "summary.txt")
    if "core_fmax_mhz" in s:
        fmax = s["core_fmax_mhz"].strip()
    elif os.path.exists(fp):
        fmax = kv(fp)["fmax_mhz"].strip()

    bram_s = ("%g" % bram)
    print("| Step | LUT | FF | Slice | DSP | BRAM | ENS | Decaps cycles | WNS (core clk) | ACVP | ATP (ENS*ms) | Fmax (MHz) |")
    print("| ---- | --: | -: | ----: | --: | ---: | --: | ------------: | -----------: | ---- | -----------: | ---------: |")
    print("| %s | %d | %d | %d | %d | %s | %.0f | %.0f | %+.3f ns | %s | %.0f | %s |"
          % (args.step, lut, ff, slc, dsp, bram_s, ens, cyc, wns, args.acvp, atp, fmax))


if __name__ == "__main__":
    main()
