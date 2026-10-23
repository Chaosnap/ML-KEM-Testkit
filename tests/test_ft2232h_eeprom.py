"""scripts/board/ft2232h_eeprom.py against a model of the SAKURA-X board's EEPROM.

The model is a 93C46 (128 bytes) on an FT2232H, as on the Linux PC (chip byte 0x46,
Channel A UART + VCP, Channel B UART, strings "FTDI" / "USB <-> Serial Converter" /
"FT9A4QV0" stored at 0x1A with FT_PROG-style pointers 0x9A..). Reads past 128 bytes
wrap around, which is what makes plain pyftdi fail. Needs pyftdi (skipped otherwise):

    ~/ftdi-venv/bin/python -m unittest tests.test_ft2232h_eeprom -v
"""
import importlib.util
import io
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
HAVE_PYFTDI = importlib.util.find_spec("pyftdi") is not None


def crc(data):
    c = 0xAAAA
    for i in range(0, len(data) - 2, 2):
        c ^= data[i] | (data[i + 1] << 8)
        c = ((c << 1) | (c >> 15)) & 0xFFFF
    return c


def board_image():
    img = bytearray(128)
    img[0], img[1] = 0x08, 0x00                     # A: UART + VCP, B: UART + D2XX
    img[2:8] = struct.pack("<HHH", 0x0403, 0x6010, 0x0700)
    img[8], img[9], img[10] = 0x80, 250, 0x08       # bus powered, 500 mA, has serial
    pos, table = 0x1A, 0x0E
    for s in ("FTDI", "USB <-> Serial Converter", "FT9A4QV0"):
        u = s.encode("utf-16le")
        img[pos:pos + 2 + len(u)] = bytes([2 + len(u), 3]) + u
        img[table], img[table + 1] = 0x80 | pos, 2 + len(u)
        pos, table = pos + 2 + len(u), table + 2
    img[0x18] = 0x46                                # 93C46
    img[126:128] = struct.pack("<H", crc(img))
    return img


class Fake93C46:
    def __init__(self, img):
        self.img, self.is_connected, self.device_version = bytearray(img), False, 0x0700
        self.is_eeprom_internal, self.max_eeprom_size, self.writes = False, 256, 0

    def open_from_url(self, url):
        self.is_connected = True

    def close(self, *a, **k):
        self.is_connected = False

    def read_eeprom(self, addr=0, length=None, eeprom_size=None):
        return bytes(self.img[i % 128] for i in range(eeprom_size or 256))   # a 93C46 wraps

    def calc_eeprom_checksum(self, data):
        from pyftdi.ftdi import Ftdi
        return Ftdi.calc_eeprom_checksum(self, data)       # pyftdi's own checksum code

    def overwrite_eeprom(self, data, dry_run=True):
        if not dry_run:
            assert len(data) == 128, "wrote %d bytes into a 93C46" % len(data)
            self.img[:] = data
            self.writes += 1


