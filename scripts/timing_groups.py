#!/usr/bin/env python3
"""Group failing timing paths by source / destination register.

Reads timing_failing.rpt from scripts/vivado_reports.tcl (report_timing
-unique_pins -slack_lesser_than 0) and prints, per (source, destination)
register group with bit indices removed: endpoint count, worst slack,
data-path delay and logic levels of the worst path.

Usage: python3 scripts/timing_groups.py reports/<dir>/timing_failing.rpt [-n 40]
"""

import argparse
import collections
import re


def norm(cell):
    cell = re.sub(r"/(C|D|CE|R|S|CLR|PRE|[A-Z]+\[\d+\]|[A-Z]+)$", "", cell)
    cell = re.sub(r"\[\d+\]", "[*]", cell)
    cell = re.sub(r"_reg(_\d+)?(_replica(_\d+)?)?", "", cell)
    cell = re.sub(r"__\d+", "", cell)
    return cell


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("rpt")
    ap.add_argument("-n", type=int, default=40)
    args = ap.parse_args()
    groups = collections.OrderedDict()
    cur = {}
    with open(args.rpt) as f:
        for line in f:
            m = re.match(r"Slack \((VIOLATED|MET)\)\s*:\s*(-?[\d.]+)ns", line)
            if m:
                cur = {"slack": float(m.group(2))}
                continue
            m = re.match(r"\s+Source:\s+(\S+)", line)
            if m:
                cur["src"] = norm(m.group(1))
                continue
            m = re.match(r"\s+Destination:\s+(\S+)", line)
            if m:
                cur["dst"] = norm(m.group(1))
                continue
            m = re.match(r"\s+Data Path Delay:\s+([\d.]+)ns", line)
            if m:
                cur["delay"] = float(m.group(1))
                continue
            m = re.match(r"\s+Logic Levels:\s+(\d+)\s*(\(.*\))?", line)
            if m and "src" in cur:
                cur["levels"] = m.group(1) + " " + (m.group(2) or "")
                key = (cur["src"], cur["dst"])
                g = groups.setdefault(key, {"n": 0, "worst": cur})
                g["n"] += 1
                if cur["slack"] < g["worst"]["slack"]:
                    g["worst"] = cur
                cur = {}
    rows = sorted(groups.items(), key=lambda kv: kv[1]["worst"]["slack"])
    total = sum(g["n"] for _, g in rows)
    print("%d failing endpoints in %d groups" % (total, len(rows)))
    print("%8s %5s %7s  %-24s %s -> %s" % ("slack", "n", "delay", "levels", "source", "destination"))
    for (src, dst), g in rows[:args.n]:
        w = g["worst"]
        print("%8.3f %5d %7.3f  %-24s %s -> %s" % (w["slack"], g["n"], w["delay"], w["levels"][:24], src, dst))


if __name__ == "__main__":
    main()
