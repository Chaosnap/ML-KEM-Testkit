#!/usr/bin/env python3
"""Command line for ML-KEM-768 trace acquisition on the Arty A7 board.

    uv run python hostlab/capture.py ports              # find the board
    uv run python hostlab/capture.py scopes             # find the instrument
    uv run python hostlab/capture.py check -p PORT      # bring-up checks
    uv run python hostlab/capture.py dry-run -p PORT    # full flow, no scope
    uv run python hostlab/capture.py run -p PORT -r VISA -n 5000
    uv run python hostlab/capture.py analyse DIR
    uv run python hostlab/capture.py info DIR

See hostlab/README.md for the bench setup and the order of experiments.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tracelab import analysis, protocol, scope as scope_mod, vectors as vec_mod  # noqa: E402
from tracelab.campaign import Campaign  # noqa: E402
from tracelab.storage import TraceSet  # noqa: E402
from tracelab.target import Target  # noqa: E402

ROOT = vec_mod.ROOT
DEFAULT_OUT = os.path.join(ROOT, "sim_result", "traces")


def log(msg: str = "") -> None:
    print(msg, flush=True)


def rel(path: str) -> str:
    r = os.path.relpath(path, ROOT)
    return path if r.startswith("..") else r


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def cmd_ports(args) -> int:
    ports = protocol.list_ports()
    if not ports:
        log("no serial ports found; is the board plugged in and powered?")
        return 1
    log("%-32s %s" % ("port", "description"))
    for dev, desc in ports:
        log("%-32s %s" % (dev, desc))
    log("")
    log("The Arty A7 enumerates twice on one FTDI chip: the *second* port is")
    log("the UART (the first is JTAG). On macOS prefer the /dev/cu.* name.")
    return 0


def cmd_scopes(args) -> int:
    res = scope_mod.list_resources()
    if not res:
        log("no VISA resources found.")
        log("USB scopes need a backend: `python3 -m pip install pyvisa-py pyusb`,")
        log("or the vendor's VISA runtime. LAN scopes also work as")
        log("TCPIP0::<ip>::INSTR without any backend beyond pyvisa-py.")
        return 1
    for r, idn in res:
        log("%-44s %s" % (r, idn))
    return 0


# ---------------------------------------------------------------------------
# Bring-up
# ---------------------------------------------------------------------------

def cmd_check(args) -> int:
    with Target.open(args.port, args.baud, args.timeout, args.op_timeout) as t:
        log("link checks on %s @ %d baud" % (args.port, args.baud))
        checks = t.preflight()
        for c in checks:
            log("  [%s] %-28s %s" % ("ok" if c.ok else "FAIL", c.name, c.detail))
        if not all(c.ok for c in checks):
            log("")
            log("link not healthy; do not start a campaign. Check the port, the")
            log("baud rate (115200), and that the bitstream is loaded (LD7 blinks).")
            return 1

        info = t.info()
        log("")
        log("core: ML-KEM v%s, SEC_LEVEL %s, %.0f MHz"
            % (info["version"], info["sec_level"], info["core_mhz"]))

        log("")
        log("running one Decaps to measure the operation time ...")
        vs = vec_mod.generate(4, op="decaps", quiet=True)
        out = t.run(vs.op, vs[0].data)
        if not out.ok:
            log("  FAILED: %s" % out.describe())
            return 1
        log("  %s" % out.describe())
        log("  host round trip %.0f ms (UART transfer dominates)" % (out.wall_time * 1000))
        log("")
        log("Set the scope window to cover %.1f us and trigger on LD4 (pin H5),"
            % (out.cycles / protocol.CORE_MHZ))
        log("which carries the core's busy signal. See hostlab/README.md.")
    return 0


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------

def _build_vectors(args):
    if args.vectors:
        vs = vec_mod.load(args.vectors, op=args.op)
        log("loaded %d vectors from %s" % (len(vs), rel(args.vectors)))
    else:
        vs = vec_mod.generate(
            args.traces, op=args.op, seed=args.seed,
            invalid_random=args.invalid_random,
        )
    if args.fixed_vs_fixed:
        # Control experiment: both classes get the fixed input, so no real
        # leakage can exist and whatever the t-test reports is the false
        # positive level of the setup.
        fixed = next((v.data for v in vs if v.cls == 0), None)
        if fixed is None:
            raise SystemExit("the vector set has no fixed class")
        for v in vs:
            v.data = fixed
        vs.source += " (fixed-vs-fixed control)"
    return vs


def _make_scope(args):
    if args.dry_run or not args.resource:
        return scope_mod.NullScope(samples=args.samples, noise=8.0, leak=args.synthetic_leak)
    s = scope_mod.VisaScope(
        args.resource, channel=args.channel, points=args.samples,
        dialect=args.dialect, timeout_ms=int(args.scope_timeout * 2000),
    )
    s.configure()
    return s


def cmd_run(args) -> int:
    vs = _build_vectors(args)
    name = args.name or "%s_%s%s%s" % (
        datetime.now().strftime("%Y%m%d_%H%M%S"),
        args.op,
        "_invalid" if args.invalid_random else "",
        "_control" if args.fixed_vs_fixed else "",
    )
    out_dir = os.path.join(args.out, name)

    with Target.open(args.port, args.baud, args.timeout, args.op_timeout) as t:
        if not args.no_preflight:
            checks = t.preflight()
            bad = [c for c in checks if not c.ok]
            if bad:
                for c in bad:
                    log("  [FAIL] %-28s %s" % (c.name, c.detail))
                log("link not healthy; aborting. Run `check` for the full list.")
                return 1
            log("link ok (%d checks)" % len(checks))

        sc = _make_scope(args)
        try:
            c = Campaign(
                t, sc, vs, out_dir,
                scope_timeout=args.scope_timeout,
                max_errors=args.max_errors,
            )
            ts = c.run(log)
        finally:
            sc.close()

    log("")
    log("trace set: %s" % rel(out_dir))
    if ts.completed >= 4 and not args.no_analysis:
        log("")
        _analyse(out_dir, args.detection_curve)
    return 0


def cmd_dry_run(args) -> int:
    args.dry_run = True
    args.resource = ""
    log("dry run: synthetic scope, the board still runs every operation")
    return cmd_run(args)


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def _analyse(path: str, detection_curve: bool = False) -> dict:
    ts = TraceSet.load(path)
    res = analysis.assess(ts, detection_curve=detection_curve)
    log(analysis.summary(res))
    fig = os.path.join(path, "tvla.png")
    try:
        analysis.plot(res, fig, title="%s - %s" % (ts.meta.get("op", "?"), os.path.basename(path)))
        log("figure        %s" % rel(fig))
    except ImportError:
        log("(matplotlib not available: no figure written)")
    return res


def cmd_analyse(args) -> int:
    _analyse(args.dir, args.detection_curve)
    return 0


def cmd_info(args) -> int:
    log(TraceSet.load(args.dir).summary())
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    def board_args(p):
        p.add_argument("-p", "--port", required=True, help="serial port of the board")
        p.add_argument("-b", "--baud", type=int, default=115200, help="baud rate (default 115200)")
        p.add_argument("--timeout", type=float, default=2.0, help="serial read timeout (s)")
        p.add_argument("--op-timeout", type=float, default=5.0, help="core operation timeout (s)")

    def campaign_args(p):
        board_args(p)
        p.add_argument("-n", "--traces", type=int, default=2000,
                       help="traces, split equally between fixed and random (default 2000)")
        p.add_argument("--op", choices=["decaps", "keygen"], default="decaps")
        p.add_argument("--seed", default="",
                       help="64-byte hex seed fixing the key pair and the fixed input")
        p.add_argument("--invalid-random", action="store_true",
                       help="decaps: random class uses random ciphertexts (implicit rejection)")
        p.add_argument("--vectors", default="", help="use an existing `sca gen` CSV instead")
        p.add_argument("--fixed-vs-fixed", action="store_true",
                       help="control run: both classes get the fixed input, so the result "
                            "is the false-positive level of the setup")
        p.add_argument("-r", "--resource", default="", help="VISA resource of the scope")
        p.add_argument("--channel", default="CHANnel1", help="scope waveform source")
        p.add_argument("--dialect", choices=sorted(scope_mod.DIALECTS), default="default")
        p.add_argument("-s", "--samples", type=int, default=5000, help="samples per trace")
        p.add_argument("--scope-timeout", type=float, default=10.0,
                       help="seconds to wait for one capture (default 10)")
        p.add_argument("--synthetic-leak", type=float, default=0.0,
                       help="dry run only: amplitude of a fake input-dependent signal, to "
                            "check that the analysis can detect something. Try 5. How "
                            "visible it is depends on the fixed input that happened to be "
                            "drawn, so it varies between runs; 0 (the default) is the "
                            "negative control and must report nothing")
        p.add_argument("--max-errors", type=int, default=5,
                       help="abort after this many consecutive failures")
        p.add_argument("-o", "--out", default=DEFAULT_OUT, help="results directory")
        p.add_argument("--name", default="", help="campaign directory name")
        p.add_argument("--no-preflight", action="store_true")
        p.add_argument("--no-analysis", action="store_true", help="do not analyse afterwards")
        p.add_argument("--detection-curve", action="store_true",
                       help="also compute max|t| against trace count")
        p.set_defaults(dry_run=False)

    p = sub.add_parser("ports", help="list serial ports")
    p.set_defaults(fn=cmd_ports)

    p = sub.add_parser("scopes", help="list VISA instruments")
    p.set_defaults(fn=cmd_scopes)

    p = sub.add_parser("check", help="bring-up checks and one timed operation")
    board_args(p)
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("dry-run", help="full flow with a synthetic scope (no instrument)")
    campaign_args(p)
    p.set_defaults(fn=cmd_dry_run)

    p = sub.add_parser("run", help="capture a campaign")
    campaign_args(p)
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("analyse", help="Welch t-test over a captured set")
    p.add_argument("dir")
    p.add_argument("--detection-curve", action="store_true")
    p.set_defaults(fn=cmd_analyse)

    p = sub.add_parser("info", help="summary of a captured set")
    p.add_argument("dir")
    p.set_defaults(fn=cmd_info)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
