"""The ML-KEM-768 core on the board, as seen by the host.

Host side of ``hdl/core/uart_axi_bridge.sv`` (the same framing and register
map as ``hostlab/tracelab/protocol.py`` + ``target.py`` in git history),
trimmed to what a Decaps acquisition needs. ``SimTarget`` has the same
interface without a board, so the acquisition flow can be exercised at the
desk.

Frames are little-endian and CRC-32 protected::

    host -> FPGA   [CMD:1][ADDR:4][LEN:4][DATA:LEN][CRC32:4]
    FPGA -> host   [STATUS:1][LEN:4][DATA:LEN][CRC32:4]
"""

from __future__ import annotations

import hashlib
import struct
import time
import zlib
from dataclasses import dataclass

import config as C

CMD_READ_REG, CMD_WRITE_REG, CMD_WRITE_DATA, CMD_READ_DATA = 0x01, 0x02, 0x03, 0x04
MAX_WRITE = 256

REG = {
    "CTRL": 0x00, "STATUS": 0x04, "ALG_ID": 0x08, "SEC_LEVEL": 0x0C, "OP_MODE": 0x10,
    "CYCLE_COUNT": 0x14, "VERSION": 0x18, "ERROR_CODE": 0x1C, "DATA_IN_ADDR": 0x20,
    "DATA_IN_LEN": 0x24, "DATA_OUT_ADDR": 0x28, "DATA_OUT_LEN": 0x2C,
}
CTRL_START, CTRL_RESET = 1, 2
ST_BUSY, ST_DONE, ST_ERROR = 1, 2, 4
OP_DECAPS = 2
LEVEL = 768
DK_LEN, CT_LEN = 2400, 1088
ERRORS = {0: "none", 1: "unsupported SEC_LEVEL / OP_MODE",
          2: "encapsulation key failed the modulus check", 3: "illegal microcode"}


class ProtocolError(Exception):
    """A frame was malformed, timed out, or the bridge reported an error."""


@dataclass
class Outcome:
    status: int
    error_code: int
    cycles: int
    output: bytes
    wall_time: float
    link_error: str = ""

    @property
    def ok(self) -> bool:
        return not self.link_error and not self.status & ST_ERROR and bool(self.status & ST_DONE)

    def describe(self) -> str:
        if self.link_error:
            return "link error: %s" % self.link_error
        if self.status & ST_ERROR:
            return "ERROR_CODE %d (%s)" % (self.error_code, ERRORS.get(self.error_code, "?"))
        if not self.status & ST_DONE:
            return "STATUS 0x%x: not done" % self.status
        return "ok, %d cycles (%.1f us)" % (self.cycles, self.cycles / C.CORE_MHZ)


class Link:
    """A framed register / data-buffer link over a serial port."""

    def __init__(self, port: str, baud: int = C.BAUD, timeout: float = 2.0):
        try:
            import serial
        except ImportError:
            raise SystemExit("pyserial is missing: pip install -r requirements.txt")
        try:
            self.ser = serial.Serial(port, baud, bytesize=8, parity="N", stopbits=1, timeout=timeout)
        except Exception as e:  # serial.SerialException and OS errors
            raise SystemExit("cannot open %s: %s" % (port, e))
        self.port, self.baud = port, baud
        time.sleep(0.05)
        self.ser.reset_input_buffer()

    def close(self) -> None:
        self.ser.close()

    def command(self, cmd: int, addr: int, data: bytes = b"") -> bytes:
        frame = struct.pack("<BII", cmd, addr, len(data)) + data
        frame += struct.pack("<I", zlib.crc32(frame) & 0xFFFFFFFF)
        self.ser.reset_input_buffer()
        self.ser.write(frame)
        hdr = self._read(5)
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

    def _read(self, n: int) -> bytes:
        buf = self.ser.read(n)
        if len(buf) != n:
            raise ProtocolError("timeout: got %d of %d bytes" % (len(buf), n))
        return buf

    def rd(self, name: str) -> int:
        return struct.unpack("<I", self.command(CMD_READ_REG, REG[name]))[0]

    def wr(self, name: str, value: int) -> None:
        self.command(CMD_WRITE_REG, REG[name], struct.pack("<I", value & 0xFFFFFFFF))

    def write_data(self, offset: int, data: bytes) -> None:
        for i in range(0, len(data), MAX_WRITE):
            self.command(CMD_WRITE_DATA, offset + i, data[i : i + MAX_WRITE])

    def read_data(self, offset: int, length: int) -> bytes:
        data = self.command(CMD_READ_DATA, offset, struct.pack("<I", length))
        if len(data) != length:
            raise ProtocolError("read %d bytes, expected %d" % (len(data), length))
        return data

    def resync(self) -> bool:
        """Drain the line after an error; True if the core answers again."""
        time.sleep(0.2)  # longer than the bridge's 100 ms inter-byte timeout
        self.ser.reset_input_buffer()
        try:
            return self.rd("ALG_ID") == 1
        except ProtocolError:
            return False


