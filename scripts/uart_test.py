#!/usr/bin/env python3
"""Layer-by-layer UART test tool for the pqc-testkit ML-KEM FPGA core.

Speaks the same frame protocol as pkg/fpga/uart/serial.go and
hdl/core/uart_axi_bridge.sv:

  host -> FPGA  [CMD:1][ADDR:4][LEN:4][DATA:LEN][CRC32:4]   (little-endian)
  FPGA -> host  [STATUS:1][LEN:4][DATA:LEN][CRC32:4]

Requires pyserial:  python3 -m pip install pyserial

Examples:
  python3 scripts/uart_test.py -p /dev/cu.usbserial-XXXX1 selftest
  python3 scripts/uart_test.py -p /dev/ttyUSB1 info
  python3 scripts/uart_test.py -p /dev/ttyUSB1 read 0x08
  python3 scripts/uart_test.py -p /dev/ttyUSB1 write 0x0C 768
  python3 scripts/uart_test.py -p /dev/ttyUSB1 loopback --size 2000
  python3 scripts/uart_test.py -p /dev/ttyUSB1 roundtrip
  python3 scripts/uart_test.py -p /dev/ttyUSB1 run keygen --level 768 \\
          --in-hex <128 hex chars d||z> --out kg_out.bin
  python3 scripts/uart_test.py -p /dev/ttyUSB1 acvp \\
          ML-KEM-keyGen-FIPS203/internalProjection.json \\
          ML-KEM-encapDecap-FIPS203/internalProjection.json

Byte-for-byte comparison against the software reference (Level 6) is done
by the Go tool:  pqc-testkit fpga -T uart -d <port>

The core implements ML-KEM-768 only, so every
test defaults to level 768; pass --level/--levels for other builds.
"""

import argparse
import hashlib
import os
import struct
import sys
import time
import zlib

try:
    import serial  # pyserial
except ImportError:
    sys.exit("pyserial is required: python3 -m pip install pyserial")

# Commands.
CMD_READ_REG, CMD_WRITE_REG, CMD_WRITE_DATA, CMD_READ_DATA = 0x01, 0x02, 0x03, 0x04
MAX_WRITE = 256          # Largest 0x03 payload accepted by the RTL bridge.

# CSR offsets (pkg/fpga/device.go).
REG = {
    "CTRL": 0x00, "STATUS": 0x04, "ALG_ID": 0x08, "SEC_LEVEL": 0x0C,
    "OP_MODE": 0x10, "CYCLE_COUNT": 0x14, "VERSION": 0x18, "ERROR_CODE": 0x1C,
    "DATA_IN_ADDR": 0x20, "DATA_IN_LEN": 0x24, "DATA_OUT_ADDR": 0x28,
    "DATA_OUT_LEN": 0x2C,
}
IN_BASE, OUT_BASE = 0x0000, 0x0E00   # = IN_BASE / OUT_BASE in gen_mlkem_ucode.py
OPS = {"keygen": 0, "encaps": 1, "decaps": 2}
ERRORS = {0: "none", 1: "unsupported SEC_LEVEL/OP_MODE",
          2: "ek failed modulus check", 3: "illegal microcode"}

# FIPS 203 sizes: level -> (ek, dk, ct)
SIZES = {768: (1184, 2400, 1088), 1024: (1568, 3168, 1568)}   # ML-KEM-512 not supported.
DEFAULT_LEVEL = 768      # The only level the core implements.


class ProtocolError(Exception):
    pass


