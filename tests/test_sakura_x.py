"""cpa_toolkit/sakura_x.py: telling the Spartan-6 controllers apart before any Kintex access.

Byte-level models of what can sit behind the FT2232H's Channel A:

* ShellCtrl  -- third_party/sakura_x_shell/sakura-x-shell-ctrl/command_control.v: 6-byte frames,
  1-byte ACK (0x00 ok, 0x01 bad pre/postamble, 0x02 unknown type), reads then wait for the
  Kintex; without a Kintex answer the controller stays inside the transaction ("stuck") and
  ignores everything until a Channel-B RTS reset (ctrl_top.v: reset_n & i_ftdi_rts_n).
* AistCtrl   -- AIST's factory sasebo_giii_ctrl (ctrl_lbus.v): 00 AH AL -> 2-byte reply,
  01 AH AL DH DL -> write, any other command byte -> TRAP for good (RTS is not wired).
* nothing at all (Spartan unconfigured / Channel A not in FIFO mode).

    uv run python -m unittest tests.test_sakura_x -v
"""

import struct
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cpa_toolkit"))

import sakura_x as SH  # noqa: E402
import uart_target as T  # noqa: E402


class ShellCtrl:
    def __init__(self, kintex=True, rts_wired=True, stuck=False):
        self.kintex, self.rts_wired, self.stuck = kintex, rts_wired, stuck
        self.buf, self.out = bytearray(), bytearray()
        self.regs = {SH.IDENT: SH.MAGIC}

    def rts_reset(self):
        if self.rts_wired:
            self.buf.clear()
            self.stuck = False

    def feed(self, data):
        if self.stuck:
            return
        self.buf += data
        while len(self.buf) >= 6 and not self.stuck:
            f = int.from_bytes(self.buf[:6], "big")
            del self.buf[:6]
            pre, typ, attr, addr, post = f >> 44, (f >> 40) & 15, (f >> 36) & 15, (f >> 4) & 0xFFFFFFFF, f & 15
            if pre != 8 or post != 1:
                self.out.append(0x01)
            elif typ not in (1, 2) and not (typ == 0 and attr == 0):
                self.out.append(0x02)
            else:
                self.out.append(0x00)
                if typ == 1:
                    if not self.kintex:
                        self.stuck = True          # waits for addr_ready forever
                        return
                    for w in range(attr + 1):
                        self.out += struct.pack(">I", self.regs.get(addr + 4 * w, 0))


class AistCtrl:
    def __init__(self, trapped=False):
        self.trap, self.buf, self.out = trapped, bytearray(), bytearray()

    def rts_reset(self):
        pass                                       # Channel B RTS is not connected in this design

    def feed(self, data):
        if self.trap:
            return
        self.buf += data
        while self.buf and not self.trap:
            cmd = self.buf[0]
            if cmd == 0x00:
                if len(self.buf) < 3:
                    return
                del self.buf[:3]
                self.out += b"\x47\x02"            # LBUS_IF ID register value
            elif cmd == 0x01:
                if len(self.buf) < 5:
                    return
                del self.buf[:5]
            else:
                self.trap = True


class Dead:
    out = bytearray()

    def rts_reset(self):
        pass

    def feed(self, data):
        pass


class ChannelA:
    def __init__(self, ctrl):
        self.ctrl, self.timeout = ctrl, 0.5

    def write(self, data):
        self.ctrl.feed(bytes(data))
        return len(data)

    def read(self, n):
        out = bytes(self.ctrl.out[:n])
        del self.ctrl.out[:n]
        return out

    def reset_input_buffer(self):
        self.ctrl.out.clear()

    def close(self):
        pass


class ChannelB:
    def __init__(self, ctrl):
        self.ctrl, self._rts = ctrl, False

    @property
    def rts(self):
        return self._rts

    @rts.setter
    def rts(self, value):
        if self._rts and not value:                # RTS_N released after being held low
            self.ctrl.rts_reset()
        self._rts = value

    def close(self):
        pass


