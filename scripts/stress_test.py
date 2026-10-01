#!/usr/bin/env python3
"""Repeated FPGA validation (stability / stress test) over UART.

Runs `pqc-testkit fpga` N times. Every run uses fresh random seeds and
compares KeyGen / Encaps / Decaps / implicit-reject outputs of each --levels
parameter set (default ML-KEM-768, the only level the core implements)
byte-for-byte against the software reference, so N runs are 4*N*len(levels)
independent known-answer tests on the hardware.

Each run's full output is saved to <log-dir>/run_XXXX.log. The script stops
at the first failure unless --keep-going is given, and prints a summary
with pass counts and per-test cycle statistics at the end (also on Ctrl-C).

Examples:
  python3 scripts/stress_test.py -p /dev/cu.usbserial-XXXX1
  python3 scripts/stress_test.py -p /dev/cu.usbserial-XXXX1 -n 1000 --keep-going
"""

import argparse
import os
import re
import subprocess
import sys
import time

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

# "       ML-KEM-768  Decaps (implicit reject) PASS (32 bytes, 110633 cycles)"
KAT_LINE = re.compile(r"(ML-KEM-\d+)\s+(.+?)\s+(PASS|MISMATCH|FAILED)(?:\s+\((\d+) bytes, (\d+) cycles\))?")


def build_binary(path):
    print("building %s ..." % os.path.relpath(path, ROOT))
    subprocess.run(["go", "build", "-o", path, "./cmd/pqc-testkit"], cwd=ROOT, check=True)


def run_once(binary, port, baud, levels, timeout):
    """Run one validation; returns (ok, output, {test: cycles}, reason)."""
    expected = 4 * len(levels)
    try:
        proc = subprocess.run([binary, "fpga", "-T", "uart", "-d", port, "-b", str(baud),
                               "--levels", ",".join(map(str, levels))],
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        return False, out, {}, "run timed out after %ds" % timeout
    out = proc.stdout + proc.stderr
    cycles = {}
    failed = []
    for m in KAT_LINE.finditer(out):
        name = "%s %s" % (m.group(1), m.group(2))
        if m.group(3) == "PASS":
            cycles[name] = int(m.group(5))
        else:
            failed.append(name)
    ok = proc.returncode == 0 and "ML-KEM KAT: PASS" in out and len(cycles) == expected
    if ok:
        reason = ""
    elif failed:
        reason = "failed: " + ", ".join(failed)
    else:
        err = [l for l in out.splitlines() if l.startswith("error:")]
        reason = err[-1] if err else "exit code %d, %d/%d KATs passed" % (proc.returncode, len(cycles), expected)
    return ok, out, cycles, reason


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-p", "--port", required=True, help="UART port, e.g. /dev/cu.usbserial-XXXX1")
    ap.add_argument("-b", "--baud", type=int, default=115200)
    ap.add_argument("-n", "--runs", type=int, default=50, help="number of runs (default 50)")
    ap.add_argument("--levels", type=int, nargs="+", choices=[768, 1024], default=[768],
                    help="ML-KEM levels built into the bitstream (default 768)")
    ap.add_argument("--binary", default=os.path.join(ROOT, "pqc-testkit"),
                    help="pqc-testkit binary (built automatically if missing)")
    ap.add_argument("--rebuild", action="store_true", help="rebuild the binary first")
    ap.add_argument("--log-dir", default=os.path.join(ROOT, "stress_logs"))
    ap.add_argument("--keep-going", action="store_true", help="continue after a failed run")
    ap.add_argument("--run-timeout", type=int, default=120, help="seconds allowed per run")
    args = ap.parse_args()

    if args.rebuild or not os.path.exists(args.binary):
        build_binary(args.binary)
    os.makedirs(args.log_dir, exist_ok=True)

    print("Stress test: %d runs of ML-KEM-%s on %s @ %d baud, logs in %s\n"
          % (args.runs, "/".join(map(str, args.levels)), args.port, args.baud,
             os.path.relpath(args.log_dir)))

    passed, failed = 0, []
    stats = {}                       # test -> [min, max, sum, count]
    t_start = time.time()
    try:
        for i in range(1, args.runs + 1):
            t0 = time.time()
            ok, out, cycles, reason = run_once(args.binary, args.port, args.baud, args.levels,
                                               args.run_timeout)
            log = os.path.join(args.log_dir, "run_%04d.log" % i)
            with open(log, "w") as f:
                f.write(out)
            for name, c in cycles.items():
                s = stats.setdefault(name, [c, c, 0, 0])
                s[0], s[1], s[2], s[3] = min(s[0], c), max(s[1], c), s[2] + c, s[3] + 1
            if ok:
                passed += 1
                print("run %4d/%d: PASS  (%.1f s)" % (i, args.runs, time.time() - t0))
            else:
                failed.append(i)
                print("run %4d/%d: FAIL  %s  -> %s" % (i, args.runs, reason, os.path.relpath(log)))
                if not args.keep_going:
                    break
    except KeyboardInterrupt:
        print("\ninterrupted")

    total = passed + len(failed)
    print("\n=== Summary ===")
    print("runs:     %d passed / %d executed (%d KATs passed)" % (passed, total, sum(s[3] for s in stats.values())))
    print("failed:   %s" % (", ".join(map(str, failed)) if failed else "none"))
    print("duration: %.1f min" % ((time.time() - t_start) / 60))
    if stats:
        print("\n%-34s %9s %9s %9s" % ("test", "min cyc", "max cyc", "avg cyc"))
        for name in sorted(stats, key=lambda n: (int(n.split()[0][7:]), n)):
            mn, mx, sm, cnt = stats[name]
            print("%-34s %9d %9d %9d" % (name, mn, mx, sm // cnt))
    print("\nRESULT: %s" % ("PASS" if total and not failed else "FAIL"))
    sys.exit(0 if total and not failed else 1)


if __name__ == "__main__":
    main()
