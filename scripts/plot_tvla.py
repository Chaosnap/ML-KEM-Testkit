#!/usr/bin/env python3
"""Plot the t-curves written by `pqc-testkit sca sim` (tvla_t.csv).

One panel per selected instance: t per cycle with the +/-4.5 TVLA bounds.
Needs matplotlib:  python3 -m pip install matplotlib
                   (or: uv run --with matplotlib scripts/plot_tvla.py ...)

Examples:
  python3 scripts/plot_tvla.py build/tvla_decaps
  python3 scripts/plot_tvla.py build/tvla_decaps --groups total u_alu u_sponge
  python3 scripts/plot_tvla.py build/tvla_decaps --from 20000 --to 30000 -o zoom.png
"""

import argparse
import csv
import os
import sys

THRESHOLD = 4.5


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dir", help="output directory of pqc-testkit sca sim")
    ap.add_argument("--groups", nargs="+", help="instances to plot (default: all)")
    ap.add_argument("--from", dest="lo", type=int, default=0, help="first cycle")
    ap.add_argument("--to", dest="hi", type=int, help="last cycle (exclusive)")
    ap.add_argument("-o", "--output", help="image file (default: <dir>/tvla_t.png)")
    args = ap.parse_args()

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        sys.exit("matplotlib is required: python3 -m pip install matplotlib")

    with open(os.path.join(args.dir, "tvla_t.csv"), newline="") as f:
        rows = list(csv.reader(f))
    header, rows = rows[0], rows[1:]
    hi = args.hi if args.hi is not None else len(rows)
    rows = rows[args.lo:hi]
    groups = [h[2:] for h in header if h.startswith("t_")]
    if args.groups:
        missing = set(args.groups) - set(groups)
        if missing:
            sys.exit("unknown groups %s; available: %s" % (sorted(missing), " ".join(groups)))
        groups = args.groups

    cycles = [int(r[0]) for r in rows]
    fig, axes = plt.subplots(len(groups), 1, sharex=True, squeeze=False,
                             figsize=(12, 1.8 * len(groups) + 0.6))
    for ax, g in zip(axes[:, 0], groups):
        col = header.index("t_" + g)
        t = [float(r[col]) for r in rows]
        ax.plot(cycles, t, linewidth=0.4)
        for y in (THRESHOLD, -THRESHOLD):
            ax.axhline(y, color="red", linewidth=0.6, linestyle="--")
        ax.set_ylabel("t (%s)" % g, fontsize=8)
        ax.tick_params(labelsize=7)
    axes[-1, 0].set_xlabel("clock cycle")
    fig.tight_layout()
    out = args.output or os.path.join(args.dir, "tvla_t.png")
    fig.savefig(out, dpi=150)
    print("wrote %s" % out)


if __name__ == "__main__":
    main()
