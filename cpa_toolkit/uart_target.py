"""The ML-KEM-768 core on the board, as seen by the host.

Host side of ``hdl/core/uart_axi_bridge.sv``; the protocol is described in
``docs/UART_PROTOCOL.md``. Frames are little-endian and CRC-32 protected::

    host -> FPGA   [CMD:1][ADDR:4][LEN:4][DATA:LEN][CRC32:4]
    FPGA -> host   [STATUS:1][LEN:4][DATA:LEN][CRC32:4]

:class:`Target` offers one call per step of an acquisition, in the spirit of
the SimpleSerial commands of Kamucheka et al. (x / m / s / c / e / a):
:meth:`Target.reset`, :meth:`Target.configure`, :meth:`Target.load_secret_key`,
:meth:`Target.load_ciphertext`, :meth:`Target.run` (start, wait for the
FPGA's completion notice, read the result) and :meth:`Target.read_shared_secret`.
:meth:`Target.run` is one 0x05 Run frame: the FPGA answers only when the core
is done and the link is silent while it computes. There is no STATUS polling;
a bitstream without 0x05 is detected on connect (:meth:`Target.probe_run`) and
every operation on it fails with :class:`BitstreamError` until it is rebuilt.

Thread safety: one :class:`Link` is shared by the GUI's request threads, its
link monitor and a running acquisition, so every frame (and every multi-frame
operation of :class:`Target`) holds ``Link.lock``. Read timeouts scale with
the number of bytes expected at the current baud rate, so a dead link is
noticed in a fraction of a second while an 8 KB read still has time to arrive.
"""

from __future__ import annotations

import struct
import threading
import time
import zlib
from dataclasses import dataclass

import config as C
import sakura_lbus

CMD_READ_REG, CMD_WRITE_REG, CMD_WRITE_DATA, CMD_READ_DATA = 0x01, 0x02, 0x03, 0x04
CMD_RUN = 0x05  # write CTRL, reply only when the core is done (status block + result)
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
DK_LEN, CT_LEN, K_LEN = 2400, 1088, 32
OP_KEYGEN, OP_ENCAPS = 0, 1
#: OP_MODE -> (name, input bytes at IN_BASE, output bytes at OUT_BASE)
OPS = {OP_KEYGEN: ("KeyGen", 64, 1184 + DK_LEN),          # d || z  -> ek || dk
       OP_ENCAPS: ("Encaps", 1184 + 32, CT_LEN + K_LEN),  # ek || m -> c || K
       OP_DECAPS: ("Decaps", DK_LEN + CT_LEN, K_LEN)}     # dk || c -> K




def status_flags(st: int) -> list[str]:
    """Names of the STATUS bits that are set (``busy`` / ``done`` / ``error``)."""
    return [n for bit, n in ((ST_BUSY, "busy"), (ST_DONE, "done"), (ST_ERROR, "error")) if st & bit]


ERRORS = {0: "none", 1: "unsupported SEC_LEVEL / OP_MODE",
          2: "encapsulation key failed the modulus check", 3: "illegal microcode"}

#: Fixed part of every read timeout (bridge turnaround + USB latency), seconds.
BASE_TIMEOUT = 0.3


class LinkError(Exception):
    """The serial port could not be opened."""


class BitstreamError(ValueError):
    """The bitstream lacks 0x05 Run, the only way operations are started."""


NO_RUN = ("this bitstream has no 0x05 Run command (it predates it): rebuild it from "
          "hdl/core/uart_axi_bridge.sv -- operations are disabled, register and buffer access still work")


class ProtocolError(Exception):
    """A frame was malformed, timed out, or the bridge reported an error.

    ``fatal`` is True when the port itself is gone (closed, USB unplugged):
    retrying on the same :class:`Link` cannot succeed.
    """

    def __init__(self, msg: str, fatal: bool = False):
        super().__init__(msg)
        self.fatal = fatal


