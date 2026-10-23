#!/usr/bin/env python3
"""NIST ACVP conformance test of the ML-KEM-768 core (FIPS 203).

Runs the ACVP-Server ML-KEM vector sets (internalProjection.json, i.e. with
the expected answers from NIST) against the core and writes a report.

Targets (same vectors, same checks):
  uart   the FPGA board, over the USB-UART bridge (hardware test)
  sim    the RTL in Verilator (testbench/core_sim), no board needed

Test groups (--groups, default: all):
  keygen    keyGen AFT                25   d||z  -> ek||dk     must equal NIST (ek, dk)
  encaps    encapsulation AFT         25   ek||m -> c||K       must equal NIST (c, k)
  decaps    decapsulation VAL         10   dk||c -> K          must equal NIST k
                                           (5 valid, 5 modified ciphertexts: implicit rejection)
  ekcheck   encapsulationKeyCheck     10   Encaps(ek) fails with ERROR_CODE 2 iff NIST says invalid
  errors    parameter checks          10   SEC_LEVEL 512/1024/999 and OP_MODE 3 -> ERROR_CODE 1
keygen + encaps + decaps are the 60 required vectors.

Usage:
  uv run python scripts/test/acvp_test.py ports                         # list serial ports
  uv run python scripts/test/acvp_test.py uart -p /dev/cu.usbserial-XXXX1
  uv run python scripts/test/acvp_test.py uart -p COM5 --groups keygen decaps
  uv run python scripts/test/acvp_test.py sim

Results go to data/acvp/<YYYYmmdd_HHMMSS>_<target>/:
  summary.md     report (per group, cycle statistics, failures)
  results.csv    one row per test case
  summary.json   the same, machine readable
  console.log    everything printed on the console
  failures/      input / expected / got (hex) of every failing case

Exit status: 0 all selected tests passed, 1 a test failed, 2 setup error.
Guide: docs/ACVP_TEST.md
"""

import argparse
import csv
import datetime
import json
import os
import platform
import struct
import subprocess
import sys
import tempfile
import time
import zlib
from dataclasses import dataclass, field

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
DEFAULT_VECTORS = os.path.join(ROOT, "tests", "acvp")
DEFAULT_OUT = os.path.join(ROOT, "data", "acvp")
SIM_DIR = os.path.join(ROOT, "testbench", "core_sim")
SIM_BINARY = os.path.join(SIM_DIR, "obj_dir", "Vtb_core")

# ML-KEM-768 (FIPS 203) sizes in bytes.
LEVEL = 768
EK_LEN, DK_LEN, CT_LEN, K_LEN = 1184, 2400, 1088, 32

CORE_MHZ = 206.0                 # ML-KEM core clock on the Arty A7 (MMCM); CYCLE_COUNT counts it.

OP_KEYGEN, OP_ENCAPS, OP_DECAPS = 0, 1, 2
OP_NAMES = {OP_KEYGEN: "KeyGen", OP_ENCAPS: "Encaps", OP_DECAPS: "Decaps"}

# STATUS bits and ERROR_CODE values (hdl/core/pqc_axi_csr.sv, mlkem_ctrl.sv).
ST_BUSY, ST_DONE, ST_ERROR = 1, 2, 4
ERRORS = {0: "none", 1: "unsupported SEC_LEVEL / OP_MODE",
          2: "encapsulation key failed the modulus check", 3: "illegal microcode"}

GROUPS = ["keygen", "encaps", "decaps", "ekcheck", "errors"]
REQUIRED = ("keygen", "encaps", "decaps")
GROUP_TITLES = {
    "keygen":  "keyGen (AFT)",
    "encaps":  "encapsulation (AFT)",
    "decaps":  "decapsulation (VAL, incl. implicit rejection)",
    "ekcheck": "encapsulationKeyCheck (VAL)",
    "errors":  "parameter checks (ERROR_CODE 1)",
}


# ===========================================================================
# Test cases
# ===========================================================================

@dataclass
class Case:
    group: str           # keygen / encaps / decaps / ekcheck / errors
    tcid: str            # ACVP tcId, or a name for the error checks
    op: int              # OP_MODE written to the core
    level: int           # SEC_LEVEL written to the core
    data: bytes          # input written at DATA_IN_ADDR
    expect_out: bytes = b""    # expected output (keygen / encaps / decaps)
    expect_err: int = 0        # expected ERROR_CODE (0 = success)
    note: str = ""             # e.g. ACVP "reason"


