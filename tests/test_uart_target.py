"""cpa_toolkit/uart_target.py against a software model of the board.

FakeBoard plays hdl/core/uart_axi_bridge.sv + the core at the byte level:
frames, CRC, the CSRs, the data buffer and Decaps (computed with the
reference mlkem768). It is built with or without command 0x05 (Run): with it
every operation must be one Run frame (no STATUS polling anywhere), without it
operations must be refused before anything is uploaded. It counts frames to
pin down the round trips per trace. scripts/test/acvp_test.py's own UART
target is checked against the same model.

    uv run python -m unittest discover -s tests -v
"""

import struct
import sys
import threading
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cpa_toolkit"))

import config as C  # noqa: E402
import mlkem768 as KEM  # noqa: E402
import uart_target as T  # noqa: E402


class FakeBoard:
    """Byte-level model of the bridge + core (serial.Serial interface)."""

    BUF = 8192
    baudrate = C.BAUD

    def __init__(self, has_run: bool):
        self.has_run = has_run
        self.timeout = 0.3
        self.csr = {0x0C: 768, 0x10: 0, 0x20: 0, 0x24: 0, 0x28: 0x1800}
        self.status = self.cycles = self.error = 0
        self.buf = bytearray(self.BUF)
        self.out_len = 0
        self.rx = bytearray()
        self.frames = []           # (cmd, addr) of every frame received

    # serial.Serial
    def reset_input_buffer(self):
        self.rx.clear()

    def write(self, frame: bytes):
        cmd, addr, n = struct.unpack("<BII", frame[:9])
        data, crc = frame[9:9 + n], struct.unpack("<I", frame[9 + n:])[0]
        self.frames.append((cmd, addr))
        if zlib.crc32(frame[:9 + n]) & 0xFFFFFFFF != crc:
            return self._reply(1)
        if cmd == T.CMD_READ_REG:
            return self._reply(0, struct.pack("<I", self._rd(addr)))
        if cmd == T.CMD_WRITE_REG:
            self._wr(addr, struct.unpack("<I", data)[0])
            return self._reply(0)
        if cmd == T.CMD_WRITE_DATA:
            self.buf[addr:addr + n] = data
            return self._reply(0)
        if cmd == T.CMD_READ_DATA:
            count = struct.unpack("<I", data)[0]
            return self._reply(0, bytes(self.buf[addr:addr + count]))
        if cmd == T.CMD_RUN and self.has_run and n == 12:
            value, off, count = struct.unpack("<III", data)
            self._wr(addr, value)
            st = self._rd(0x04)
            block = struct.pack("<III", st, self.cycles, self.error)
            out = bytes(self.buf[off:off + count]) if st == T.ST_DONE else b""
            return self._reply(0, block + out)
        return self._reply(1)      # unknown command: error status, frame consumed

    def read(self, n: int) -> bytes:
        out, self.rx = bytes(self.rx[:n]), self.rx[n:]
        return out

    def close(self):
        pass

    # bridge / core
    def _reply(self, status: int, payload: bytes = b""):
        if status:
            payload = b""
        hdr = struct.pack("<BI", status, len(payload))
        self.rx += hdr + payload + struct.pack("<I", zlib.crc32(hdr + payload) & 0xFFFFFFFF)

    def _rd(self, addr: int) -> int:
        return {0x04: self.status, 0x08: 1, 0x14: self.cycles, 0x18: 0x010000,
                0x1C: self.error, 0x2C: self.out_len}.get(addr, self.csr.get(addr, 0))

    def _wr(self, addr: int, value: int):
        if addr == 0x00:
            if value & T.CTRL_RESET:
                self.status = self.error = self.cycles = 0
            elif value & T.CTRL_START:
                self._start()
        elif addr in self.csr:
            self.csr[addr] = value

    def _start(self):
        if self.csr[0x0C] != 768 or self.csr[0x10] > 2:
            self.status, self.error = T.ST_ERROR, 1
            return
        base, out = self.csr[0x20], self.csr[0x28]
        if self.csr[0x10] == T.OP_DECAPS:
            dk = bytes(self.buf[base:base + T.DK_LEN])
            ct = bytes(self.buf[base + T.DK_LEN:base + T.DK_LEN + T.CT_LEN])
            self.buf[out:out + 32] = KEM.decaps(dk, ct)
        else:                                  # another operation: some other output
            self.buf[out:out + 32] = bytes(32)
        self.status, self.cycles, self.error, self.out_len = T.ST_DONE, 26363, 0, 32


def make_target(board: FakeBoard) -> T.Target:
    link = T.Link.__new__(T.Link)          # no real port
    link.ser, link.port, link.baud = board, "fake", C.BAUD
    link.lock, link.closed, link._serial_exc = threading.RLock(), False, (OSError,)
    return T.Target(link)