@unittest.skipUnless(HAVE_PYFTDI, "pyftdi not installed")
class TestFt2232hEeprom(unittest.TestCase):
    def setUp(self):
        self.dev = Fake93C46(board_image())
        self.cwd = os.getcwd()
        self.tmp = tempfile.mkdtemp()
        os.chdir(self.tmp)                          # backups land here
        sys.path.insert(0, str(ROOT / "scripts" / "board"))
        import ft2232h_eeprom
        self.mod = ft2232h_eeprom

    def tearDown(self):
        os.chdir(self.cwd)

    def ftdi_class(self):
        """Stands in for pyftdi.ftdi.Ftdi: constructing it yields the model; class data stays real."""
        from pyftdi.ftdi import Ftdi
        dev = self.dev
        return type("FakeFtdiClass", (), {"DEVICE_NAMES": Ftdi.DEVICE_NAMES, "__new__": lambda cls: dev})

    def run_script(self, *args):
        out = io.StringIO()
        with mock.patch("pyftdi.eeprom.Ftdi", self.ftdi_class()), mock.patch("sys.stdout", out), \
                mock.patch("sys.argv", ["ft2232h_eeprom.py", *args]):
            code = self.mod.main()
        return code, out.getvalue()

    def test_plain_pyftdi_fails_like_on_the_board(self):
        """The model reproduces the user's error: default open + set_property -> IndexError."""
        from pyftdi.eeprom import FtdiEeprom
        with mock.patch("pyftdi.eeprom.Ftdi", self.ftdi_class()):
            ee = FtdiEeprom()
            ee.open("ftdi://ftdi:2232h/1")
            self.assertEqual(ee._config["product"], "")          # strings decode empty, as on the board
            self.assertEqual(ee._config["channel_a_type"], "UART")
            with self.assertRaises(IndexError):
                ee.set_property("channel_a_type", "FIFO")

    def test_read_only_reports_wrong_modes_and_backs_up(self):
        code, out = self.run_script()
        self.assertEqual(code, 0)
        self.assertIn("'USB <-> Serial Converter'", out)        # decoded with the real size
        self.assertIn("'FT9A4QV0'", out)
        self.assertIn("WRONG", out)
        self.assertEqual(self.dev.writes, 0)
        backups = list(Path(self.tmp).glob("ft2232h_*.bin"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), bytes(board_image()))

    def test_write_sets_fifo_and_keeps_everything_else(self):
        code, out = self.run_script("--write", "--yes")
        self.assertEqual(code, 0, out)
        img, old = self.dev.img, board_image()
        self.assertEqual(img[0], 0x09)                          # FIFO, driver bit (VCP) unchanged
        self.assertEqual(img[1], 0x00)
        self.assertEqual([i for i in range(126) if img[i] != old[i]], [0])   # only byte 0 (+ checksum)
        self.assertTrue(self.mod.ftdi_crc_ok(bytes(img)))
        self.assertIn("Done", out)

    def test_rename_keeps_serial_and_valid_checksum(self):
        code, out = self.run_script("--write", "--rename", "--yes")
        self.assertEqual(code, 0, out)
        from pyftdi.eeprom import FtdiEeprom
        with mock.patch("pyftdi.eeprom.Ftdi", self.ftdi_class()):
            ee = FtdiEeprom()
            ee.open("ftdi://ftdi:2232h/1", size=128)
            c = ee._config
        self.assertEqual((c["product"], c["serial"], c["manufacturer"]), ("SAKURA-X Shell", "FT9A4QV0", "FTDI"))
        self.assertEqual((c["channel_a_type"], c["channel_b_type"]), ("FIFO", "UART"))
        self.assertTrue(self.mod.ftdi_crc_ok(bytes(self.dev.img)))
        # the chip resolves string pointers modulo its size: every pointer must land inside 128 bytes
        for t in (0x0E, 0x10, 0x12):
            p, n = self.dev.img[t] & 0x7F, self.dev.img[t + 1]
            self.assertLessEqual(p + n, 126)

    def test_second_write_is_a_no_op(self):
        self.run_script("--write", "--yes")
        code, out = self.run_script("--write", "--yes")
        self.assertIn("Nothing to change", out)
        self.assertEqual(self.dev.writes, 1)

    def test_restore(self):
        self.run_script("--write", "--yes")
        backup = Path(self.tmp) / "orig.bin"
        backup.write_bytes(bytes(board_image()))
        code, out = self.run_script("--restore", str(backup), "--yes")
        self.assertEqual(code, 0, out)
        self.assertEqual(bytes(self.dev.img), bytes(board_image()))

    def test_restore_refuses_a_bad_image(self):
        bad = Path(self.tmp) / "bad.bin"
        bad.write_bytes(bytes(128))
        with self.assertRaises(SystemExit):
            self.run_script("--restore", str(bad), "--yes")
        self.assertEqual(self.dev.writes, 0)


if __name__ == "__main__":
    unittest.main()
