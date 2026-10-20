"""cpa_toolkit/sakura_lbus.py against a model of the SAKURA-X USB path.

FakeSakuraUsb plays the FT2232H channel A + AIST's factory Spartan-6
controller (0x00/0x01 register commands) + sakura_lbus_bridge.sv (FIFOs and
register map); behind the bridge sits test_uart_target's FakeBoard, so the
whole framed host protocol runs over the local bus exactly as on the board.
The bridge RTL itself is checked against AIST's controller source by
testbench/sakura/tb_lbus_bridge.sv.

    uv run python -m unittest tests.test_sakura_lbus -v
"""

import struct
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cpa_toolkit"))
sys.path.insert(0, str(ROOT / "tests"))

import sakura_lbus as S  # noqa: E402
import uart_target as T  # noqa: E402
from test_uart_target import FakeBoard  # noqa: E402

import mlkem768 as KEM  # noqa: E402


class FakeSakuraUsb:
    """serial.Serial stand-in for the board USB: lbus commands in, replies out."""

    H2F_DEPTH = 2048

    def __init__(self, board, *args, **kwargs):
        self.board = board
        self.cmd = bytearray()       # partial USB command
        self.reply = bytearray()     # USB bytes to the host
        self.h2f = bytearray()       # bridge FIFO host -> FPGA (drained into frames)
        self.f2h = bytearray()       # bridge FIFO FPGA -> host
        self.reads_during_quiet = 0
        self.timeout, self.is_open = 1.0, True

    # serial.Serial
    def write(self, data):
        self.cmd += data
        while self.cmd:
            if self.cmd[0] == 0x00 and len(self.cmd) >= 3:
                addr = struct.unpack(">H", self.cmd[1:3])[0]
                del self.cmd[:3]
                self.reply += struct.pack(">H", self._rd(addr))
            elif self.cmd[0] == 0x01 and len(self.cmd) >= 5:
                addr, value = struct.unpack(">HH", self.cmd[1:5])
                del self.cmd[:5]
                self._wr(addr, value)
            elif self.cmd[0] in (0x00, 0x01):
                break
            else:
                raise AssertionError("controller would TRAP on command 0x%02x" % self.cmd[0])
        return len(data)

    def read(self, n):
        out = bytes(self.reply[:n])
        del self.reply[:n]
        return out

    def reset_input_buffer(self):
        self.reply.clear()

    def close(self):
        self.is_open = False

    # bridge
    def _rd(self, addr):
        if addr == S.A_ID:
            return S.BRIDGE_ID
        if addr == S.A_F2H_CNT:
            self._pump()
            return len(self.f2h)
        if addr == S.A_H2F_FREE:
            return self.H2F_DEPTH - len(self.h2f)
        if addr == S.A_POP1:
            if not self.f2h:
                return 0x8000
            b = self.f2h.pop(0)
            return b
        if addr == S.A_POP2:
            b0, b1 = (self.f2h + b"\0\0")[:2]
            del self.f2h[:2]
            return (b0 << 8) | b1
        return 0

    def _wr(self, addr, value):
        if addr == S.A_PUSH1:
            self.h2f.append(value & 0xFF)
        elif addr == S.A_PUSH2:
            self.h2f += bytes([value >> 8, value & 0xFF])
        elif addr == S.A_FLUSH:
            if value & 1:
                self.f2h.clear()
            if value & 2:
                self.h2f.clear()
        assert len(self.h2f) <= self.H2F_DEPTH, "host overran the bridge FIFO"

    def _pump(self):
        """Complete frames in h2f go to the board; its reply lands in f2h."""
        while len(self.h2f) >= 9:
            n = struct.unpack("<I", self.h2f[5:9])[0]
            if len(self.h2f) < 13 + n:
                break
            frame = bytes(self.h2f[:13 + n])
            del self.h2f[:13 + n]
            self.board.write(frame)
            self.f2h += self.board.read(1 << 20)


def open_link(board):
    with mock.patch("serial.Serial", lambda *a, **k: FakeSakuraUsb(board, *a, **k)):
        link = T.Link("sakura:/dev/ttyUSB0")
    return link