class Target:
    """An ML-KEM-768 accelerator reached over a :class:`Link`."""

    simulated = False

    def __init__(self, link: Link, op_timeout: float = 5.0):
        self.link = link
        self.op_timeout = op_timeout

    @classmethod
    def open(cls, port: str, baud: int = C.BAUD) -> "Target":
        return cls(Link(port, baud))

    def close(self) -> None:
        self.link.close()

    def info(self) -> dict:
        v = self.link.rd("VERSION")
        return {
            "alg_id": self.link.rd("ALG_ID"),
            "version": "%d.%d.%d" % ((v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF),
            "port": self.link.port, "baud": self.link.baud, "core_mhz": C.CORE_MHZ,
        }

    def decaps(self, dk: bytes, ct: bytes, resend_dk: bool = True) -> Outcome:
        """Run Decaps. With ``resend_dk=False`` only ``c`` (1088 B) is sent.

        The data buffer holds ``dk || c`` (ciphertext at IN_BASE + 2400). Not
        resending ``dk`` cuts the UART time to a third, but it relies on the
        core never writing into the input region; the acquisition script
        therefore refreshes dk periodically and checks known-answer traces.
        """
        if len(dk) != DK_LEN or len(ct) != CT_LEN:
            raise ValueError("decaps takes dk=%d and c=%d bytes" % (DK_LEN, CT_LEN))
        t0 = time.time()
        link = self.link
        try:
            if resend_dk:
                link.write_data(C.IN_BASE, dk + ct)
            else:
                link.write_data(C.IN_BASE + DK_LEN, ct)
            link.wr("DATA_IN_ADDR", C.IN_BASE)
            link.wr("DATA_IN_LEN", DK_LEN + CT_LEN)
            link.wr("DATA_OUT_ADDR", C.OUT_BASE)
            link.wr("SEC_LEVEL", LEVEL)
            link.wr("OP_MODE", OP_DECAPS)
            link.wr("CTRL", CTRL_START)
            deadline = time.time() + self.op_timeout
            while True:
                st = link.rd("STATUS")
                if st & (ST_DONE | ST_ERROR):
                    break
                if time.time() > deadline:
                    raise ProtocolError("operation did not finish within %.1f s (STATUS=0x%x)"
                                        % (self.op_timeout, st))
            err = link.rd("ERROR_CODE")
            cycles = link.rd("CYCLE_COUNT")
            out = b"" if st & ST_ERROR else link.read_data(C.OUT_BASE, link.rd("DATA_OUT_LEN"))
            return Outcome(st, err, cycles, out, time.time() - t0)
        except ProtocolError as e:
            return Outcome(0, 0, 0, b"", time.time() - t0, link_error=str(e))

    def preflight(self) -> list:
        """Bottom-up link checks; returns ``[(name, ok, detail), ...]``."""
        link, res = self.link, []

        def step(name, fn):
            try:
                ok, detail = fn()
            except ProtocolError as e:
                ok, detail = False, str(e)
                link.resync()
            except Exception as e:  # noqa: BLE001 - report anything as a failure
                ok, detail = False, "%s: %s" % (type(e).__name__, e)
            res.append((name, ok, detail))

        def alg():
            v = link.rd("ALG_ID")
            return v == 1, "ALG_ID = %d (1 = ML-KEM)" % v

        def reset():
            link.wr("CTRL", CTRL_RESET)
            st = link.rd("STATUS")
            return st & (ST_BUSY | ST_ERROR) == 0, "STATUS = 0x%x after reset" % st

        def bulk():
            pattern = bytes((i * 7 + 13) & 0xFF for i in range(1024))
            link.write_data(C.IN_BASE, pattern)
            return link.read_data(C.IN_BASE, 1024) == pattern, "1024-byte loopback via the data buffer"

        step("link identity (ALG_ID)", alg)
        step("datapath reset", reset)
        step("bulk transfer (1 KB)", bulk)
        return res


class SimTarget(Target):
    """No board: same interface, fabricated output. For desk dry-runs only.

    The "shared secret" is a hash of the inputs, so the known-answer checks
    in the acquisition loop still exercise their comparison logic. It says
    nothing about the real core.
    """

    simulated = True

    def __init__(self, cycles: int = 26304):
        self._cycles = cycles

    def close(self) -> None:
        pass

    def info(self) -> dict:
        return {"alg_id": 1, "version": "sim", "port": "(simulated)", "baud": 0, "core_mhz": C.CORE_MHZ}

    def decaps(self, dk: bytes, ct: bytes, resend_dk: bool = True) -> Outcome:
        out = hashlib.sha3_256(dk + ct).digest()
        return Outcome(ST_DONE, 0, self._cycles, out, 0.0)

    def preflight(self) -> list:
        return [("simulated target", True, "no board attached")]


def list_ports() -> list:
    try:
        from serial.tools import list_ports as lp
    except ImportError:
        raise SystemExit("pyserial is missing: pip install -r requirements.txt")
    return [(p.device, p.description) for p in sorted(lp.comports(), key=lambda p: p.device)]