def make_link(ctrl):
    link = SH.ShellLink.__new__(SH.ShellLink)
    link.port, link.baud, link.lock, link.closed = "sakura-x:/dev/ttyUSB0", None, threading.RLock(), False
    link.ser, link.reset_ser = ChannelA(ctrl), ChannelB(ctrl)
    link._serial_exc, link.quiet_ms = (OSError,), 20.0
    link.data_port, link.reset_port = "/dev/ttyUSB0", "/dev/ttyUSB1"
    return link


@mock.patch("time.sleep", lambda s: None)
class TestControllerCheck(unittest.TestCase):
    def test_shell_with_kintex(self):
        link = make_link(ShellCtrl())
        self.assertEqual(link.check_controller(), "shell")
        self.assertEqual(link.identify(), SH.MAGIC)          # frames are aligned afterwards

    def test_shell_left_stuck_by_an_earlier_session_is_recovered(self):
        """The case a plain probe cannot handle: an earlier read waited for the Kintex forever."""
        ctrl = ShellCtrl(stuck=True)
        link = make_link(ctrl)
        with self.assertRaises(T.ProtocolError):
            link.probe_controller()                          # what Connect used to do
        self.assertEqual(link.check_controller(), "shell")   # what it does now
        self.assertEqual(link.identify(), SH.MAGIC)

    def test_shell_without_a_kintex_answer_is_reported_at_the_kintex_stage(self):
        link = make_link(ShellCtrl(kintex=False))
        self.assertEqual(link.check_controller(), "shell")
        with self.assertRaises(T.ProtocolError) as cm:
            link.identify()
        self.assertIn("Kintex", str(cm.exception))

    def test_factory_controller_is_named(self):
        reports = []
        link = make_link(AistCtrl())
        with self.assertRaises(T.ProtocolError) as cm:
            link.check_controller(lambda *r: reports.append(r))
        self.assertIn("FACTORY", str(cm.exception))
        self.assertIn("47 02", str(cm.exception))
        self.assertEqual([r[:2] for r in reports], [("Channel B reset", True), ("Spartan controller", False)])

    def test_factory_controller_already_trapped_or_nothing_at_all(self):
        for ctrl in (AistCtrl(trapped=True), Dead()):
            with self.assertRaises(T.ProtocolError) as cm:
                make_link(ctrl).check_controller()
            self.assertIn("No Spartan-6 controller", str(cm.exception))
            self.assertIn("LED17", str(cm.exception))

    def test_shell_whose_rts_reset_does_not_arrive(self):
        ctrl = ShellCtrl(rts_wired=False)
        link = make_link(ctrl)
        self.assertEqual(link.check_controller(), "shell-no-rts")
        self.assertEqual(link.identify(), SH.MAGIC)          # the frame boundary was restored in-band

    def test_frames_match_the_template_layout(self):
        h = SH.ShellLink._header(1, 0x104, 1)
        self.assertEqual(h.hex(), "810000001041")            # pre 8 | type 1 | words-1 0 | 0x00000104 | post 1


@mock.patch("time.sleep", lambda s: None)
class TestConnectFlow(unittest.TestCase):
    def run_connect(self, ctrl):
        import connection
        checks, published = [], []

        def publish(obj=None, check=None):
            if check:
                checks.append((check["name"], check["ok"]))
            elif obj is not None:
                published.append(obj)

        link = make_link(ctrl)
        with mock.patch("sakura_x.ShellLink", lambda port, report=None: link):
            try:
                connection.open_board({"port": "sakura-x:/dev/ttyUSB0", "op_timeout": 5}, publish)
                error = None
            except T.ProtocolError as e:
                error = str(e)
        return checks, error

    def test_factory_controller_stops_before_the_kintex(self):
        checks, error = self.run_connect(AistCtrl())
        self.assertIn("FACTORY", error)
        self.assertEqual(checks, [("Channel B reset", True), ("Spartan controller", False)])

    def test_stuck_shell_now_reaches_the_kintex_stage(self):
        checks, error = self.run_connect(ShellCtrl(kintex=False, stuck=True))
        self.assertIn(("Spartan controller", True), checks)
        self.assertIn(("Kintex shell identity", False), checks)


if __name__ == "__main__":
    unittest.main()
