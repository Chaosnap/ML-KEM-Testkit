#!/usr/bin/env python3
"""Per-instruction cycle profile of the ML-KEM microcode, from RTL simulation.

Runs the ML-KEM-768 ACVP vectors of the chosen operation on the core in
Verilator (testbench/core_sim) while sampling mlkem_ctrl's state and pc on
every busy cycle, and prints how CYCLE_COUNT splits over the instruction
classes. Every busy cycle is counted exactly once:

  exec      cycles in the instruction's own states (C_HSQZ, C_RD, C_UNIT, ...)
  overhead  sequencer C_NEXT / C_FETCH / C_DECODE (3 cycles per instruction)

Numbers are averaged over the vectors (SAMPLE / rejection sampling makes
them slightly data dependent). Bytes per instruction come from the len field
of the microcode word, so byte-stream instructions also get cycles/byte.

Usage:
  python3 scripts/profile_ucode.py                      # Decaps-768
  python3 scripts/profile_ucode.py --op keygen
  python3 scripts/profile_ucode.py --op all --md reports/step0/profile.md
  python3 scripts/profile_ucode.py --per-pc             # also list every pc
"""

import argparse
import collections
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import coresim  # noqa: E402

ROM = os.path.join(coresim.ROOT, "hdl", "core", "mlkem_ucode_rom.sv")
KEYGEN = os.path.join(coresim.ROOT, "acvp", "ML-KEM-keyGen-FIPS203", "internalProjection.json")
ENCDEC = os.path.join(coresim.ROOT, "acvp", "ML-KEM-encapDecap-FIPS203", "internalProjection.json")

OPNAMES = {0x00: "END", 0x01: "HINIT", 0x02: "HABS", 0x03: "HABI", 0x04: "HFIN",
           0x05: "HSQZ", 0x06: "COPY", 0x07: "CMP", 0x08: "CSEL", 0x10: "SAMPLE",
           0x11: "CBD", 0x12: "DECODE", 0x13: "ENCODE", 0x20: "NTT", 0x21: "INTT",
           0x22: "BMUL", 0x23: "ADD", 0x24: "SUB"}
ORDER = ["HINIT", "HABS", "HABI", "HFIN", "HSQZ", "COPY", "CMP", "CSEL", "SAMPLE",
         "CBD", "DECODE", "ENCODE", "NTT", "INTT", "BMUL", "ADD", "SUB", "END"]
BYTE_OPS = {"HABS", "HSQZ", "COPY", "CMP", "CSEL"}
STARTUP_PC = 2047

_ROM_RE = re.compile(r"ADDR_W'\((\d+)\): data <= 96'h([0-9a-f]+);\s*//\s*(.*)")


def read_rom():
    """pc -> (opname, len_field, comment), and the program entry points."""
    rom, entries = {}, {}
    with open(ROM) as f:
        for line in f:
            m = _ROM_RE.search(line)
            if m:
                word = int(m.group(2), 16)
                rom[int(m.group(1))] = (OPNAMES.get(word >> 88, "0x%02x" % (word >> 88)),
                                        (word >> 16) & 0xFFFF, m.group(3).strip())
            m = re.search(r"entry = ADDR_W'\((\d+)\);\s*//\s*(\w+) ML-KEM-(\d+)", line)
            if m:
                entries[(m.group(2), int(m.group(3)))] = int(m.group(1))
    return rom, entries


def vectors(op):
    """ACVP ML-KEM-768 inputs for one operation."""
    jobs = []
    if op == "keygen":
        with open(KEYGEN) as f:
            for g in json.load(f)["testGroups"]:
                if g["parameterSet"] == "ML-KEM-768":
                    jobs += [coresim.Job(0, 768, bytes.fromhex(t["d"] + t["z"])) for t in g["tests"]]
    else:
        func = {"encaps": "encapsulation", "decaps": "decapsulation"}[op]
        with open(ENCDEC) as f:
            for g in json.load(f)["testGroups"]:
                if g["parameterSet"] == "ML-KEM-768" and g.get("function") == func:
                    for t in g["tests"]:
                        data = t["ek"] + t["m"] if op == "encaps" else t["dk"] + t["c"]
                        jobs.append(coresim.Job(coresim.OPS[op], 768, bytes.fromhex(data)))
    return jobs