class Link:
    """One UART connection to the FPGA."""

    def __init__(self, port, baud, timeout):
        self.ser = serial.Serial(port, baud, bytesize=8, parity="N",
                                 stopbits=1, timeout=timeout)
        time.sleep(0.05)
        self.ser.reset_input_buffer()

    def close(self):
        self.ser.close()

    def command(self, cmd, addr, data=b""):
        frame = struct.pack("<BII", cmd, addr, len(data)) + data
        frame += struct.pack("<I", zlib.crc32(frame) & 0xFFFFFFFF)
        self.ser.reset_input_buffer()
        self.ser.write(frame)
        hdr = self._read(5)
        status, n = struct.unpack("<BI", hdr)
        if n > 16384:
            raise ProtocolError("response LEN=%d, link out of sync" % n)
        body = self._read(n + 4)
        payload, crc = body[:n], struct.unpack("<I", body[n:])[0]
        if zlib.crc32(hdr + payload) & 0xFFFFFFFF != crc:
            raise ProtocolError("response CRC mismatch")
        if status != 0:
            raise ProtocolError("FPGA returned error status 0x%02x" % status)
        return payload

    def _read(self, n):
        buf = self.ser.read(n)
        if len(buf) != n:
            raise ProtocolError("timeout: got %d of %d bytes (%s)" % (len(buf), n, buf.hex()))
        return buf

    # --- register / buffer access ------------------------------------------
    def rd(self, addr):
        return struct.unpack("<I", self.command(CMD_READ_REG, addr))[0]

    def wr(self, addr, value):
        self.command(CMD_WRITE_REG, addr, struct.pack("<I", value & 0xFFFFFFFF))

    def write_data(self, offset, data):
        for i in range(0, len(data), MAX_WRITE):
            self.command(CMD_WRITE_DATA, offset + i, data[i:i + MAX_WRITE])

    def read_data(self, offset, length):
        data = self.command(CMD_READ_DATA, offset, struct.pack("<I", length))
        if len(data) != length:
            raise ProtocolError("read %d bytes, expected %d" % (len(data), length))
        return data

    # --- ML-KEM operation ---------------------------------------------------
    def run(self, level, op, inp, timeout=5.0):
        self.write_data(IN_BASE, inp)
        self.wr(REG["DATA_IN_ADDR"], IN_BASE)
        self.wr(REG["DATA_IN_LEN"], len(inp))
        self.wr(REG["DATA_OUT_ADDR"], OUT_BASE)
        self.wr(REG["SEC_LEVEL"], level)
        self.wr(REG["OP_MODE"], op)
        self.wr(REG["CTRL"], 1)                      # start
        deadline = time.time() + timeout
        while True:
            st = self.rd(REG["STATUS"])
            if st & 4:
                code = self.rd(REG["ERROR_CODE"])
                raise ProtocolError("core error %d (%s)" % (code, ERRORS.get(code, "?")))
            if st & 2:
                break
            if time.time() > deadline:
                raise ProtocolError("timeout waiting for done (STATUS=0x%x)" % st)
        cycles = self.rd(REG["CYCLE_COUNT"])
        n = self.rd(REG["DATA_OUT_LEN"])
        return self.read_data(OUT_BASE, n), cycles


def input_size(level, op):
    ek, dk, ct = SIZES[level]
    return {0: 64, 1: ek + 32, 2: dk + ct}[op]


# --- sub-commands -------------------------------------------------------------