@dataclass
class Outcome:
    status: int          # STATUS register after the operation
    err: int             # ERROR_CODE register
    cycles: int          # CYCLE_COUNT register
    out: bytes = b""     # DATA_OUT_LEN bytes from DATA_OUT_ADDR (successful runs)
    seconds: float = 0.0 # wall-clock time of the whole transaction (UART only)
    link_error: str = "" # UART protocol problem, if any


@dataclass
class Result:
    case: Case
    outcome: Outcome
    passed: bool
    detail: str


@dataclass
class Report:
    target: str
    started: str
    vector_info: dict
    preflight: list = field(default_factory=list)   # (name, ok, detail)
    results: list = field(default_factory=list)
    skipped: dict = field(default_factory=dict)


def load_cases(vectors_dir, groups):
    """ACVP ML-KEM-768 cases of the selected groups, plus the error checks."""
    cases, info, skipped = [], {}, {}
    kg_path = os.path.join(vectors_dir, "ML-KEM-keyGen-FIPS203", "internalProjection.json")
    ed_path = os.path.join(vectors_dir, "ML-KEM-encapDecap-FIPS203", "internalProjection.json")
    for path in (kg_path, ed_path):
        if not os.path.exists(path):
            raise SystemExit("vector file not found: %s" % path)
        with open(path) as f:
            vs = json.load(f)
        name = os.path.basename(os.path.dirname(path))
        info[name] = {"vsId": vs.get("vsId"), "mode": vs.get("mode"), "revision": vs.get("revision")}
        for g in vs["testGroups"]:
            if g["parameterSet"] != "ML-KEM-%d" % LEVEL:
                skipped["other parameter sets"] = skipped.get("other parameter sets", 0) + len(g["tests"])
                continue
            func = g.get("function", "keyGen")
            for t in g["tests"]:
                h = {k: bytes.fromhex(v) for k, v in t.items()
                     if k in ("d", "z", "ek", "dk", "c", "k", "m")}
                tc = str(t["tcId"])
                if func == "keyGen" and "keygen" in groups:
                    cases.append(Case("keygen", tc, OP_KEYGEN, LEVEL, h["d"] + h["z"],
                                      expect_out=h["ek"] + h["dk"]))
                elif func == "encapsulation" and "encaps" in groups:
                    cases.append(Case("encaps", tc, OP_ENCAPS, LEVEL, h["ek"] + h["m"],
                                      expect_out=h["c"] + h["k"]))
                elif func == "decapsulation" and "decaps" in groups:
                    cases.append(Case("decaps", tc, OP_DECAPS, LEVEL, h["dk"] + h["c"],
                                      expect_out=h["k"], note=t.get("reason", "")))
                elif func == "encapsulationKeyCheck" and "ekcheck" in groups:
                    # Any m works: only the modulus check of ek is tested.
                    cases.append(Case("ekcheck", tc, OP_ENCAPS, LEVEL, h["ek"] + bytes(32),
                                      expect_err=0 if t["testPassed"] else 2,
                                      note=t.get("reason", "")))
                elif func == "decapsulationKeyCheck":
                    skipped["decapsulationKeyCheck (not implemented by the core)"] = \
                        skipped.get("decapsulationKeyCheck (not implemented by the core)", 0) + 1
    if "errors" in groups:
        for lvl in (512, 1024, 999):
            for op in (OP_KEYGEN, OP_ENCAPS, OP_DECAPS):
                cases.append(Case("errors", "SEC_LEVEL=%d %s" % (lvl, OP_NAMES[op]), op, lvl,
                                  bytes(64), expect_err=1))
        cases.append(Case("errors", "OP_MODE=3", 3, LEVEL, bytes(64), expect_err=1))
    return cases, info, skipped


