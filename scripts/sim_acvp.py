#!/usr/bin/env python3
"""NIST ACVP ML-KEM-768 vectors on the RTL core, in Verilator.

Simulation counterpart of `uart_test.py acvp`: the same internalProjection
vector sets run through tb_core.sv (AXI-Lite host flow on pqc_mlkem_top).

Required set (must be 60/60):
  ML-KEM-768 keyGen AFT          25   d||z  -> ek||dk
  ML-KEM-768 encapsulation AFT   25   ek||m -> c||K
  ML-KEM-768 decapsulation VAL   10   dk||c -> K   (incl. implicit rejection)

Extra checks (reported separately, also must pass):
  ML-KEM-768 encapsulationKeyCheck  10   error 2 iff testPassed is false
  SEC_LEVEL 512 / 1024 / 999            error 1 for every OP_MODE
  OP_MODE 3                             error 1

Usage:
  python3 scripts/sim_acvp.py [-v] [--no-extra]
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import coresim  # noqa: E402

ACVP = os.path.join(coresim.ROOT, "acvp")
KEYGEN = os.path.join(ACVP, "ML-KEM-keyGen-FIPS203", "internalProjection.json")
ENCDEC = os.path.join(ACVP, "ML-KEM-encapDecap-FIPS203", "internalProjection.json")
LEVEL = 768
STATUS_DONE, STATUS_ERROR = 2, 4


def load_cases():
    """Returns [(kind, tcId, Job, check(result) -> (ok, detail), required)]."""
    cases = []
    for path in (KEYGEN, ENCDEC):
        with open(path) as f:
            vs = json.load(f)
        for g in vs["testGroups"]:
            if g["parameterSet"] != "ML-KEM-%d" % LEVEL:
                continue
            func = g.get("function", "keyGen")
            for t in g["tests"]:
                h = {k: bytes.fromhex(v) for k, v in t.items()
                     if k in ("d", "z", "ek", "dk", "c", "k", "m")}
                kind = "ML-KEM-%d %s" % (LEVEL, func)
                if func == "keyGen":
                    job = coresim.Job(0, LEVEL, h["d"] + h["z"])
                    exp = h["ek"] + h["dk"]
                elif func == "encapsulation":
                    job = coresim.Job(1, LEVEL, h["ek"] + h["m"])
                    exp = h["c"] + h["k"]
                elif func == "decapsulation":
                    job = coresim.Job(2, LEVEL, h["dk"] + h["c"])
                    exp = h["k"]
                elif func == "encapsulationKeyCheck":
                    job = coresim.Job(1, LEVEL, h["ek"] + bytes(32))
                    want_err = 0 if t["testPassed"] else 2

                    def chk(r, want_err=want_err):
                        got = 0 if r.status == STATUS_DONE else r.err
                        return got == want_err, "err=%d want %d" % (got, want_err)
                    cases.append((kind, t["tcId"], job, chk, False))
                    continue
                else:
                    continue    # decapsulationKeyCheck: not implemented by the core.

                def chk(r, exp=exp):
                    if r.status != STATUS_DONE:
                        return False, "status=%d err=%d" % (r.status, r.err)
                    return r.out == exp, "" if r.out == exp else "output mismatch"
                cases.append((kind, t["tcId"], job, chk, True))
    return cases


def extra_param_cases():
    cases = []
    for level in (512, 1024, 999):
        for op in (0, 1, 2):
            def chk(r):
                return (r.status & STATUS_ERROR) != 0 and r.err == 1, "status=%d err=%d" % (r.status, r.err)
            cases.append(("SEC_LEVEL %d -> error 1" % level, op, coresim.Job(op, level, bytes(64)), chk, False))
    def chk3(r):
        return (r.status & STATUS_ERROR) != 0 and r.err == 1, "status=%d err=%d" % (r.status, r.err)
    cases.append(("OP_MODE 3 -> error 1", 3, coresim.Job(3, LEVEL, bytes(64)), chk3, False))
    return cases


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--no-extra", action="store_true", help="only the 60 required vectors")
    args = ap.parse_args()

    cases = load_cases()
    if args.no_extra:
        cases = [c for c in cases if c[4]]
    else:
        cases += extra_param_cases()
    results = coresim.run([c[2] for c in cases])

    counts = {}
    req_pass = req_total = 0
    all_ok = True
    for (kind, tc, job, chk, required), r in zip(cases, results):
        ok, detail = chk(r)
        c = counts.setdefault(kind, [0, 0, []])
        c[0 if ok else 1] += 1
        if job.op == 2 and ok and kind.endswith("decapsulation"):
            c[2].append(r.cycles)
        all_ok &= ok
        if required:
            req_total += 1
            req_pass += ok
        if not ok or args.verbose:
            print("  %-34s tc %-4s %s cycles=%d %s" % (kind, tc, "PASS" if ok else "FAIL", r.cycles, detail))

    for kind in sorted(counts):
        p, fl, cyc = counts[kind]
        extra = ""
        if cyc:
            extra = " (cycles min %d max %d)" % (min(cyc), max(cyc))
        print("  %-34s %3d/%-3d %s%s" % (kind, p, p + fl, "PASS" if fl == 0 else "FAIL", extra))
    print("\nACVP ML-KEM-%d %d/%d %s" % (LEVEL, req_pass, req_total,
                                       "PASS" if req_pass == req_total == 60 else "FAIL"))
    print("SIM_ACVP %s" % ("PASS" if all_ok and req_total == 60 else "FAIL"))
    sys.exit(0 if all_ok and req_total == 60 else 1)


if __name__ == "__main__":
    main()
