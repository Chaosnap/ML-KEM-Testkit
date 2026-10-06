"""Wire protocol of the UART-to-AXI bridge on the FPGA.

This is the host side of ``hdl/core/uart_axi_bridge.sv``. Every exchange is
one request frame followed by one response frame, both little-endian and
protected by a CRC-32 over everything that precedes it::

    host -> FPGA   [CMD:1][ADDR:4][LEN:4][DATA:LEN][CRC32:4]
    FPGA -> host   [STATUS:1][LEN:4][DATA:LEN][CRC32:4]

The bridge has no flow control beyond this: a frame that is cut short is
abandoned after an inter-byte timeout of 100 ms, which :meth:`Link.resync`
relies on to recover the link after an error.

The register map is the one in ``pkg/fpga/device.go`` and
``hdl/core/pqc_axi_csr.sv``; it is fixed across transports and vendors, so
the same register names work over PCIe or AXI if the campaign is ever moved
to another board.
"""

from __future__ import annotations

import struct
import time
import zlib

# --- Commands understood by the bridge ------------------------------------
CMD_READ_REG = 0x01
CMD_WRITE_REG = 0x02
CMD_WRITE_DATA = 0x03
CMD_READ_DATA = 0x04

#: Largest payload the bridge accepts in one CMD_WRITE_DATA frame.
MAX_WRITE = 256

#: Control/status register offsets (pkg/fpga/device.go).
REG = {
    "CTRL": 0x00,
    "STATUS": 0x04,
    "ALG_ID": 0x08,
    "SEC_LEVEL": 0x0C,
    "OP_MODE": 0x10,
    "CYCLE_COUNT": 0x14,
    "VERSION": 0x18,
    "ERROR_CODE": 0x1C,
    "DATA_IN_ADDR": 0x20,
    "DATA_IN_LEN": 0x24,
    "DATA_OUT_ADDR": 0x28,
    "DATA_OUT_LEN": 0x2C,
}

# --- CTRL / STATUS bits ----------------------------------------------------
CTRL_START = 1 << 0
CTRL_RESET = 1 << 1

ST_BUSY = 1 << 0
ST_DONE = 1 << 1
ST_ERROR = 1 << 2

# --- Operations and error codes -------------------------------------------
OP_KEYGEN, OP_ENCAPS, OP_DECAPS = 0, 1, 2
OP_NAMES = {OP_KEYGEN: "KeyGen", OP_ENCAPS: "Encaps", OP_DECAPS: "Decaps"}

ERRORS = {
    0: "none",
    1: "unsupported SEC_LEVEL / OP_MODE",
    2: "encapsulation key failed the modulus check",
    3: "illegal microcode",
}

# --- Data buffer layout (scripts/gen_mlkem_ucode.py) ----------------------
IN_BASE = 0x0000
OUT_BASE = 0x0E00

# --- ML-KEM-768 (FIPS 203) sizes in bytes ---------------------------------
LEVEL = 768
EK_LEN, DK_LEN, CT_LEN, K_LEN = 1184, 2400, 1088, 32

#: Core clock on the Arty A7 build; CYCLE_COUNT counts this clock.
CORE_MHZ = 206.0


class ProtocolError(Exception):
    """A frame was malformed, timed out, or the bridge reported an error."""


class Link:
    """A framed register/memory link to the board over a serial port.

    This class only knows about frames and registers. The ML-KEM operation
    sequence lives in :class:`tracelab.target.Target`.

    :param port: serial device, e.g. ``/dev/cu.usbserial-XXXXB``.
    :param baud: must match ``BAUD_RATE`` in the board wrapper (115200).
    :param timeout: per-read timeout in seconds.
    """

    def __init__(self, port: str, baud: int = 115200, timeout: float = 2.0):
        try:
            import serial  # pyserial
        except ImportError:  # pragma: no cover - depends on the environment
            raise SystemExit(
                "pyserial is missing: run with `uv run python ...`, or "
                "install it with `python3 -m pip install pyserial`"
            )
        try:
            self.ser = serial.Serial(
                port, baud, bytesize=8, parity="N", stopbits=1, timeout=timeout
            )
        except Exception as e:  # serial.SerialException and OS errors
            raise SystemExit("cannot open %s: %s" % (port, e))
        self.port = port
        self.baud = baud
        time.sleep(0.05)
        self.ser.reset_input_buffer()

    # --- lifecycle --------------------------------------------------------
    def close(self) -> None:
        self.ser.close()

    def __enter__(self) -> "Link":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- frames -----------------------------------------------------------
    def command(self, cmd: int, addr: int, data: bytes = b"") -> bytes:
        """Send one frame and return the payload of the response."""
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

    # --- registers and buffer --------------------------------------------
    def rd(self, name: str) -> int:
        """Read a control/status register by name."""
        return struct.unpack("<I", self.command(CMD_READ_REG, REG[name]))[0]

    def wr(self, name: str, value: int) -> None:
        """Write a control/status register by name."""
        self.command(CMD_WRITE_REG, REG[name], struct.pack("<I", value & 0xFFFFFFFF))

    def write_data(self, offset: int, data: bytes) -> None:
        """Write the data buffer, splitting into frames of ``MAX_WRITE``."""
        for i in range(0, len(data), MAX_WRITE):
            self.command(CMD_WRITE_DATA, offset + i, data[i : i + MAX_WRITE])

    def read_data(self, offset: int, length: int) -> bytes:
        """Read ``length`` bytes from the data buffer."""
        data = self.command(CMD_READ_DATA, offset, struct.pack("<I", length))
        if len(data) != length:
            raise ProtocolError("read %d bytes, expected %d" % (len(data), length))
        return data

    # --- recovery ---------------------------------------------------------
    def resync(self) -> bool:
        """Drain the line after an error and report whether the link answers.

        Waits longer than the bridge's 100 ms frame timeout so that a partial
        frame is abandoned, then checks that ALG_ID still reads back as ML-KEM.
        """
        time.sleep(0.2)
        self.ser.reset_input_buffer()
        try:
            return self.rd("ALG_ID") == 1
        except ProtocolError:
            return False


def list_ports() -> list:
    """Serial ports, as ``(device, description)``, newest FTDI style first.

    An Arty A7 enumerates as two ports on one FTDI chip; the second one
    (higher trailing letter or number) is the UART, the first is JTAG.
    """
    try:
        from serial.tools import list_ports as lp
    except ImportError:  # pragma: no cover
        raise SystemExit("pyserial is missing: run with `uv run python ...`")
    return [(p.device, p.description) for p in sorted(lp.comports(), key=lambda p: p.device)]