class TestSakuraLbus(unittest.TestCase):
    def test_port_names(self):
        self.assertTrue(S.is_sakura_port("sakura:/dev/ttyUSB0"))
        self.assertEqual(S.device_of("sakura:/dev/ttyUSB0"), "/dev/ttyUSB0")
        self.assertEqual(S.device_of("/dev/ttyUSB1"), "/dev/ttyUSB1")

    def test_wrong_bitstream(self):
        class NoBridge(FakeSakuraUsb):
            def _rd(self, addr):
                return 0
        with mock.patch("serial.Serial", lambda *a, **k: NoBridge(FakeBoard(True))):
            with self.assertRaises(T.LinkError):
                T.Link("sakura:/dev/ttyUSB0")

    def test_decaps_over_local_bus(self):
        ek, dk = KEM.keygen(bytes(range(32)), bytes(range(32, 64)))
        board = FakeBoard(True)
        link = open_link(board)
        link.ser.quiet = 0.0
        t = T.Target(link)
        self.assertTrue(t.probe_run())
        for i in range(2):
            k, ct = KEM.encaps(ek, bytes([i]) * 32)
            out = t.decaps(dk, ct, resend_dk=(i == 0))
            self.assertTrue(out.ok, out.describe())
            self.assertEqual(out.output, k)
        link.close()

    def test_odd_lengths_and_flush(self):
        class Draining(FakeSakuraUsb):
            """The FPGA drains h2f only now and then: the host must respect 0x0004."""
            seen = bytearray()

            def _rd(self, addr):
                if addr == S.A_H2F_FREE and len(self.h2f) > 1000:
                    self.seen += self.h2f
                    self.h2f.clear()
                return super()._rd(addr)

        usb = Draining(FakeBoard(True))
        port = S.LbusPort.__new__(S.LbusPort)
        port._ser, port.timeout, port.quiet = usb, 0.05, 0.0
        port._rx, port._free, port._last_write = bytearray(), 0, 0.0
        for n in (1, 2, 3, 2047, 2048, 2049, 5000):
            usb.seen, usb.h2f = bytearray(), bytearray()
            port._free = 0
            data = bytes((i * 7 + n) & 0xFF for i in range(n))
            port.write(data)
            self.assertEqual(bytes(usb.seen + usb.h2f), data, "write of %d bytes" % n)
        usb.f2h += b"abcde"
        self.assertEqual(port.read(3), b"abc")
        port.reset_input_buffer()
        self.assertEqual(usb.f2h, b"")
        self.assertEqual(port.read(1), b"")     # timeout, nothing left


class FakePortInfo:
    """Just the pyserial ListPortInfo fields the port list reads."""

    def __init__(self, device, vid=None, pid=None, serial_number=None, location=None, description="n/a",
                 manufacturer=None, product=None, hwid="n/a"):
        self.device, self.vid, self.pid, self.serial_number, self.location = device, vid, pid, serial_number, location
        self.description, self.manufacturer, self.product, self.hwid = description, manufacturer, product, hwid


class TestPortList(unittest.TestCase):
    def test_linux_sakura_board(self):
        """What the Linux PC showed: the SAKURA-X FT2232H (ttyUSB0 / ttyUSB1), 32 phantom ttyS nodes."""
        ports = [FakePortInfo("/dev/ttyS%d" % i) for i in range(32)] + [
            FakePortInfo("/dev/ttyUSB0", 0x0403, 0x6010, "FT1A2B3C", "1-3:1.0", "USB <-> Serial Converter",
                         "FTDI", "USB <-> Serial Converter", "USB VID:PID=0403:6010"),
            FakePortInfo("/dev/ttyUSB1", 0x0403, 0x6010, "FT1A2B3C", "1-3:1.1", "USB <-> Serial Converter",
                         "FTDI", "USB <-> Serial Converter", "USB VID:PID=0403:6010"),
            FakePortInfo("/dev/ttyUSB2", 0x0403, 0x6010, "210319A", "1-4:1.1", "Digilent USB Device",
                         "Digilent", "Digilent USB Device", "USB VID:PID=0403:6010"),
        ]
        with mock.patch("serial.tools.list_ports.comports", lambda: ports):
            rows = T.list_ports()
        devs = [r[0] for r in rows]
        self.assertFalse(any("/ttyS" in d for d in devs), "phantom ttyS nodes must be hidden")
        rec = [r[0] for r in rows if r[3]]
        self.assertIn("sakura:/dev/ttyUSB0", rec)
        hint = {r[0]: r[2] for r in rows}
        self.assertIn("channel B", hint["/dev/ttyUSB1"])
        self.assertNotIn("recommended", hint["/dev/ttyUSB1"])
        self.assertIn("raw", hint["/dev/ttyUSB0"])
        self.assertEqual(hint["/dev/ttyUSB2"], "board UART (recommended)")   # an Arty's UART is still recognised

    def test_real_ttyS_with_hardware_is_kept(self):
        ports = [FakePortInfo("/dev/ttyS0", description="ttyS0", hwid="PNP0501")]
        with mock.patch("serial.tools.list_ports.comports", lambda: ports):
            self.assertEqual([r[0] for r in T.list_ports()], ["/dev/ttyS0"])


if __name__ == "__main__":
    unittest.main()