def check(case, o):
    """(passed, detail) for one case."""
    if o.link_error:
        return False, "UART: " + o.link_error
    failed = bool(o.status & ST_ERROR)
    if case.expect_err:
        if failed and o.err == case.expect_err:
            return True, "ERROR_CODE %d as expected" % o.err
        if failed:
            return False, "ERROR_CODE %d (%s), expected %d" % (o.err, ERRORS.get(o.err, "?"), case.expect_err)
        return False, "operation succeeded, expected ERROR_CODE %d" % case.expect_err
    if failed:
        return False, "core reported ERROR_CODE %d (%s)" % (o.err, ERRORS.get(o.err, "?"))
    if not o.status & ST_DONE:
        return False, "STATUS 0x%x: not done" % o.status
    if not case.expect_out:                  # ekcheck, valid key: success is all we need
        return True, "accepted as expected"
    if len(o.out) != len(case.expect_out):
        return False, "output length %d, expected %d" % (len(o.out), len(case.expect_out))
    if o.out != case.expect_out:
        i = next(i for i in range(len(o.out)) if o.out[i] != case.expect_out[i])
        bad = sum(a != b for a, b in zip(o.out, case.expect_out))
        part = ""
        if case.group == "keygen":
            part = " (in ek)" if i < EK_LEN else " (in dk)"
        elif case.group == "encaps":
            part = " (in c)" if i < CT_LEN else " (in K)"
        return False, "output differs from NIST: first at byte %d%s, %d byte(s) differ" % (i, part, bad)
    return True, "matches NIST"


# ===========================================================================
# Target: FPGA over UART
# ===========================================================================

class ProtocolError(Exception):
    pass


