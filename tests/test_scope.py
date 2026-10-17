"""cpa_toolkit/scope.py: the Teledyne LeCroy driver against a software stand-in
of a MAUI scope's remote interface, the WAVEDESC parser on hand-built blocks
(both byte orders, byte and word samples, optional blocks before the data),
the transport / dialect selection, and the GUI's trigger-position check.

    uv run python -m unittest discover -s tests -v
"""

import struct
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cpa_toolkit"))

import config as C  # noqa: E402
import scope as S  # noqa: E402


def make_block(samples, *, little=True, word=True, interval=4e-10, offset=0.0, gain=1e-5, voff=0.0,
               user_text=b"", trigtime=b"", prefix=b""):
    """A ``WF? ALL`` (or ``DESC`` when samples is None) block as a MAUI scope sends it."""
    e = "<" if little else ">"
    data = b""
    if samples is not None:
        data = np.asarray(samples, dtype=np.dtype(e + ("i2" if word else "i1"))).tobytes()
    d = bytearray(346)
    d[0:8] = b"WAVEDESC"
    d[16:26] = b"LECROY_2_3"
    struct.pack_into(e + "h", d, 32, 1 if word else 0)
    struct.pack_into(e + "h", d, 34, 1 if little else 0)
    for off, val in ((36, 346), (40, len(user_text)), (48, len(trigtime)), (52, 0), (56, 0), (60, len(data)), (64, 0)):
        struct.pack_into(e + "i", d, off, val)
    d[76:90] = b"LECROY_HDO6104"
    struct.pack_into(e + "i", d, 116, len(data) // (2 if word else 1))
    struct.pack_into(e + "f", d, 156, gain)
    struct.pack_into(e + "f", d, 160, voff)
    struct.pack_into(e + "h", d, 172, 12)
    struct.pack_into(e + "f", d, 176, interval)
    struct.pack_into(e + "d", d, 180, offset)
    return prefix + bytes(d) + user_text + trigtime + data


class FakeLeCroy:
    """The commands LeCroyScope uses, with a single-shot acquisition state.

    ``trigger()`` is the board's trig_out edge: it ends an armed SINGLE
    acquisition (TRMD? then reads STOP), as on the real instrument.
    """

    def __init__(self, samples, interval=4e-10, offset=0.0):
        self.samples, self.interval, self.offset = np.asarray(samples), interval, offset
        self.timeout, self.mode, self.log, self.acquired = 20000, "STOP", [], 0
        self.wait_timeouts = []

    def trigger(self):
        if self.mode == "SINGLE":
            self.mode = "STOP"
            self.acquired += 1

    def write(self, cmd):
        self.log.append(cmd)
        if cmd == "TRMD SINGLE":
            self.mode = "SINGLE"
        elif cmd.startswith("WAIT"):
            self.wait_timeouts.append(self.timeout)

    def query(self, cmd):
        self.log.append(cmd)
        answers = {"*IDN?": "LECROY,HDO6104,LCRY3561N15922,9.9.0", "*OPC?": "1", "TRMD?": self.mode,
                   "TRSE?": "EDGE,SR,C4,HT,OFF", "C4:TRLV?": "1.25E+00 V", "TDIV?": "2.00E-06",
                   "C2:VDIV?": "20.0E-3 V", "C2:CPL?": "D50"}
        return answers[cmd] + "\n"

    def query_binary_values(self, cmd, datatype="B", container=list):
        self.log.append(cmd)
        part = cmd.split()[-1]
        return container(make_block(self.samples if part == "ALL" else None, interval=self.interval,
                                    offset=self.offset))

    def close(self):
        pass


class TestWavedesc(unittest.TestCase):
    def test_word_little_endian(self):
        s = np.array([-32768, -1, 0, 1, 1234, 32767], dtype=np.int16)
        d = S.parse_wavedesc(make_block(s, gain=2.5e-6, voff=0.01, offset=-1e-6))
        np.testing.assert_array_equal(d["samples"], s)
        self.assertEqual(d["samples"].dtype, np.int16)
        self.assertAlmostEqual(d["vertical_gain"], 2.5e-6, places=10)
        self.assertAlmostEqual(d["vertical_offset"], 0.01, places=6)
        self.assertAlmostEqual(d["horiz_interval"], 4e-10, places=15)
        self.assertEqual(d["horiz_offset"], -1e-6)
        self.assertEqual(d["nominal_bits"], 12)
        self.assertEqual(d["wave_array_count"], s.size)
        self.assertEqual(d["instrument"], "LECROY_HDO6104")

    def test_big_endian_and_bytes(self):
        s = np.array([-128, -5, 0, 7, 127], dtype=np.int8)
        d = S.parse_wavedesc(make_block(s, little=False, word=False))
        np.testing.assert_array_equal(d["samples"], s)
        w = np.array([300, -300, 5], dtype=np.int16)
        np.testing.assert_array_equal(S.parse_wavedesc(make_block(w, little=False))["samples"], w)

    def test_blocks_before_the_data_and_a_text_prefix(self):
        s = np.arange(-50, 50, dtype=np.int16)
        d = S.parse_wavedesc(make_block(s, user_text=b"x" * 160, trigtime=b"\1" * 16, prefix=b"ALL,"))
        np.testing.assert_array_equal(d["samples"], s)

    def test_descriptor_only(self):
        self.assertIsNone(S.parse_wavedesc(make_block(None))["samples"])

    def test_rejects_garbage_and_truncation(self):
        with self.assertRaises(ValueError):
            S.parse_wavedesc(b"#900000000" + bytes(400))
        with self.assertRaises(ValueError):
            S.parse_wavedesc(make_block(np.zeros(100, np.int16))[:-10])


class TestLeCroyScope(unittest.TestCase):
    def setUp(self):
        self.wave = (np.arange(5000) % 977 - 488).astype(np.int16)
        self.dev = FakeLeCroy(self.wave)
        self.sc = S.LeCroyScope("VICP::192.0.2.100::INSTR", channel="c2", dev=self.dev)
        self.sc.configure()

    def test_configure(self):
        for cmd in ("CHDR OFF", "CFMT DEF9,WORD,BIN", "CORD LO", "WFSU SP,0,NP,0,FP,0,SN,0"):
            self.assertIn(cmd, self.dev.log)
        self.assertEqual(self.sc.channel, "C2")
        self.assertAlmostEqual(self.sc.sample_rate(), 2.5e9, delta=1e3)   # from the DESC query
        info = self.sc.info()
        self.assertEqual(info["trigger"], "EDGE,SR,C4,HT,OFF")
        self.assertEqual(info["trigger_level_v"], "1.25E+00 V")
        self.assertEqual(info["dialect"], "lecroy")
        self.assertEqual(info["nominal_bits"], 12)

    def test_capture(self):
        self.sc.arm()
        self.assertFalse(self.sc.triggered())     # armed, nothing ran yet
        self.dev.trigger()
        self.assertTrue(self.sc.triggered())
        t = self.sc.read(timeout=3.0)
        np.testing.assert_array_equal(t, self.wave)
        self.assertEqual(self.sc.record_start_s(), 0.0)
        self.assertIn("C2:WF? ALL", self.dev.log)
        self.assertGreaterEqual(self.dev.wait_timeouts[-1], 8000)  # VISA timeout > WAIT time
        self.assertEqual(self.dev.timeout, 20000)                  # ... and restored

    def test_no_trigger_times_out(self):
        self.sc.arm()
        with self.assertRaises(TimeoutError):
            self.sc.read(timeout=0.1)
        self.assertEqual(self.dev.timeout, 20000)

    def test_rearm_after_a_capture(self):
        for _ in range(3):
            self.sc.arm()
            self.dev.trigger()
            self.sc.read(timeout=1.0)
        self.assertEqual(self.dev.acquired, 3)

    def test_points(self):
        sc = S.LeCroyScope("VICP::x::INSTR", channel="C1", points=2500, dev=FakeLeCroy(self.wave))
        sc.configure()
        self.assertIn("WFSU SP,0,NP,2500,FP,0,SN,0", sc.dev.log)

    def test_bad_channel(self):
        with self.assertRaises(ValueError):
            S.LeCroyScope("VICP::x::INSTR", channel="CHANnel1", dev=FakeLeCroy(self.wave))


class TestSelection(unittest.TestCase):
    def test_resources(self):
        self.assertEqual(S.resource_for("192.168.0.174", "vicp"), "VICP::192.168.0.174::INSTR")
        self.assertEqual(S.resource_for("192.168.0.174", "vxi11"), "TCPIP0::192.168.0.174::INSTR")
        self.assertEqual(S.resource_for("10.0.0.1"), C.SCOPE_RESOURCES[C.SCOPE_TRANSPORT].format(ip="10.0.0.1"))
        with self.assertRaises(ValueError):
            S.resource_for("10.0.0.1", "gpib")

    def test_dialects(self):
        self.assertIn("lecroy", S.DIALECT_NAMES)
        self.assertIn(C.SCOPE_DIALECT, S.DIALECT_NAMES)
        with self.assertRaises(ValueError):
            S.open_scope("10.0.0.1", dialect="agilent")


class TestRecordStartCheck(unittest.TestCase):
    def setUp(self):
        import web_gui
        self.check = web_gui._record_start_check

    def _sc(self, start):
        class Stub:
            def record_start_s(self):
                return start
        return Stub()

    def test_at_the_trigger(self):
        name, ok, detail = self.check(self._sc(1.2e-9))   # sub-sample trigger interpolation
        self.assertTrue(ok, detail)

    def test_pre_trigger(self):
        ok, detail = self.check(self._sc(-1e-6))[1:]      # the scope's default: trigger mid-screen
        self.assertFalse(ok)
        self.assertIn("BEFORE", detail)

    def test_post_trigger(self):
        ok, detail = self.check(self._sc(+2e-6))[1:]
        self.assertFalse(ok)
        self.assertIn("AFTER", detail)

    def test_unknown(self):
        self.assertIsNone(self.check(object())[1])
        self.assertIsNone(self.check(self._sc(None))[1])


if __name__ == "__main__":
    unittest.main()
