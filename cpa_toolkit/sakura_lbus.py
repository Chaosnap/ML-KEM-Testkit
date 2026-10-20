"""Host byte link over the SAKURA-X (SASEBO-GIII) board USB.

The board's USB port (CN15) is an FT2232H whose channel A (245-FIFO mode,
``sasebo_giii/sasebo_giii.xml``) talks to the Spartan-6 control FPGA. With
AIST's factory control bitstream (``sasebo_giii_ctrl``) every USB access is a
16-bit register access on the inter-FPGA local bus::

    0x01 AH AL DH DL    write DH:DL to address AH:AL   (no reply)
    0x00 AH AL          read address AH:AL             (reply: DH DL)

``hdl/xilinx/sakura_x/sakura_lbus_bridge.sv`` in the Kintex-7 maps a byte
stream onto a few of those registers. :class:`LbusPort` does the host half and
looks like a ``serial.Serial`` to its callers (``write``, ``read``,
``reset_input_buffer``, ``close``, ``timeout``, ``baudrate``), so the framed
host protocol of ``uart_target`` / ``scripts/test/acvp_test.py`` runs over it
unchanged. A port name ``sakura:<device>`` selects it, e.g.
``sakura:/dev/ttyUSB0`` (Linux: channel A is the first ttyUSB of the board).

Side-channel note: replies are polled. After every write the first poll is
held back by ``quiet`` seconds (default 2 ms; a Decaps takes ~128 us), so the
USB / Spartan-6 / local-bus traffic of the polling does not overlap the
operation the scope is recording.
"""

from __future__ import annotations

import struct
import time

PREFIX = "sakura:"

# Bridge registers (sakura_lbus_bridge.sv).
A_F2H_CNT = 0x0002
A_H2F_FREE = 0x0004
A_PUSH1 = 0x0010
A_PUSH2 = 0x0012
A_POP1 = 0x0020
A_POP2 = 0x0022
A_FLUSH = 0x0030
A_ID = 0xFFFC
BRIDGE_ID = 0x4D4B

MAX_POP_CMDS = 256          # pops per USB round trip (replies stay small)
POLL_INTERVAL = 0.0005


class LbusError(OSError):
    """The bridge did not answer (wrong port, Spartan-6 or Kintex-7 not configured)."""


def is_sakura_port(port: str) -> bool:
    return isinstance(port, str) and port.startswith(PREFIX)


def device_of(port: str) -> str:
    """``sakura:/dev/ttyUSB0`` -> ``/dev/ttyUSB0`` (other names unchanged)."""
    return port[len(PREFIX):] if is_sakura_port(port) else port


def _rd_cmd(addr: int) -> bytes:
    return struct.pack(">BH", 0x00, addr)


def _wr_cmd(addr: int, value: int) -> bytes:
    return struct.pack(">BHH", 0x01, addr, value & 0xFFFF)


class LbusPort:
    """A ``serial.Serial``-like byte stream through the SAKURA-X local bus."""

    def __init__(self, device: str, baudrate: int = 115200, timeout: float | None = 1.0,
                 write_timeout: float | None = 2.0, exclusive: bool = True, quiet: float = 0.002,
                 check_id: bool = True):
        import serial
        kw = {"exclusive": True} if exclusive else {}
        # The baud rate means nothing in FIFO mode; it only feeds the callers'
        # timeout arithmetic (a 115200-baud estimate is far slower than this link).
        self._ser = serial.Serial(device_of(device), 115200, timeout=1.0,
                                  write_timeout=write_timeout, **kw)
        try:   # Linux ftdi_sio: latency timer 1 ms instead of 16 ms per poll
            self._ser.set_low_latency_mode(True)
        except Exception:
            pass
        self.port = device
        self.baudrate = baudrate
        self.timeout = timeout
        self.quiet = quiet
        self._rx = bytearray()
        self._free = 0
        self._last_write = 0.0
        self._ser.reset_input_buffer()
        if check_id:
            ident = self.reg_read(A_ID)
            if ident != BRIDGE_ID:
                self._ser.close()
                raise LbusError(
                    "no ML-KEM local-bus bridge on %s (ID 0x%04x, expected 0x%04x): is the "
                    "Kintex-7 programmed with the SAKURA-X bitstream, and is this the board's "
                    "channel A (first port)?" % (device_of(device), ident, BRIDGE_ID))

    # --- local-bus register access ------------------------------------------
    def _transact(self, cmds: bytes, nreply: int) -> bytes:
        self._ser.write(cmds)
        if not nreply:
            return b""
        buf = self._ser.read(nreply)
        if len(buf) != nreply:
            raise LbusError("local bus: got %d of %d reply bytes (Spartan-6 not running its "
                            "factory bitstream, or wrong port?)" % (len(buf), nreply))
        return buf

    def reg_read(self, addr: int) -> int:
        return struct.unpack(">H", self._transact(_rd_cmd(addr), 2))[0]

    def reg_write(self, addr: int, value: int) -> None:
        self._transact(_wr_cmd(addr, value), 0)

    # --- serial.Serial interface --------------------------------------------
    @property
    def in_waiting(self) -> int:
        return len(self._rx) + self.reg_read(A_F2H_CNT)

    def write(self, data: bytes) -> int:
        data = bytes(data)
        i = 0
        while i < len(data):
            if self._free < 2:
                self._free = self.reg_read(A_H2F_FREE)
                if self._free < 2:
                    time.sleep(POLL_INTERVAL)
                    continue
            n = min(len(data) - i, self._free & ~1 if len(data) - i > 1 else 1, 1024)
            chunk = data[i:i + n]
            cmds = bytearray()
            for j in range(0, len(chunk) - 1, 2):
                cmds += _wr_cmd(A_PUSH2, (chunk[j] << 8) | chunk[j + 1])
            if len(chunk) % 2:
                cmds += _wr_cmd(A_PUSH1, chunk[-1])
            self._transact(bytes(cmds), 0)
            self._free -= n
            i += n
        self._last_write = time.monotonic()
        return len(data)

    def read(self, n: int = 1) -> bytes:
        deadline = None if self.timeout is None else time.monotonic() + self.timeout
        hold = self._last_write + self.quiet - time.monotonic()
        if len(self._rx) < n and hold > 0:
            time.sleep(hold)
        while len(self._rx) < n:
            cnt = self.reg_read(A_F2H_CNT)
            if cnt:
                k = min(cnt, 2 * MAX_POP_CMDS)
                cmds = _rd_cmd(A_POP2) * (k // 2) + _rd_cmd(A_POP1) * (k % 2)
                reply = self._transact(cmds, 2 * (k // 2 + k % 2))
                for j in range(k // 2):
                    self._rx += reply[2 * j:2 * j + 2]
                if k % 2:
                    self._rx.append(reply[-1])
                continue
            if deadline is not None and time.monotonic() >= deadline:
                break
            time.sleep(POLL_INTERVAL)
        out = bytes(self._rx[:n])
        del self._rx[:n]
        return out

    def reset_input_buffer(self) -> None:
        self.reg_write(A_FLUSH, 0x0001)
        self._rx.clear()

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self._ser.close()

    @property
    def is_open(self) -> bool:
        return self._ser.is_open