class TestTarget(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ek, cls.dk = KEM.keygen(bytes(range(32)), bytes(range(32, 64)))
        cls.cts, cls.ks = [], []
        for i in range(3):
            k, ct = KEM.encaps(ek, bytes([i]) * 32)
            cls.cts.append(ct)
            cls.ks.append(k)

    def test_probe(self):
        for has_run in (True, False):
            t = make_target(FakeBoard(has_run))
            self.assertEqual(t.probe_run(), has_run)
            info = t.info()
            self.assertEqual((info["alg_id"], info["has_run"]), (1, has_run))

    def test_decaps(self):
        board = FakeBoard(True)
        t = make_target(board)
        for i, ct in enumerate(self.cts):
            out = t.decaps(self.dk, ct, resend_dk=(i == 0))
            self.assertTrue(out.ok, out.describe())
            self.assertEqual(out.output, self.ks[i])
            self.assertEqual(out.cycles, 26363)

    def test_no_status_polling(self):
        """The operation path never reads STATUS (or CTRL.start via 0x02): only 0x05."""
        board = FakeBoard(True)
        t = make_target(board)
        t.decaps(self.dk, self.cts[0], resend_dk=True)
        self.assertNotIn((T.CMD_READ_REG, T.REG["STATUS"]), board.frames)
        self.assertNotIn((T.CMD_WRITE_REG, T.REG["CTRL"]), board.frames)
        self.assertIn((T.CMD_RUN, T.REG["CTRL"]), board.frames)

    def test_old_bitstream_refused(self):
        """Without 0x05 nothing runs, and nothing is uploaded first."""
        board = FakeBoard(False)
        t = make_target(board)
        with self.assertRaises(T.BitstreamError):
            t.decaps(self.dk, self.cts[0], resend_dk=True)
        self.assertEqual([c for c, _ in board.frames], [T.CMD_RUN])       # only the probe
        with self.assertRaises(T.BitstreamError):
            t.run()
        self.assertEqual(board.status, 0)                                 # never started

    def test_round_trips_per_trace(self):
        """c only, configuration cached: 5 ct frames + 1 Run."""
        board = FakeBoard(True)
        t = make_target(board)
        t.decaps(self.dk, self.cts[0], resend_dk=True)
        board.frames.clear()
        t.decaps(self.dk, self.cts[1], resend_dk=False)
        self.assertEqual([c for c, _ in board.frames], [T.CMD_WRITE_DATA] * 5 + [T.CMD_RUN])

    def test_config_rewritten_after_board_reset(self):
        board = FakeBoard(True)
        t = make_target(board)
        self.assertTrue(t.decaps(self.dk, self.cts[0], resend_dk=True).ok)
        board.csr[0x10] = 0                    # BTN0: OP_MODE back to its reset value
        bad = t.decaps(self.dk, self.cts[1], resend_dk=False)
        self.assertTrue(bad.ok)                # the core ran (KeyGen) without error ...
        self.assertNotEqual(bad.output, self.ks[1])   # ... only the expected K shows it
        t.forget_config()                      # what the acquisition does on a K mismatch
        good = t.decaps(self.dk, self.cts[1], resend_dk=False)
        self.assertEqual(good.output, self.ks[1])
        board.status = 0                       # a failed outcome forgets it by itself
        board.csr[0x0C] = 512
        self.assertFalse(t.decaps(self.dk, self.cts[2], resend_dk=False).ok)
        board.csr[0x0C] = 768
        self.assertEqual(t.decaps(self.dk, self.cts[2], resend_dk=False).output, self.ks[2])

    def test_parameter_error(self):
        board = FakeBoard(True)
        t = make_target(board)
        t.configure()
        board.csr[0x0C] = 512
        st, cycles, err, out = t.run()
        self.assertEqual((st, err, out), (T.ST_ERROR, 1, b""))

    def test_acvp_uart_target(self):
        """scripts/test/acvp_test.py runs its operations with 0x05 too."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("acvp_test", ROOT / "scripts" / "test" / "acvp_test.py")
        A = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(A)
        board = FakeBoard(True)
        dev = A.UartTarget.__new__(A.UartTarget)
        dev.ser, dev.op_timeout = board, 5.0
        dev.OUT_BASE = C.OUT_BASE
        case = A.Case("decaps", 1, A.OP_DECAPS, A.LEVEL, self.dk + self.cts[1], expect_out=self.ks[1])
        o = dev.run(case)
        self.assertEqual((o.status, o.err, o.out), (T.ST_DONE, 0, self.ks[1]))
        self.assertNotIn((T.CMD_READ_REG, T.REG["STATUS"]), board.frames)
        self.assertIn((T.CMD_RUN, T.REG["CTRL"]), board.frames)

    def test_status_flags(self):
        self.assertEqual(T.status_flags(T.ST_DONE), ["done"])
        self.assertEqual(T.status_flags(T.ST_BUSY | T.ST_ERROR), ["busy", "error"])
        self.assertEqual(T.OPS[T.OP_DECAPS], ("Decaps", T.DK_LEN + T.CT_LEN, T.K_LEN))

    def test_step_api(self):
        board = FakeBoard(True)
        t = make_target(board)
        t.reset()
        t.configure()
        t.load_secret_key(self.dk)
        t.load_ciphertext(self.cts[2])
        st, cycles, err, k = t.run()
        self.assertEqual((st, err), (T.ST_DONE, 0))
        self.assertEqual(k, self.ks[2])
        self.assertEqual(t.read_shared_secret(), self.ks[2])


if __name__ == "__main__":
    unittest.main()