class UartTarget:
    """The host protocol of hdl/core/uart_axi_bridge.sv (docs/UART_PROTOCOL.md):

    host -> FPGA  [CMD:1][ADDR:4][LEN:4][DATA:LEN][CRC32:4]   little-endian
    FPGA -> host  [STATUS:1][LEN:4][DATA:LEN][CRC32:4]
    CMD 0x01 read register, 0x02 write register, 0x03 write buffer, 0x04 read buffer,
    0x05 Run: write CTRL.start, reply only when the core is done (STATUS, CYCLE_COUNT,
    ERROR_CODE, then the output). Operations are started only with 0x05.
    """

    CMD_READ_REG, CMD_WRITE_REG, CMD_WRITE_DATA, CMD_READ_DATA, CMD_RUN = 0x01, 0x02, 0x03, 0x04, 0x05
    OUT_LEN = {OP_KEYGEN: 1184 + 2400, OP_ENCAPS: 1088 + 32, OP_DECAPS: 32}   # bytes at OUT_BASE
    MAX_WRITE = 256                  # Largest 0x03 payload the bridge accepts.
    REG = {"CTRL": 0x00, "STATUS": 0x04, "ALG_ID": 0x08, "SEC_LEVEL": 0x0C,
           "OP_MODE": 0x10, "CYCLE_COUNT": 0x14, "VERSION": 0x18, "ERROR_CODE": 0x1C,
           "DATA_IN_ADDR": 0x20, "DATA_IN_LEN": 0x24, "DATA_OUT_ADDR": 0x28,
           "DATA_OUT_LEN": 0x2C}
    IN_BASE, OUT_BASE = 0x0000, 0x0E00   # Data buffer layout (scripts/gen/gen_mlkem_ucode.py).

    def __init__(self, port, baud, timeout, op_timeout):
        try:
            import serial  # pyserial
        except ImportError:
            raise SystemExit("pyserial is missing: run the script with `uv run python ...` "
                             "or install it with `python3 -m pip install pyserial`")
        try:
            self.ser = serial.Serial(port, baud, bytesize=8, parity="N", stopbits=1, timeout=timeout)
        except Exception as e:  # serial.SerialException and OS errors
            raise SystemExit("cannot open %s: %s" % (port, e))
        self.op_timeout = op_timeout
        time.sleep(0.05)
        self.ser.reset_input_buffer()

    def close(self):
        self.ser.close()

    # --- frames -------------------------------------------------------------
    def command(self, cmd, addr, data=b"", wait=0.0):
        """One frame; ``wait`` is extra time the reply may take (0x05 answers after the operation)."""
        frame = struct.pack("<BII", cmd, addr, len(data)) + data
        frame += struct.pack("<I", zlib.crc32(frame) & 0xFFFFFFFF)
        self.ser.reset_input_buffer()
        self.ser.write(frame)
        hdr = self._read(5, wait)
        status, n = struct.unpack("<BI", hdr)
        if n > 16384:
            raise ProtocolError("response LEN=%d: link out of sync" % n)
        body = self._read(n + 4)
        payload, crc = body[:n], struct.unpack("<I", body[n:])[0]
        if zlib.crc32(hdr + payload) & 0xFFFFFFFF != crc:
            raise ProtocolError("response CRC mismatch")
        if status != 0:
            raise ProtocolError("bridge returned error status 0x%02x" % status)
        return payload

    def _read(self, n, wait=0.0):
        base = self.ser.timeout
        self.ser.timeout = base + wait + 20.0 * n / self.ser.baudrate   # 10 bit times per byte, x2
        try:
            buf = self.ser.read(n)
        finally:
            self.ser.timeout = base
        if len(buf) != n:
            raise ProtocolError("timeout: got %d of %d bytes" % (len(buf), n))
        return buf

    def rd(self, name):
        return struct.unpack("<I", self.command(self.CMD_READ_REG, self.REG[name]))[0]

    def wr(self, name, value):
        self.command(self.CMD_WRITE_REG, self.REG[name], struct.pack("<I", value & 0xFFFFFFFF))

    def write_data(self, offset, data):
        for i in range(0, len(data), self.MAX_WRITE):
            self.command(self.CMD_WRITE_DATA, offset + i, data[i:i + self.MAX_WRITE])

    def read_data(self, offset, length):
        data = self.command(self.CMD_READ_DATA, offset, struct.pack("<I", length))
        if len(data) != length:
            raise ProtocolError("read %d bytes, expected %d" % (len(data), length))
        return data

    def resync(self):
        """After a protocol error: drain the line and check the link answers."""
        time.sleep(0.2)                       # Longer than the bridge's 100 ms frame timeout.
        self.ser.reset_input_buffer()
        try:
            return self.rd("ALG_ID") == 1
        except ProtocolError:
            return False

    # --- preflight ----------------------------------------------------------
    def preflight(self, log):
        """Layer-by-layer bring-up checks; returns [(name, ok, detail)]."""
        checks = []

        def step(name, fn):
            try:
                ok, detail = fn()
            except ProtocolError as e:
                ok, detail = False, str(e)
            checks.append((name, ok, detail))
            log("  %-44s %s  %s" % (name, "PASS" if ok else "FAIL", detail))
            return ok

        def alg():
            v = self.rd("ALG_ID")
            return v == 1, "ALG_ID=%d (1 = ML-KEM)" % v

        def version():
            v = self.rd("VERSION")
            return v != 0, "v%d.%d.%d" % ((v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF)

        def reset():
            self.wr("CTRL", 2)                      # CTRL.reset pulse
            st = self.rd("STATUS")
            return st == 0, "STATUS=0x%x after CTRL.reset" % st

        def regs():
            self.wr("SEC_LEVEL", 999)
            back = self.rd("SEC_LEVEL")
            self.wr("SEC_LEVEL", LEVEL)
            return back == 999 and self.rd("SEC_LEVEL") == LEVEL, "SEC_LEVEL write/read-back"

        def bad_crc():
            frame = struct.pack("<BII", self.CMD_READ_REG, self.REG["ALG_ID"], 0)
            frame += struct.pack("<I", (zlib.crc32(frame) ^ 1) & 0xFFFFFFFF)
            self.ser.reset_input_buffer()
            self.ser.write(frame)
            resp = self._read(9)
            again = self.rd("ALG_ID") == 1
            return resp[0] == 1 and again, "frame with a wrong CRC rejected (status %d), link still in sync" % resp[0]

        def loopback():
            data = os.urandom(1024)
            t0 = time.time()
            self.write_data(self.OUT_BASE, data)
            back = self.read_data(self.OUT_BASE, len(data))
            dt = time.time() - t0
            odd = os.urandom(101)
            self.write_data(self.OUT_BASE + 1, odd)
            ok = back == data and self.read_data(self.OUT_BASE + 1, len(odd)) == odd
            return ok, "1024 B aligned + 101 B unaligned, %.1f kB/s" % (2 * len(data) / dt / 1e3)

        def addrs():
            i, o = self.rd("DATA_IN_ADDR"), self.rd("DATA_OUT_ADDR")
            return True, "DATA_IN_ADDR=0x%04x DATA_OUT_ADDR=0x%04x (the test writes 0x%04x / 0x%04x)" % (
                i, o, self.IN_BASE, self.OUT_BASE)

        def run_cmd():
            # 0x05 that writes 0 to the read-only ALG_ID: starts nothing, answers at once.
            try:
                blk = self.command(self.CMD_RUN, self.REG["ALG_ID"], struct.pack("<III", 0, 0, 0), wait=0.5)
            except ProtocolError as e:
                if "error status" not in str(e):
                    raise
                return False, ("this bitstream has no 0x05 Run, the only way operations are started: "
                               "rebuild it from hdl/core/uart_axi_bridge.sv")
            return len(blk) == 12, "0x05 Run answers with a 12-byte status block"

        for name, fn in (("1. link: read ALG_ID", alg), ("2. core VERSION", version),
                         ("3. CTRL.reset clears STATUS", reset), ("4. register write / read", regs),
                         ("5. bad CRC frame rejected", bad_crc), ("6. data buffer loopback", loopback),
                         ("7. buffer offsets", addrs), ("8. 0x05 Run (completion notice)", run_cmd)):
            if not step(name, fn):
                break
        return checks

    # --- one ML-KEM operation -------------------------------------------------
    def run(self, case):
        t0 = time.time()
        try:
            self.write_data(self.IN_BASE, case.data)
            self.wr("DATA_IN_ADDR", self.IN_BASE)
            self.wr("DATA_IN_LEN", len(case.data))
            self.wr("DATA_OUT_ADDR", self.OUT_BASE)
            self.wr("SEC_LEVEL", case.level)
            self.wr("OP_MODE", case.op)
            # 0x05 Run: CTRL.start, then one reply when the core is done -- status block + output
            # (the output only when STATUS = done without error).
            args = struct.pack("<III", 1, self.OUT_BASE, self.OUT_LEN.get(case.op, 0))
            blk = self.command(self.CMD_RUN, self.REG["CTRL"], args, wait=self.op_timeout)
            if len(blk) < 12:
                raise ProtocolError("Run reply of %d bytes" % len(blk))
            st, cycles, err = struct.unpack("<III", blk[:12])
            out = blk[12:]
            if st & ST_BUSY:
                raise ProtocolError("operation did not finish within the FPGA's Run timeout (STATUS=0x%x)" % st)
            if case.level != LEVEL:
                self.wr("SEC_LEVEL", LEVEL)
            return Outcome(st, err, cycles, out, time.time() - t0)
        except ProtocolError as e:
            return Outcome(0, 0, 0, b"", time.time() - t0, link_error=str(e))


# ===========================================================================
# Target: RTL in Verilator
# ===========================================================================

def run_sim(cases, log):
    """All cases in one run of testbench/core_sim (AXI-Lite host flow on
    pqc_mlkem_top, see tb_core.sv for the job / result formats)."""
    log("building the Verilator model (make -C testbench/core_sim) ...")
    r = subprocess.run(["make", "-C", SIM_DIR], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("Verilator build failed:\n%s%s" % (r.stdout, r.stderr))
    tmp = tempfile.mkdtemp(prefix="acvp_sim_")
    jobs, res = os.path.join(tmp, "jobs.txt"), os.path.join(tmp, "results.txt")
    with open(jobs, "w") as f:
        f.write("%d\n" % len(cases))
        for c in cases:
            f.write("%d %d %d\n%s\n" % (c.op, c.level, len(c.data), " ".join("%02x" % b for b in c.data)))
    log("simulating %d operations ..." % len(cases))
    t0 = time.time()
    r = subprocess.run([SIM_BINARY, "+jobs=" + jobs, "+out=" + res], capture_output=True, text=True)
    if r.returncode != 0 or "TB_CORE DONE" not in r.stdout:
        raise SystemExit("simulation failed:\n%s%s" % (r.stdout, r.stderr))
    outcomes = []
    with open(res) as f:
        for line in f:
            if not line.startswith("JOB "):
                continue
            kv = dict(p.split("=", 1) for p in line.split()[2:])
            outcomes.append(Outcome(int(kv["status"]), int(kv["err"]), int(kv["cycles"]),
                                    bytes.fromhex(kv["out"])))
    if len(outcomes) != len(cases):
        raise SystemExit("got %d results for %d cases" % (len(outcomes), len(cases)))
    log("simulation finished in %.1f s" % (time.time() - t0))
    return outcomes


# ===========================================================================
# Reports
# ===========================================================================

def show(path):
    """Path relative to the repository when inside it, else absolute."""
    rel = os.path.relpath(path, ROOT)
    return path if rel.startswith("..") else rel


def git_info():
    try:
        head = subprocess.run(["git", "-C", ROOT, "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", ROOT, "status", "--porcelain", "hdl"],
                               capture_output=True, text=True).stdout.strip()
        return head + (" (hdl/ has uncommitted changes)" if dirty else "")
    except OSError:
        return "unknown"


def cycle_stats(results):
    """Per operation: (n, min, mean, max) cycles of successful runs."""
    stats = {}
    for r in results:
        if r.case.group in REQUIRED and r.passed:
            stats.setdefault(OP_NAMES[r.case.op], []).append(r.outcome.cycles)
    return {k: (len(v), min(v), sum(v) / len(v), max(v)) for k, v in stats.items()}


def write_reports(rep, run_dir, args, console):
    os.makedirs(run_dir, exist_ok=True)
    groups = [g for g in GROUPS if any(r.case.group == g for r in rep.results)]
    req = [r for r in rep.results if r.case.group in REQUIRED]
    req_pass = sum(r.passed for r in req)
    all_pass = all(r.passed for r in rep.results) and all(ok for _, ok, _ in rep.preflight)
    verdict = "PASS" if all_pass and rep.results else "FAIL"

    # results.csv
    with open(os.path.join(run_dir, "results.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["group", "tcId", "result", "op", "sec_level", "status", "error_code",
                    "cycles", "core_time_us", "uart_seconds", "detail", "acvp_reason"])
        for r in rep.results:
            o = r.outcome
            w.writerow([r.case.group, r.case.tcid, "PASS" if r.passed else "FAIL",
                        OP_NAMES.get(r.case.op, str(r.case.op)), r.case.level,
                        "0x%x" % o.status, o.err, o.cycles,
                        "%.2f" % (o.cycles / CORE_MHZ) if o.cycles else "",
                        "%.3f" % o.seconds if o.seconds else "", r.detail, r.case.note])

    # failures/
    fails = [r for r in rep.results if not r.passed]
    if fails:
        fdir = os.path.join(run_dir, "failures")
        os.makedirs(fdir, exist_ok=True)
        for r in fails:
            name = "%s_%s.txt" % (r.case.group, r.case.tcid.replace(" ", "_").replace("=", ""))
            with open(os.path.join(fdir, name), "w") as f:
                f.write("group:    %s\ntcId:     %s\nresult:   %s\nreason:   %s\n"
                        % (r.case.group, r.case.tcid, r.detail, r.case.note))
                f.write("input:    %s\nexpected: %s\ngot:      %s\n"
                        % (r.case.data.hex(), r.case.expect_out.hex() or "ERROR_CODE %d" % r.case.expect_err,
                           r.outcome.out.hex() or "STATUS 0x%x ERROR_CODE %d" % (r.outcome.status, r.outcome.err)))

    stats = cycle_stats(rep.results)

    # summary.json
    with open(os.path.join(run_dir, "summary.json"), "w") as f:
        json.dump({
            "verdict": verdict, "target": rep.target, "started": rep.started,
            "port": getattr(args, "port", None), "git": git_info(), "core_mhz": CORE_MHZ,
            "vectors": rep.vector_info, "skipped": rep.skipped,
            "required": {"passed": req_pass, "total": len(req)},
            "preflight": [{"check": n, "ok": ok, "detail": d} for n, ok, d in rep.preflight],
            "groups": {g: {"passed": sum(r.passed for r in rep.results if r.case.group == g),
                           "total": sum(1 for r in rep.results if r.case.group == g)} for g in groups},
            "cycles": {k: {"n": v[0], "min": v[1], "mean": round(v[2], 1), "max": v[3],
                           "mean_us": round(v[2] / CORE_MHZ, 2)} for k, v in stats.items()},
            "failures": [{"group": r.case.group, "tcId": r.case.tcid, "detail": r.detail} for r in fails],
        }, f, indent=2)

    # summary.md
    L = []
    w = L.append
    w("# ACVP 测试报告：ML-KEM-768（%s）" % ("FPGA，UART" if rep.target == "uart" else "RTL 仿真，Verilator"))
    w("")
    w("**结论：%s**　必测向量 %d/%d 通过（keyGen 25 + encapsulation 25 + decapsulation 10）。"
      % (verdict, req_pass, len(req)))
    w("")
    w("| 项目 | 值 |")
    w("| ---- | -- |")
    w("| 时间 | %s |" % rep.started)
    w("| 目标 | %s |" % ("FPGA 板卡，串口 %s，%d baud" % (args.port, args.baud) if rep.target == "uart"
                         else "RTL 仿真（testbench/core_sim，Verilator）"))
    w("| 源码版本 | git %s%s |" % (git_info(), "（板卡上的 bitstream 不一定是这个版本）" if rep.target == "uart" else ""))
    w("| 向量 | %s |" % "；".join("%s vsId %s" % (k, v["vsId"]) for k, v in rep.vector_info.items()))
    w("| 核心时钟 | %.2f MHz（CYCLE_COUNT 按核心时钟计数） |" % CORE_MHZ)
    w("| 主机 | %s，Python %s |" % (platform.platform(), platform.python_version()))
    w("")
    if rep.preflight:
        w("## 上板自检")
        w("")
        w("| 检查 | 结果 | 说明 |")
        w("| ---- | ---- | ---- |")
        for n, ok, d in rep.preflight:
            w("| %s | %s | %s |" % (n, "PASS" if ok else "**FAIL**", d))
        w("")
    w("## 各组结果")
    w("")
    w("| 组 | 通过 / 总数 | 结果 |")
    w("| -- | ----------: | ---- |")
    for g in groups:
        rs = [r for r in rep.results if r.case.group == g]
        p = sum(r.passed for r in rs)
        w("| %s | %d / %d | %s |" % (GROUP_TITLES[g], p, len(rs), "PASS" if p == len(rs) else "**FAIL**"))
    for k, v in rep.skipped.items():
        w("| 跳过：%s | %d | — |" % (k, v))
    w("")
    if stats:
        w("## 运算周期（必测向量中通过的用例）")
        w("")
        w("| 运算 | 用例数 | 最少周期 | 平均周期 | 最多周期 | 平均时间 @ %.2f MHz |" % CORE_MHZ)
        w("| ---- | -----: | -------: | -------: | -------: | ------------------: |")
        for k in ("KeyGen", "Encaps", "Decaps"):
            if k in stats:
                n, lo, mean, hi = stats[k]
                w("| %s | %d | %d | %.0f | %d | %.1f µs |" % (k, n, lo, mean, hi, mean / CORE_MHZ))
        w("")
        w("Decaps 的周期数随密文略有变化：重加密时 SampleNTT 的拒绝采样次数取决于输入。")
        w("")
    w("## 全部用例")
    w("")
    w("| 组 | tcId | 结果 | 周期 | 说明 | ACVP reason |")
    w("| -- | ---- | ---- | ---: | ---- | ----------- |")
    for r in rep.results:
        w("| %s | %s | %s | %s | %s | %s |" % (r.case.group, r.case.tcid, "PASS" if r.passed else "**FAIL**",
                                              r.outcome.cycles or "", r.detail, r.case.note))
    w("")
    if fails:
        w("失败用例的输入、期望值和实际输出见 `failures/`。")
        w("")
    w("文件：`results.csv`（每个用例一行）、`summary.json`、`console.log`。")
    with open(os.path.join(run_dir, "summary.md"), "w") as f:
        f.write("\n".join(L) + "\n")
    with open(os.path.join(run_dir, "console.log"), "w") as f:
        f.write("\n".join(console) + "\n")
    return verdict


# ===========================================================================
# Main
# ===========================================================================

def cmd_ports(_args):
    try:
        from serial.tools import list_ports
    except ImportError:
        raise SystemExit("pyserial is missing: run the script with `uv run python ...`")
    ports = list(list_ports.comports())
    if not ports:
        print("no serial ports found (is the board plugged in?)")
    for p in ports:
        print("%-32s %s  %s" % (p.device, p.description, p.hwid))
    print("\nArty A7: the FTDI chip has two interfaces; the UART is the second one "
          "(macOS: /dev/cu.usbserial-XXXXXXXX1, Linux: /dev/ttyUSB1, Windows: the higher COM number).")
    print("SAKURA-X board USB: pass sakura:<channel A port>, e.g. sakura:/dev/ttyUSB0.")
    return 0


def run_tests(args):
    started = datetime.datetime.now()
    run_dir = os.path.join(args.out, "%s_%s" % (started.strftime("%Y%m%d_%H%M%S"), args.target))
    console = []

    def log(msg=""):
        print(msg, flush=True)
        console.append(msg)

    groups = args.groups
    cases, info, skipped = load_cases(args.vectors, groups)
    rep = Report(args.target, started.strftime("%Y-%m-%d %H:%M:%S"), info, skipped=skipped)
    log("ACVP ML-KEM-%d test, target: %s, %d cases (%s)" % (LEVEL, args.target, len(cases), ", ".join(groups)))
    log("results: %s" % show(run_dir))
    log("")

    if args.target == "uart":
        dev = UartTarget(args.port, args.baud, args.timeout, args.op_timeout)
        try:
            if not args.no_preflight:
                log("Preflight (%s, %d baud)" % (args.port, args.baud))
                rep.preflight = dev.preflight(log)
                log("")
                if not all(ok for _, ok, _ in rep.preflight):
                    log("preflight failed: see docs/ACVP_TEST.md, section troubleshooting")
                    write_reports(rep, run_dir, args, console)
                    return 2
            aborted = False
            for i, c in enumerate(cases):
                if aborted:
                    o = Outcome(0, 0, 0, link_error="not run: link lost")
                else:
                    o = dev.run(c)
                    if o.link_error and not dev.resync():
                        aborted = True
                ok, detail = check(c, o)
                rep.results.append(Result(c, o, ok, detail))
                log("[%3d/%d] %-8s %-24s %s %7s cyc %6.2f s  %s"
                    % (i + 1, len(cases), c.group, c.tcid, "PASS" if ok else "FAIL",
                       o.cycles or "-", o.seconds, detail if (not ok or args.verbose) else ""))
        finally:
            dev.close()
    else:
        outcomes = run_sim(cases, log)
        for i, (c, o) in enumerate(zip(cases, outcomes)):
            ok, detail = check(c, o)
            rep.results.append(Result(c, o, ok, detail))
            log("[%3d/%d] %-8s %-24s %s %7s cyc  %s"
                % (i + 1, len(cases), c.group, c.tcid, "PASS" if ok else "FAIL",
                   o.cycles or "-", detail if (not ok or args.verbose) else ""))

    log("")
    for g in GROUPS:
        rs = [r for r in rep.results if r.case.group == g]
        if rs:
            p = sum(r.passed for r in rs)
            log("  %-48s %3d/%-3d %s" % (GROUP_TITLES[g], p, len(rs), "PASS" if p == len(rs) else "FAIL"))
    for k, v in skipped.items():
        log("  skipped: %s: %d" % (k, v))
    req = [r for r in rep.results if r.case.group in REQUIRED]
    log("")
    log("required ACVP vectors: %d/%d" % (sum(r.passed for r in req), len(req)))
    verdict = write_reports(rep, run_dir, args, console)
    print("ACVP %s   report: %s" % (verdict, show(os.path.join(run_dir, "summary.md"))))
    return 0 if verdict == "PASS" else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="target", required=True)
    sub.add_parser("ports", help="list serial ports")

    def common(p):
        p.add_argument("--groups", nargs="+", choices=GROUPS, default=GROUPS,
                       help="test groups to run (default: all)")
        p.add_argument("--vectors", default=DEFAULT_VECTORS,
                       help="directory with ML-KEM-keyGen-FIPS203/ and ML-KEM-encapDecap-FIPS203/ (default: tests/acvp/)")
        p.add_argument("--out", default=DEFAULT_OUT, help="results directory (default: data/acvp)")
        p.add_argument("-v", "--verbose", action="store_true", help="print the detail of passing cases too")

    p = sub.add_parser("uart", help="test the FPGA board over UART")
    p.add_argument("-p", "--port", required=True, help="serial port, e.g. /dev/cu.usbserial-XXXX1, /dev/ttyUSB1, COM5")
    p.add_argument("-b", "--baud", type=int, default=115200, help="baud rate (default 115200, as in the bitstream)")
    p.add_argument("--timeout", type=float, default=2.0, help="UART read timeout in seconds (default 2)")
    p.add_argument("--op-timeout", type=float, default=5.0, help="max seconds per ML-KEM operation (default 5)")
    p.add_argument("--no-preflight", action="store_true", help="skip the bring-up checks")
    common(p)

    p = sub.add_parser("sim", help="test the RTL in Verilator (no board needed)")
    common(p)

    args = ap.parse_args()
    if args.target == "ports":
        return cmd_ports(args)
    return run_tests(args)


if __name__ == "__main__":
    sys.exit(main())