@dataclass
class Outcome:
    status: int
    error_code: int
    cycles: int
    output: bytes
    wall_time: float
    link_error: str = ""
    link_fatal: bool = False

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

    def __init__(self, port: str, baud: int = C.BAUD):
        try:
            import serial
        except ImportError:
            raise LinkError("pyserial is missing: uv sync (or pip install pyserial)")
        self._serial_exc = (serial.SerialException, OSError, TypeError, AttributeError)
        try:
            # exclusive: a second program (or a second GUI tab) must not share the port
            if sakura_lbus.is_sakura_port(port):   # SAKURA-X board USB (local bus)
                self.ser = sakura_lbus.LbusPort(port, baud, timeout=BASE_TIMEOUT,
                                                write_timeout=2.0, exclusive=True)
            else:
                self.ser = serial.Serial(port, baud, bytesize=8, parity="N", stopbits=1,
                                         timeout=BASE_TIMEOUT, write_timeout=2.0, exclusive=True)
        except Exception as e:  # serial.SerialException and OS errors
            msg = str(e)
            if "busy" in msg.lower() or "Errno 16" in msg:
                msg += " -- the port is held by another program (on Digilent boards the ...0 " \
                       "interface is the JTAG channel, held by openFPGALoader / Vivado)"
            raise LinkError("cannot open %s: %s" % (port, msg))
        self.port, self.baud = port, baud
        self.lock = threading.RLock()
        self.closed = False
        try:
            self.ser.reset_input_buffer()
        except self._serial_exc:
            pass

    def close(self) -> None:
        """Close the port. Safe from any thread: an I/O call blocked in another
        thread fails with a fatal :class:`ProtocolError` right away."""
        self.closed = True
        try:
            self.ser.close()
        except self._serial_exc:
            pass

    def _byte_time(self) -> float:
        return 10.0 / self.baud  # 8N1: 10 bit times per byte

    def _read(self, n: int, extra: float = 0.0) -> bytes:
        self.ser.timeout = BASE_TIMEOUT + extra + 2.0 * n * self._byte_time()
        buf = self.ser.read(n)
        if len(buf) != n:
            raise ProtocolError("timeout: got %d of %d bytes" % (len(buf), n))
        return buf

    def command(self, cmd: int, addr: int, data: bytes = b"", wait: float = 0.0) -> bytes:
        """Send one frame, return the response payload. ``wait`` is extra time
        the FPGA may take before it answers (0x05 replies after the operation)."""
        frame = struct.pack("<BII", cmd, addr, len(data)) + data
        frame += struct.pack("<I", zlib.crc32(frame) & 0xFFFFFFFF)
        with self.lock:
            if self.closed:
                raise ProtocolError("port closed", fatal=True)
            try:
                self.ser.reset_input_buffer()
                self.ser.write(frame)
                # write() returns once the frame is queued, not sent: allow its wire time too
                hdr = self._read(5, extra=wait + 2.0 * len(frame) * self._byte_time())
                status, n = struct.unpack("<BI", hdr)
                if n > 16384:
                    raise ProtocolError("response LEN=%d: link out of sync" % n)
                body = self._read(n + 4)
            except ProtocolError:
                raise
            except self._serial_exc as e:
                self.closed = True
                raise ProtocolError("serial port lost: %s" % e, fatal=True)
        payload, crc = body[:n], struct.unpack("<I", body[n:])[0]
        if zlib.crc32(hdr + payload) & 0xFFFFFFFF != crc:
            raise ProtocolError("response CRC mismatch")
        if status != 0:
            raise ProtocolError("bridge returned error status 0x%02x" % status)
        return payload

    def rd(self, name: str) -> int:
        return struct.unpack("<I", self.command(CMD_READ_REG, REG[name]))[0]

    def wr(self, name: str, value: int) -> None:
        self.command(CMD_WRITE_REG, REG[name], struct.pack("<I", value & 0xFFFFFFFF))

    def write_data(self, offset: int, data: bytes) -> None:
        with self.lock:
            for i in range(0, len(data), MAX_WRITE):
                self.command(CMD_WRITE_DATA, offset + i, data[i : i + MAX_WRITE])

    def read_data(self, offset: int, length: int) -> bytes:
        data = self.command(CMD_READ_DATA, offset, struct.pack("<I", length))
        if len(data) != length:
            raise ProtocolError("read %d bytes, expected %d" % (len(data), length))
        return data

    def run(self, value: int, out_offset: int, out_count: int, wait: float,
            reg: str = "CTRL") -> tuple[int, int, int, bytes]:
        """0x05 Run: write ``value`` to ``reg``, wait for the completion notice.

        Returns ``(STATUS, CYCLE_COUNT, ERROR_CODE, output)``; ``output`` is the
        ``out_count`` buffer bytes at ``out_offset``, sent only when STATUS is
        done without error (else empty).
        """
        args = struct.pack("<III", value & 0xFFFFFFFF, out_offset, out_count)
        data = self.command(CMD_RUN, REG[reg], args, wait=wait)
        if len(data) < 12:
            raise ProtocolError("Run reply of %d bytes, expected >= 12" % len(data))
        st, cycles, err = struct.unpack("<III", data[:12])
        return st, cycles, err, data[12:]

    def resync(self) -> bool:
        """Drain the line after an error; True if the core answers again."""
        with self.lock:
            if self.closed:
                return False
            time.sleep(0.2)  # longer than the bridge's 100 ms inter-byte timeout
            try:
                self.ser.reset_input_buffer()
                return self.rd("ALG_ID") == 1
            except (ProtocolError, *self._serial_exc):
                return False


