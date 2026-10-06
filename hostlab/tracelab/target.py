"""The ML-KEM-768 core on the board, as seen by the host.

:class:`Target` wraps a :class:`~tracelab.protocol.Link` and turns it into
the operation that a measurement campaign repeats thousands of times: write
an input into the data buffer, start the core, wait for it to finish, read
the result back. :meth:`Target.run` returns both the output and the cycle
count, so a campaign can check that every run behaved identically even when
no analogue measurement is taken.

Before a long campaign, :meth:`Target.preflight` walks the link bottom-up
(identity, reset, register read/write, CRC rejection, bulk transfer, buffer
addressing) so that a failure is attributed to the right layer instead of
showing up as unexplained noise hours later.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from . import protocol as P


@dataclass
class Outcome:
    """The result of one operation on the board."""

    status: int
    error_code: int
    cycles: int
    output: bytes
    wall_time: float
    link_error: str = ""

    @property
    def ok(self) -> bool:
        return (
            not self.link_error
            and not self.status & P.ST_ERROR
            and bool(self.status & P.ST_DONE)
        )

    def describe(self) -> str:
        if self.link_error:
            return "link error: %s" % self.link_error
        if self.status & P.ST_ERROR:
            return "ERROR_CODE %d (%s)" % (
                self.error_code,
                P.ERRORS.get(self.error_code, "?"),
            )
        if not self.status & P.ST_DONE:
            return "STATUS 0x%x: not done" % self.status
        return "ok, %d cycles (%.1f us)" % (self.cycles, self.cycles / P.CORE_MHZ)


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


class Target:
    """An ML-KEM-768 accelerator reached over a :class:`Link`."""

    def __init__(self, link: P.Link, op_timeout: float = 5.0):
        self.link = link
        self.op_timeout = op_timeout

    @classmethod
    def open(
        cls,
        port: str,
        baud: int = 115200,
        timeout: float = 2.0,
        op_timeout: float = 5.0,
    ) -> "Target":
        return cls(P.Link(port, baud, timeout), op_timeout)

    def close(self) -> None:
        self.link.close()

    def __enter__(self) -> "Target":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- one operation ----------------------------------------------------
    def run(self, op: int, data: bytes, level: int = P.LEVEL) -> Outcome:
        """Run one operation and return its :class:`Outcome`.

        The sequence is the same one ``pkg/fpga`` and ``scripts/acvp_test.py``
        use, so a campaign and a functional test exercise the core identically.
        """
        t0 = time.time()
        link = self.link
        try:
            link.write_data(P.IN_BASE, data)
            link.wr("DATA_IN_ADDR", P.IN_BASE)
            link.wr("DATA_IN_LEN", len(data))
            link.wr("DATA_OUT_ADDR", P.OUT_BASE)
            link.wr("SEC_LEVEL", level)
            link.wr("OP_MODE", op)
            link.wr("CTRL", P.CTRL_START)

            deadline = time.time() + self.op_timeout
            while True:
                st = link.rd("STATUS")
                if st & (P.ST_DONE | P.ST_ERROR):
                    break
                if time.time() > deadline:
                    raise P.ProtocolError(
                        "operation did not finish within %.1f s (STATUS=0x%x)"
                        % (self.op_timeout, st)
                    )

            err = link.rd("ERROR_CODE")
            cycles = link.rd("CYCLE_COUNT")
            out = b""
            if not st & P.ST_ERROR:
                out = link.read_data(P.OUT_BASE, link.rd("DATA_OUT_LEN"))
            if level != P.LEVEL:
                link.wr("SEC_LEVEL", P.LEVEL)
            return Outcome(st, err, cycles, out, time.time() - t0)
        except P.ProtocolError as e:
            return Outcome(0, 0, 0, b"", time.time() - t0, link_error=str(e))

    def decaps(self, dk: bytes, ct: bytes) -> Outcome:
        """Decapsulate ``ct`` with ``dk``; the output is the 32-byte key K."""
        if len(dk) != P.DK_LEN or len(ct) != P.CT_LEN:
            raise ValueError(
                "ML-KEM-768 decaps takes dk=%d and c=%d bytes, got %d and %d"
                % (P.DK_LEN, P.CT_LEN, len(dk), len(ct))
            )
        return self.run(P.OP_DECAPS, dk + ct)

    def keygen(self, seed: bytes) -> Outcome:
        """Generate a key pair from a 64-byte seed d||z; output is ek||dk."""
        if len(seed) != 64:
            raise ValueError("keygen takes a 64-byte seed d||z, got %d" % len(seed))
        return self.run(P.OP_KEYGEN, seed)

    def encaps(self, ek: bytes, m: bytes) -> Outcome:
        """Encapsulate with ``ek`` and message ``m``; output is c||K."""
        if len(ek) != P.EK_LEN or len(m) != 32:
            raise ValueError("encaps takes ek=%d bytes and m=32 bytes" % P.EK_LEN)
        return self.run(P.OP_ENCAPS, ek + m)

    # --- bring-up ---------------------------------------------------------
    def info(self) -> dict:
        """Identity registers, for the campaign metadata."""
        v = self.link.rd("VERSION")
        return {
            "alg_id": self.link.rd("ALG_ID"),
            "version": "%d.%d.%d" % ((v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF),
            "sec_level": self.link.rd("SEC_LEVEL"),
            "port": self.link.port,
            "baud": self.link.baud,
            "core_mhz": P.CORE_MHZ,
        }

    def preflight(self) -> list:
        """Bottom-up link checks; returns a list of :class:`Check`."""
        import struct
        import zlib

        link = self.link
        checks: list = []

        def step(name, fn):
            try:
                ok, detail = fn()
            except P.ProtocolError as e:
                ok, detail = False, str(e)
                link.resync()
            except Exception as e:  # noqa: BLE001 - report anything as a failure
                ok, detail = False, "%s: %s" % (type(e).__name__, e)
            checks.append(Check(name, ok, detail))
            return ok

        def alg():
            v = link.rd("ALG_ID")
            return v == 1, "ALG_ID = %d (1 = ML-KEM)" % v

        def version():
            v = link.rd("VERSION")
            return v != 0, "VERSION = %d.%d.%d" % (
                (v >> 16) & 0xFF,
                (v >> 8) & 0xFF,
                v & 0xFF,
            )

        def reset():
            link.wr("CTRL", P.CTRL_RESET)
            st = link.rd("STATUS")
            return st & (P.ST_BUSY | P.ST_ERROR) == 0, "STATUS = 0x%x after reset" % st

        def regs():
            link.wr("SEC_LEVEL", 768)
            v = link.rd("SEC_LEVEL")
            return v == 768, "SEC_LEVEL wrote 768, read %d" % v

        def bad_crc():
            # A frame with a deliberately wrong CRC must be rejected, not acted
            # on. The bridge stays silent, so the read times out; the link must
            # then come back with resync().
            frame = struct.pack("<BII", P.CMD_READ_REG, P.REG["ALG_ID"], 0)
            frame += struct.pack("<I", (zlib.crc32(frame) ^ 0xFFFF) & 0xFFFFFFFF)
            link.ser.reset_input_buffer()
            link.ser.write(frame)
            link.ser.read(5)
            return link.resync(), "bad CRC rejected, link recovered"

        def bulk():
            pattern = bytes((i * 7 + 13) & 0xFF for i in range(1024))
            link.write_data(P.IN_BASE, pattern)
            back = link.read_data(P.IN_BASE, len(pattern))
            return back == pattern, "1024-byte loopback through the data buffer"

        def addressing():
            # A short write at a non-zero offset must not disturb its
            # neighbours: catches off-by-one addressing in the bridge.
            base = P.IN_BASE + 0x100
            link.write_data(base, b"\xa5" * 8)
            link.write_data(base + 8, b"\x5a" * 8)
            back = link.read_data(base, 16)
            return back == b"\xa5" * 8 + b"\x5a" * 8, "offset writes land where expected"

        step("link identity (ALG_ID)", alg)
        step("core version", version)
        step("datapath reset", reset)
        step("register write/read", regs)
        step("CRC rejection + resync", bad_crc)
        step("bulk transfer (1 KB)", bulk)
        step("buffer addressing", addressing)
        return checks