def profile(op, rom):
    jobs = vectors(op)
    results, rows = coresim.run(jobs, profile=True)
    for r in results:
        if r.status != 2:
            raise SystemExit("%s job failed: status=%d err=%d" % (op, r.status, r.err))
    n = len(jobs)
    per_pc = collections.defaultdict(lambda: [0, 0])     # pc -> [exec, ovh] summed over jobs
    for job, pc, _op, ex, ov in rows:
        per_pc[pc][0] += ex
        per_pc[pc][1] += ov
    cls = collections.OrderedDict((k, {"n": 0, "exec": 0.0, "ovh": 0.0, "bytes": 0}) for k in ORDER + ["startup"])
    for pc, (ex, ov) in per_pc.items():
        if pc == STARTUP_PC:
            name, ln = "startup", 0
        else:
            name, ln = rom[pc][0], rom[pc][1]
        c = cls.setdefault(name, {"n": 0, "exec": 0.0, "ovh": 0.0, "bytes": 0})
        c["n"] += 1
        c["exec"] += ex / n
        c["ovh"] += ov / n
        if name in BYTE_OPS:
            c["bytes"] += ln
    total = sum(c["exec"] + c["ovh"] for c in cls.values())
    cyc = [r.cycles for r in results]
    return {"op": op, "vectors": n, "total": total, "cycles_min": min(cyc), "cycles_max": max(cyc),
            "cycles_mean": sum(cyc) / n, "classes": cls,
            "per_pc": {pc: (v[0] / n, v[1] / n) for pc, v in per_pc.items()}}


def table(p, rom, per_pc=False):
    out = []
    w = out.append
    w("### %s-768 (%d ACVP vectors, CYCLE_COUNT mean %.0f, min %d, max %d)"
      % (p["op"].capitalize(), p["vectors"], p["cycles_mean"], p["cycles_min"], p["cycles_max"]))
    w("")
    w("| Instr | # | Exec cycles | Overhead | Total | Share | Bytes | Cycles/byte |")
    w("| ----- | -: | ----------: | -------: | ----: | ----: | ----: | ----------: |")
    for name, c in p["classes"].items():
        if c["n"] == 0:
            continue
        tot = c["exec"] + c["ovh"]
        bpc = "%.2f" % (c["exec"] / c["bytes"]) if c["bytes"] else ""
        w("| %s | %d | %.0f | %.0f | %.0f | %.1f%% | %s | %s |"
          % (name, c["n"], c["exec"], c["ovh"], tot, 100 * tot / p["total"],
             c["bytes"] or "", bpc))
    w("| **Total** | %d | %.0f | %.0f | **%.0f** | 100%% | | |"
      % (sum(c["n"] for c in p["classes"].values()),
         sum(c["exec"] for c in p["classes"].values()),
         sum(c["ovh"] for c in p["classes"].values()), p["total"]))
    if abs(p["total"] - p["cycles_mean"]) > 0.5:
        w("")
        w("WARNING: profile total %.1f != CYCLE_COUNT mean %.1f" % (p["total"], p["cycles_mean"]))
    if per_pc:
        w("")
        w("| pc | Exec | Ovh | Instruction |")
        w("| -: | ---: | --: | ----------- |")
        for pc in sorted(p["per_pc"]):
            ex, ov = p["per_pc"][pc]
            txt = "(start-up)" if pc == STARTUP_PC else rom[pc][2]
            w("| %d | %.0f | %.0f | `%s` |" % (pc, ex, ov, txt))
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--op", choices=["keygen", "encaps", "decaps", "all"], default="decaps")
    ap.add_argument("--per-pc", action="store_true", help="also list cycles of every instruction")
    ap.add_argument("--md", help="also write the tables to this Markdown file")
    ap.add_argument("--json", help="also write the raw class totals to this JSON file")
    args = ap.parse_args()

    rom, _ = read_rom()
    ops = ["keygen", "encaps", "decaps"] if args.op == "all" else [args.op]
    profs = [profile(op, rom) for op in ops]
    text = "\n\n".join(table(p, rom, args.per_pc) for p in profs)
    print(text)
    if args.md:
        os.makedirs(os.path.dirname(os.path.abspath(args.md)), exist_ok=True)
        with open(args.md, "w") as f:
            f.write(text + "\n")
    if args.json:
        os.makedirs(os.path.dirname(os.path.abspath(args.json)), exist_ok=True)
        with open(args.json, "w") as f:
            json.dump([{k: v for k, v in p.items() if k != "per_pc"} for p in profs], f, indent=1)


if __name__ == "__main__":
    main()