class Target:
    """An ML-KEM-768 accelerator reached over a :class:`Link`.

    The register / buffer layout of an operation is written once by
    :meth:`configure` and kept (the CSRs hold it); :meth:`reset`, a link error
    or a failed operation forget it, and ``decaps(..., resend_dk=True)``
    rewrites it, so a board reset between traces is recovered at the next
    key refresh.
    """

    def __init__(self, link: Link, op_timeout: float = 5.0):
        self.link = link
        self.op_timeout = op_timeout
        self.has_run: bool | None = None   # bitstream supports 0x05 (None: not probed)
        self._config: tuple | None = None  # what configure() last wrote

    @classmethod
    def open(cls, port: str, baud: int = C.BAUD) -> "Target":
        if port.startswith("sakura-shell:"):
            from sakura_shell import ShellLink, ShellTarget
            return ShellTarget(ShellLink(port, baud))
        return cls(Link(port, baud))

    def close(self) -> None:
        self.link.close()

    def probe_run(self) -> bool:
        """Does the bitstream have 0x05 Run? A Run that writes 0 to the
        read-only ALG_ID starts nothing and answers at once; a bridge without
        0x05 answers with error status 0x01 (the frame is consumed cleanly)."""
        try:
            self.link.run(0, 0, 0, wait=0.5, reg="ALG_ID")
            self.has_run = True
        except ProtocolError as e:
            if e.fatal or "error status" not in str(e):
                raise
            self.has_run = False
        return self.has_run

    def info(self) -> dict:
        with self.link.lock:
            alg = self.link.rd("ALG_ID")
            v = self.link.rd("VERSION")
            if self.has_run is None:
                self.probe_run()
        return {
            "alg_id": alg,
            "version": "%d.%d.%d" % ((v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF),
            "port": self.link.port, "baud": self.link.baud, "core_mhz": C.CORE_MHZ,
            "has_run": bool(self.has_run), "op_timeout": self.op_timeout,
        }

    def require_run(self) -> None:
        """Raise :class:`BitstreamError` unless the bitstream has 0x05 Run."""
        if self.has_run is None:
            self.probe_run()
        if not self.has_run:
            raise BitstreamError(NO_RUN)

    def ping(self) -> bool:
        """One register read; True if the core identifies itself as ML-KEM."""
        return self.link.rd("ALG_ID") == 1

    # ---- the steps of an operation (SimpleSerial x / m / s / c / e / a) ------

    def reset(self) -> None:
        """x: pulse CTRL.reset (datapath back to idle, STATUS cleared)."""
        self._config = None
        self.link.wr("CTRL", CTRL_RESET)

    def forget_config(self) -> None:
        """Make the next operation rewrite the configuration registers (call
        after writing CSRs by hand)."""
        self._config = None

    def configure(self, op: int = OP_DECAPS, in_len: int = DK_LEN + CT_LEN, force: bool = False) -> None:
        """m: operation and buffer layout. Skipped when already written."""
        cfg = (op, in_len)
        if self._config == cfg and not force:
            return
        link = self.link
        with link.lock:
            self._config = None
            link.wr("SEC_LEVEL", LEVEL)
            link.wr("OP_MODE", op)
            link.wr("DATA_IN_ADDR", C.IN_BASE)
            link.wr("DATA_IN_LEN", in_len)
            link.wr("DATA_OUT_ADDR", C.OUT_BASE)
            self._config = cfg

    def load_secret_key(self, dk: bytes) -> None:
        """s: the decapsulation key, at the start of the input region."""
        if len(dk) != DK_LEN:
            raise ValueError("dk must be %d bytes" % DK_LEN)
        self.link.write_data(C.IN_BASE, dk)

    def load_ciphertext(self, ct: bytes) -> None:
        """c: the ciphertext, right after dk (IN_BASE + 2400)."""
        if len(ct) != CT_LEN:
            raise ValueError("c must be %d bytes" % CT_LEN)
        self.link.write_data(C.IN_BASE + DK_LEN, ct)

    def read_shared_secret(self) -> bytes:
        """a: the 32-byte shared secret K of the last Decaps."""
        return self.link.read_data(C.OUT_BASE, K_LEN)

    def run(self, out_len: int = K_LEN) -> tuple[int, int, int, bytes]:
        """e: start the configured operation and return, once the FPGA reports
        it finished, ``(STATUS, CYCLE_COUNT, ERROR_CODE, output)``.

        One 0x05 Run frame: the bridge writes CTRL.start, sends nothing until
        the core is idle again, then replies with the status block and
        ``out_len`` bytes of the result.
        """
        link = self.link
        with link.lock:
            self.require_run()
            st, cycles, err, out = link.run(CTRL_START, C.OUT_BASE, out_len, wait=self.op_timeout)
        if st & ST_BUSY:
            raise ProtocolError("operation did not finish within the FPGA's Run timeout (STATUS=0x%x)" % st)
        return st, cycles, err, out

    def decaps(self, dk: bytes, ct: bytes, resend_dk: bool = True) -> Outcome:
        """One Decaps. With ``resend_dk=False`` only ``c`` (1088 B) is sent.

        The data buffer holds ``dk || c`` (ciphertext at IN_BASE + 2400). Not
        resending ``dk`` cuts the UART time to a third, but it relies on the
        core never writing into the input region; the acquisition therefore
        refreshes dk periodically (which also rewrites the configuration) and
        checks every output against its expected shared secret.
        """
        if len(dk) != DK_LEN or len(ct) != CT_LEN:
            raise ValueError("decaps takes dk=%d and c=%d bytes" % (DK_LEN, CT_LEN))
        self.require_run()  # before uploading anything
        t0 = time.time()
        try:
            with self.link.lock:
                self.configure(OP_DECAPS, DK_LEN + CT_LEN, force=resend_dk)
                if resend_dk:
                    self.load_secret_key(dk)
                self.load_ciphertext(ct)
                st, cycles, err, out = self.run(K_LEN)
            outcome = Outcome(st, err, cycles, out, time.time() - t0)
        except ProtocolError as e:
            outcome = Outcome(0, 0, 0, b"", time.time() - t0, link_error=str(e), link_fatal=e.fatal)
        if not outcome.ok:
            self._config = None
        return outcome

    def preflight(self) -> list:
        """Bottom-up link checks; returns ``[(name, ok, detail), ...]``."""
        link, res = self.link, []

        def step(name, fn):
            try:
                ok, detail = fn()
            except ProtocolError as e:
                ok, detail = False, str(e)
                if not e.fatal:
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

        def notify():
            if not self.probe_run():
                return False, NO_RUN
            return True, "0x05 Run supported: the FPGA reports completion, link silent during the operation"

        with link.lock:
            step("link identity (ALG_ID)", alg)
            step("datapath reset", reset)
            step("bulk transfer (1 KB)", bulk)
            step("0x05 Run (required for operations)", notify)
            self._config = None  # reset() and the loopback touched the layout
        return res


def _port_hint(p) -> tuple[str, int]:
    """(hint, rank) for a pyserial ListPortInfo; lower rank = better candidate.

    Digilent boards (Arty A7/Z7) enumerate one FT2232H as two ports: interface
    0 is the JTAG channel (used by openFPGALoader / Vivado, never the UART),
    interface 1 the USB-UART. On macOS the device name is the serial number
    plus the interface digit (``/dev/cu.usbserial-<serial>1``); on Linux the
    USB location ends in ``:1.<interface>``.
    """
    desc = " ".join(str(x) for x in (p.description, p.manufacturer) if x)
    if "Bluetooth" in p.device or "debug-console" in p.device:
        return "not a board", 9
    if _sakura_iface(p) == 0:
        return "FT2232H channel A raw -- use sakura-shell: for shell firmware", 7
    if _sakura_iface(p) == 1:
        return "FT2232H channel B -- reserved for shell RTS reset, not data", 8
    iface = None
    if p.serial_number and p.device.endswith(p.serial_number + "0"):
        iface = 0
    elif p.serial_number and p.device.endswith(p.serial_number + "1"):
        iface = 1
    elif p.location and ":1." in p.location:
        iface = int(p.location.rsplit(".", 1)[-1]) if p.location.rsplit(".", 1)[-1].isdigit() else None
    if p.vid == 0x0403 and ("Digilent" in desc or iface is not None):
        if iface == 0:
            return "JTAG channel -- not the UART", 8
        if iface == 1:
            return "board UART (recommended)", 0
    if p.vid is not None:
        return "USB serial", 2
    return "", 5


def _sakura_iface(p) -> int | None:
    """FT2232H interface (0 = channel A, 1 = channel B) of a port that belongs to a
    SAKURA-X board: an FT2232H programmed with AIST's sasebo_giii.xml (no Digilent
    strings; product "USB <-> Serial Converter"). None for anything else."""
    from sakura_shell import usb_identity
    identity = usb_identity(p)
    desc = " ".join(str(x) for x in (p.description, p.manufacturer, identity["product"]) if x)
    if identity["vid"] != 0x0403 or identity["pid"] != 0x6010 or "Digilent" in desc:
        return None
    return identity["interface"]


def _is_sakura_channel_a(p) -> bool:
    return _sakura_iface(p) == 0


def _is_phantom(p) -> bool:
    """A legacy on-board serial node with no hardware behind it (Linux creates
    /dev/ttyS0..31 whether or not the UARTs exist; pyserial lists them all)."""
    return "/ttyS" in p.device and p.vid is None and (not p.hwid or p.hwid == "n/a")


def list_ports() -> list:
    """``[(device, description, hint, recommended)]``, best candidates first."""
    try:
        from serial.tools import list_ports as lp
    except ImportError:
        raise LinkError("pyserial is missing: uv sync (or pip install pyserial)")
    from sakura_shell import PREFIX as SHELL_PREFIX, is_shell
    rows = []
    for p in lp.comports():
        if _is_phantom(p):
            continue
        hint, rank = _port_hint(p)
        rows.append((rank, p.device, p.description or "", hint))
        if _is_sakura_channel_a(p):
            rows.append((0 if is_shell(p) else 3, SHELL_PREFIX + p.device, p.description or "",
                         "SAKURA-X shell FIFO, channel A; baud ignored" +
                         (" (recommended)" if is_shell(p) else " (select explicitly for shell firmware)")))
            if not is_shell(p):
                rows.append((3, sakura_lbus.PREFIX + p.device, p.description or "",
                             "Legacy AIST local bus ONLY; incompatible with shell firmware"))
    rows.sort()
    return [(dev, desc, hint, rank == 0) for rank, dev, desc, hint in rows]


def port_exists(device: str) -> bool:
    try:
        from sakura_shell import device_of
        import os
        device = os.path.realpath(device_of(sakura_lbus.device_of(device)))
        return any(os.path.realpath(device_of(sakura_lbus.device_of(dev))) == device
                   for dev, *_ in list_ports())
    except LinkError:
        return True