def cmd_info(link, _args):
    for name in ("ALG_ID", "VERSION", "SEC_LEVEL", "OP_MODE", "STATUS",
                 "CYCLE_COUNT", "ERROR_CODE", "DATA_IN_ADDR", "DATA_OUT_ADDR",
                 "DATA_OUT_LEN"):
        v = link.rd(REG[name])
        extra = ""
        if name == "ALG_ID":
            extra = {1: "ML-KEM", 2: "ML-DSA", 3: "SLH-DSA"}.get(v, "unknown")
        elif name == "VERSION":
            extra = "v%d.%d.%d" % ((v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF)
        elif name == "STATUS":
            extra = "busy=%d done=%d error=%d" % (v & 1, (v >> 1) & 1, (v >> 2) & 1)
        print("  %-14s 0x%08x  %-10d %s" % (name, v, v, extra))


def cmd_read(link, args):
    v = link.rd(args.addr)
    print("0x%04x = 0x%08x (%d)" % (args.addr, v, v))


def cmd_write(link, args):
    link.wr(args.addr, args.value)
    print("0x%04x <- 0x%08x" % (args.addr, args.value))


def cmd_loopback(link, args):
    data = os.urandom(args.size)
    t0 = time.time()
    link.write_data(args.offset, data)
    back = link.read_data(args.offset, args.size)
    dt = time.time() - t0
    if back != data:
        bad = next(i for i in range(len(data)) if data[i] != back[i])
        raise ProtocolError("loopback mismatch at byte %d" % bad)
    print("loopback %d bytes @0x%04x OK (%.2f s)" % (args.size, args.offset, dt))


def cmd_run(link, args):
    op = OPS[args.op]
    if args.in_hex:
        inp = bytes.fromhex(args.in_hex)
    elif args.infile:
        with open(args.infile, "rb") as f:
            inp = f.read()
    else:
        inp = os.urandom(input_size(args.level, op))
        print("random input (%d bytes): %s..." % (len(inp), inp[:32].hex()))
    need = input_size(args.level, op)
    if len(inp) != need:
        sys.exit("input is %d bytes, ML-KEM-%d %s needs %d" % (len(inp), args.level, args.op, need))
    out, cycles = link.run(args.level, op, inp)
    print("ML-KEM-%d %s: %d output bytes, %d cycles (%.3f ms @100 MHz)"
          % (args.level, args.op, len(out), cycles, cycles / 1e5))
    print("  first 32 bytes: %s" % out[:32].hex())
    if args.out:
        with open(args.out, "wb") as f:
            f.write(out)
        print("  written to %s" % args.out)


def cmd_roundtrip(link, args):
    """KeyGen -> Encaps(ek) -> Decaps(dk, c) on the FPGA; both sides must agree on K."""
    ok = True
    for level in args.levels:
        ek_len, dk_len, ct_len = SIZES[level]
        kg, c1 = link.run(level, 0, os.urandom(64))
        ek, dk = kg[:ek_len], kg[ek_len:]
        en, c2 = link.run(level, 1, ek + os.urandom(32))
        ct, k_enc = en[:ct_len], en[ct_len:]
        k_dec, c3 = link.run(level, 2, dk + ct)
        bad_ct = bytes([ct[0] ^ 1]) + ct[1:]
        k_rej, _ = link.run(level, 2, dk + bad_ct)
        good = k_enc == k_dec and k_rej != k_enc
        ok &= good
        print("ML-KEM-%-4d KeyGen %6d cyc | Encaps %6d cyc | Decaps %6d cyc | K match: %s | tampered c rejected: %s"
              % (level, c1, c2, c3, k_enc == k_dec, k_rej != k_enc))
        print("            K = %s" % k_enc.hex())
    print("ROUNDTRIP %s" % ("PASS" if ok else "FAIL"))
    return ok


def cmd_acvp(link, args):
    """Run NIST ACVP-Server ML-KEM vectors (internalProjection.json) on the FPGA.

    The expected outputs come from NIST, not from a local reference, so this is
    an independent check of FIPS 203 conformance. Supported groups:
      keyGen AFT                    d||z        -> ek||dk      must equal (ek, dk)
      encapsulation AFT             ek||m       -> c||K        must equal (c, k)
      decapsulation VAL             dk||c       -> K           must equal k
      encapsulationKeyCheck VAL     Encaps(ek) must fail with error 2 iff testPassed is false
    decapsulationKeyCheck is skipped: the core does not implement the dk hash check.
    """
    import json
    counts = {}                                       # kind -> [pass, fail]
    skipped = 0

    def record(kind, tc, good, detail=""):
        c = counts.setdefault(kind, [0, 0])
        c[0 if good else 1] += 1
        if not good or args.verbose:
            print("  tcId %-4d %-24s %s %s" % (tc, kind, "PASS" if good else "FAIL", detail))

    for path in args.files:
        with open(path) as f:
            vs = json.load(f)
        groups = vs["testGroups"]
        answers = ("ek", "k", "testPassed")
        if not all(any(a in t for a in answers) and ("d" in t or "ek" in t or "dk" in t)
                   for g in groups for t in g["tests"]):
            sys.exit("%s has no expected values: use internalProjection.json, not prompt.json" % path)
        print("%s  (vsId %s, %s)" % (path, vs.get("vsId"), vs.get("mode")))
        for g in groups:
            level = int(g["parameterSet"].split("-")[-1])
            func = g.get("function", "keyGen")
            if level not in args.levels:
                skipped += len(g["tests"])
                continue
            ek_len, dk_len, ct_len = SIZES[level]
            for t in g["tests"]:
                h = {k: bytes.fromhex(v) for k, v in t.items() if k in ("d", "z", "ek", "dk", "c", "k", "m")}
                kind = "ML-KEM-%d %s" % (level, func)
                tc = t["tcId"]
                try:
                    if func == "keyGen":
                        out, _ = link.run(level, 0, h["d"] + h["z"])
                        record(kind, tc, out == h["ek"] + h["dk"])
                    elif func == "encapsulation":
                        out, _ = link.run(level, 1, h["ek"] + h["m"])
                        record(kind, tc, out == h["c"] + h["k"])
                    elif func == "decapsulation":
                        out, _ = link.run(level, 2, h["dk"] + h["c"])
                        record(kind, tc, out == h["k"], t.get("reason", ""))
                    elif func == "encapsulationKeyCheck":
                        try:
                            link.run(level, 1, h["ek"] + os.urandom(32))
                            accepted = True
                        except ProtocolError as e:
                            if "core error 2" not in str(e):
                                raise
                            accepted = False
                        record(kind, tc, accepted == t["testPassed"], t.get("reason", ""))
                    else:
                        skipped += 1
                except ProtocolError as e:
                    record(kind, tc, False, str(e))

    print()
    total_fail = 0
    for kind in sorted(counts):
        p, fl = counts[kind]
        total_fail += fl
        print("  %-36s %3d/%-3d %s" % (kind, p, p + fl, "PASS" if fl == 0 else "FAIL"))
    print("  skipped (other levels / decapsulationKeyCheck): %d" % skipped)
    ok = bool(counts) and total_fail == 0
    print("\nACVP %s" % ("PASS" if ok else "FAIL"))
    return ok


def cmd_selftest(link, args):
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        print("  %-52s %s %s" % (name, "PASS" if cond else "FAIL", detail))
        ok &= bool(cond)

    print("Level 2: register access")
    orig = link.rd(REG["SEC_LEVEL"])
    check("SEC_LEVEL reset value %d" % DEFAULT_LEVEL, orig == DEFAULT_LEVEL, "SEC_LEVEL=%d" % orig)
    link.wr(REG["SEC_LEVEL"], 999)
    check("write/read SEC_LEVEL", link.rd(REG["SEC_LEVEL"]) == 999)
    link.wr(REG["SEC_LEVEL"], orig)
    try:
        frame = struct.pack("<BII", CMD_READ_REG, REG["ALG_ID"], 0)
        frame += struct.pack("<I", (zlib.crc32(frame) ^ 1) & 0xFFFFFFFF)
        link.ser.reset_input_buffer()
        link.ser.write(frame)
        status = link._read(9)[0]
        check("bad CRC frame rejected", status == 1)
    except ProtocolError as e:
        check("bad CRC frame rejected", False, str(e))
    check("link in sync after error", link.rd(REG["ALG_ID"]) == 1)

    print("Level 3: data buffer loopback")
    data = os.urandom(1000)
    link.write_data(OUT_BASE + 1, data)
    check("1000-byte unaligned write/read", link.read_data(OUT_BASE + 1, 1000) == data)

    print("Level 4: core identification")
    alg, ver = link.rd(REG["ALG_ID"]), link.rd(REG["VERSION"])
    check("ALG_ID == 1 (ML-KEM)", alg == 1, "ALG_ID=%d" % alg)
    check("VERSION", ver != 0, "v%d.%d.%d" % ((ver >> 16) & 0xFF, (ver >> 8) & 0xFF, ver & 0xFF))

    print("Level 5: operation start/done")
    link.wr(REG["CTRL"], 2)                          # reset pulse
    check("CTRL.reset clears STATUS", link.rd(REG["STATUS"]) == 0)
    for level in args.levels:
        try:
            seed = os.urandom(64)
            out, cycles = link.run(level, 0, seed)
            ek_len, dk_len, _ = SIZES[level]
            k = (ek_len - 32) // 384
            ek, dk = out[:ek_len], out[ek_len:]
            # dk = dk_pke || ek || H(ek) || z  (FIPS 203 Algorithm 16)
            good = (len(out) == ek_len + dk_len
                    and dk[384 * k:384 * k + ek_len] == ek
                    and dk[384 * k + ek_len:384 * k + ek_len + 32] == hashlib.sha3_256(ek).digest()
                    and dk[-32:] == seed[32:])
            check("ML-KEM-%d KeyGen: dk holds ek, H(ek), z" % level, good,
                  "(%d bytes, %d cycles)" % (len(out), cycles))
        except ProtocolError as e:
            check("ML-KEM-%d KeyGen" % level, False, str(e))
    link.wr(REG["SEC_LEVEL"], 999)
    link.wr(REG["CTRL"], 1)
    st, code = link.rd(REG["STATUS"]), link.rd(REG["ERROR_CODE"])
    check("unsupported SEC_LEVEL -> error code 1", (st & 4) and code == 1)
    link.wr(REG["CTRL"], 2)
    for level in sorted({512} | (set(SIZES) - set(args.levels))):
        link.wr(REG["SEC_LEVEL"], level)
        link.wr(REG["CTRL"], 1)
        st, code = link.rd(REG["STATUS"]), link.rd(REG["ERROR_CODE"])
        check("ML-KEM-%d not in bitstream -> error code 1" % level, (st & 4) and code == 1)
        link.wr(REG["CTRL"], 2)
    link.wr(REG["SEC_LEVEL"], orig)

    print("\nSELFTEST %s" % ("PASS" if ok else "FAIL"))
    print("Level 6 (byte-for-byte KAT): run  pqc-testkit fpga -T uart -d %s" % args.port)
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-p", "--port", required=True, help="serial port (e.g. /dev/ttyUSB1, /dev/cu.usbserial-XXXX1, COM5)")
    ap.add_argument("-b", "--baud", type=int, default=115200)
    ap.add_argument("-t", "--timeout", type=float, default=2.0, help="per-read timeout in seconds")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("info", help="read identification and status registers")
    p = sub.add_parser("read", help="read a 32-bit register")
    p.add_argument("addr", type=lambda s: int(s, 0))
    p = sub.add_parser("write", help="write a 32-bit register")
    p.add_argument("addr", type=lambda s: int(s, 0))
    p.add_argument("value", type=lambda s: int(s, 0))
    p = sub.add_parser("loopback", help="data buffer write/read loopback")
    p.add_argument("--size", type=int, default=1000)
    p.add_argument("--offset", type=lambda s: int(s, 0), default=OUT_BASE + 1)
    p = sub.add_parser("run", help="run one ML-KEM operation")
    p.add_argument("op", choices=sorted(OPS))
    p.add_argument("--level", type=int, choices=sorted(SIZES), default=DEFAULT_LEVEL)
    p.add_argument("--in", dest="infile", help="binary input file")
    p.add_argument("--in-hex", help="input as hex string")
    p.add_argument("--out", help="write output bytes to this file")
    p = sub.add_parser("roundtrip", help="KeyGen -> Encaps -> Decaps on the FPGA, check shared keys")
    p.add_argument("--levels", type=int, nargs="+", choices=sorted(SIZES), default=[DEFAULT_LEVEL])
    p = sub.add_parser("acvp", help="run NIST ACVP-Server ML-KEM vectors (internalProjection.json)")
    p.add_argument("files", nargs="+", help="ML-KEM-keyGen-FIPS203/ and/or ML-KEM-encapDecap-FIPS203/ internalProjection.json")
    p.add_argument("--levels", type=int, nargs="+", choices=sorted(SIZES), default=[DEFAULT_LEVEL])
    p.add_argument("-v", "--verbose", action="store_true", help="print every test case, not only failures")
    p = sub.add_parser("selftest", help="run verification levels 2-5")
    p.add_argument("--levels", type=int, nargs="+", choices=sorted(SIZES), default=[DEFAULT_LEVEL])

    args = ap.parse_args()
    link = Link(args.port, args.baud, args.timeout)
    try:
        handler = {"info": cmd_info, "read": cmd_read, "write": cmd_write,
                   "loopback": cmd_loopback, "run": cmd_run, "roundtrip": cmd_roundtrip,
                   "acvp": cmd_acvp, "selftest": cmd_selftest}[args.cmd]
        result = handler(link, args)
        sys.exit(0 if result is None or result else 1)
    except ProtocolError as e:
        sys.exit("ERROR: %s" % e)
    finally:
        link.close()


if __name__ == "__main__":
    main()
