"""SAKURA-X shell FIFO transport and the five ML-KEM acquisition commands.

Native shell frames (no CRC): 0x8 | R/W nibble | (words-1) nibble |
32-bit byte address | 0x1, big endian; followed by big-endian 32-bit words.
The ML-KEM byte buffer itself is little endian. Channel B uses RTS only.
See docs/SAKURA_X_SHELL.md for the wire map and the quiet-window contract.
"""
from __future__ import annotations

import math
import os
from pathlib import Path
import struct
import threading
import time
from contextlib import contextmanager

import config as C
import uart_target as T

PREFIX = "sakura-shell:"
PRODUCT = "SAKURA-X Shell"
START, IDENT, TAIL, CLOCK_HZ, FAULT, INPUT_READY, RESULT_READY = range(0x100, 0x11c, 4)
MAGIC = 0x534B0001
BUFFER = 0x4000
#: AIST's factory Spartan-6 controller (sasebo_giii_ctrl) reads a register on 00 AH AL and always
#: answers two bytes; the shell controller only buffers these three bytes (no 6-byte frame yet).
AIST_ID_READ = bytes([0x00, 0xFF, 0xFC])

#: Spartan-6 LEDs (LED11..LED20 = S_LED_0..9) that tell the two controllers apart.
LED_HINT = ("Spartan-6 LEDs: shell controller = LED17 toggles about every 1.3 s (LED16 lit = stuck "
            "transaction); AIST factory controller = LED11 blinks about 3 times a second.")
AIST_FOUND = ("The Spartan-6 runs AIST's FACTORY controller (it answered the factory read with %s), not the "
              "sakura-x-shell controller. The shell bit is loaded into SRAM only, so a power cycle or USB "
              "replug (SW1 = USB powers the board from it) brings the factory design back from the flash. "
              "Load third_party/sakura_x_shell/sakura-x-shell-ctrl/sakura-x-shell-ctrl.bit on CN10 "
              "(xc3sprog -c xpc -p 1 ...), keep the board powered, then program the Kintex-7 and connect. "
              "Do not leave this pairing running: the factory controller and sakura_shell_top drive the "
              "same inter-FPGA lines.")
NOTHING = ("Neither the shell controller nor AIST's factory controller answers on Channel A, even after a "
           "Channel-B RTS reset. Before the Kintex-7 is involved: (1) the Spartan-6 is not configured "
           "(its DONE LED off) or its load failed -- reload the shell bit on CN10 and check xc3sprog "
           "reports XC6SLX45; (2) Channel A is not in 245 FIFO mode (EEPROM); (3) a factory controller "
           "locked by an earlier shell frame -- it only recovers at power-off or SW6. " + LED_HINT)


def device_of(port: str) -> str:
    """Strip only this transport's prefix."""
    return port[len(PREFIX):] if port.startswith(PREFIX) else port


def usb_identity(port) -> dict:
    """Read USB identity from Linux sysfs, with pyserial metadata as fallback.

    ttyUSB numbering can change after unplugging. The parent USB device and
    bInterfaceNumber identify the physical pair even when a udev alias or
    incomplete ListPortInfo does not contain the serial/location fields.
    """
    result = dict(device=port.device, vid=port.vid, pid=port.pid,
                  serial=port.serial_number or "", product=port.product or "",
                  manufacturer=port.manufacturer or "",
                  interface=None, usb_path="")
    path = Path("/sys/class/tty") / Path(os.path.realpath(port.device)).name / "device"
    if path.exists():
        resolved = path.resolve()
        for parent in (resolved, *resolved.parents):
            try:
                if (parent / "bInterfaceNumber").is_file():
                    result["interface"] = int((parent / "bInterfaceNumber").read_text().strip(), 16)
                if (parent / "idVendor").is_file():
                    result.update(vid=int((parent / "idVendor").read_text().strip(), 16),
                                  pid=int((parent / "idProduct").read_text().strip(), 16),
                                  usb_path=str(parent))
                    for field, filename in (("serial", "serial"), ("product", "product"),
                                            ("manufacturer", "manufacturer")):
                        if (parent / filename).is_file():
                            result[field] = (parent / filename).read_text().strip()
                    break
            except (OSError, ValueError):
                continue
    if result["interface"] is None:
        result["interface"] = _metadata_interface(port)
    return result


def _metadata_interface(port) -> int | None:
    if port.location and ":1." in port.location:
        value = port.location.rsplit(".", 1)[-1]
        if value in ("0", "1"):
            return int(value)
    for n in (0, 1):
        if port.serial_number and port.device.endswith(port.serial_number + str(n)):
            return n
    return None


def interface_number(port) -> int | None:
    """Identify the USB interface, never infer it from a ttyUSB suffix."""
    return usb_identity(port)["interface"]


def is_shell(port) -> bool:
    """Only the exact EEPROM product string permits automatic selection."""
    p = usb_identity(port)
    return (p["vid"], p["pid"], p["product"]) == (0x0403, 0x6010, PRODUCT)


def _same_usb(a, b):
    if (a["vid"], a["pid"]) != (b["vid"], b["pid"]):
        return False
    if a["usb_path"] and b["usb_path"]:
        return a["usb_path"] == b["usb_path"]
    return bool(a["serial"]) and a["serial"] == b["serial"]


def usb_ports() -> list[dict]:
    """List physical tty devices once; USB metadata does not open a port."""
    from serial.tools import list_ports
    unique = {}
    for p in list_ports.comports():
        unique.setdefault(os.path.realpath(p.device), usb_identity(p))
    return list(unique.values())


def _reset_peer(a, ports):
    matches = [p for p in ports if p["interface"] == 1 and _same_usb(a, p)]
    if len(matches) != 1:
        raise T.LinkError("Channel B could not be paired with Channel A on the same USB device. "
                          "Reconnect the board USB and click Refresh devices.")
    return matches[0]


def connection_devices() -> list[dict]:
    """Expose FIFO candidates with an explicit, non-guessing A/B mapping."""
    ports = usb_ports()
    result = []
    for a in ports:
        if (a["vid"], a["pid"], a["interface"]) != (0x0403, 0x6010, 0):
            continue
        if "digilent" in (a["manufacturer"] + " " + a["product"]).lower():
            continue  # An Arty JTAG channel is not a SAKURA FIFO candidate.
        try:
            b, error = _reset_peer(a, ports), ""
        except T.LinkError as e:
            b, error = {}, str(e)
        result.append(dict(a, device=PREFIX + a["device"], data_port=a["device"],
                           reset_port=b.get("device", ""), pair_error=error,
                           recommended=a["product"] == PRODUCT,
                           hint="Channel A: FIFO data; Channel B: RTS reset only"))
    return result


def paired_reset_port(data_port: str) -> str:
    """Pair by physical USB parent, or unique serial when sysfs is unavailable."""
    ports = usb_ports()
    selected = [p for p in ports if os.path.realpath(p["device"]) == os.path.realpath(data_port)]
    if len(selected) != 1:
        raise T.LinkError("cannot identify Channel A: %s" % data_port)
    a = selected[0]
    if (a["vid"], a["pid"], a["interface"]) != (0x0403, 0x6010, 0):
        raise T.LinkError("selected device is not FT2232H Channel A (USB interface 00). "
                          "Select a SAKURA-X USB FIFO device in Connection; do not select Channel B.")
    return _reset_peer(a, ports)["device"]


class ShellLink:
    """Serialized native shell accesses; owns data A and reset B together."""

    def __init__(self, port: str, baud: int = C.BAUD, report=None):
        import serial
        self.port, self.baud = port, None  # no UART baud on the FIFO data path
        self.lock = threading.RLock()
        self.closed = False
        self.ser = self.reset_ser = None
        self._serial_exc = (serial.SerialException, OSError, TypeError, AttributeError)
        self.quiet_ms = 20.0
        stage = "USB channel pairing"
        try:
            self.data_port = device_of(port)
            self.reset_port = paired_reset_port(self.data_port)
            if report:
                report(stage, True, "A: %s; B: %s (same USB device)" % (self.data_port, self.reset_port))
            # Set RTS before open: active-low RTS_N must stay HIGH in normal use.
            # Opening B with pyserial's default RTS=True would hold S6 in reset.
            stage = "Open reset channel B"
            self.reset_ser = serial.Serial(port=None, baudrate=115200, timeout=0.3,
                                           write_timeout=2, exclusive=True)
            self.reset_ser.rts = False
            self.reset_ser.dtr = False
            self.reset_ser.port = self.reset_port
            self.reset_ser.open()
            if report:
                report(stage, True, "RTS_N released; no UART data is sent")
            stage = "Open FIFO channel A"
            # The tty API requires a nominal baud; EEPROM FIFO mode ignores it.
            self.ser = serial.Serial(self.data_port, C.BAUD, timeout=0.5,
                                     write_timeout=2, exclusive=True)
            self.ser.reset_input_buffer()
            if report:
                report(stage, True, "USB FIFO handle opened; protocol response not yet checked")
        except Exception as e:
            self.close()
            if report:
                report(stage, False, str(e) + " Close other applications using either A or B; "
                       "check device access permissions in Linux if access is denied.")
            raise T.LinkError("cannot open shell data/reset ports: %s" % e) from e

    def close(self):
        """Release both ports; do not send a reset on disconnect."""
        self.closed = True
        for port in (self.ser, self.reset_ser):
            if port is not None:
                try:
                    port.close()
                except self._serial_exc:
                    pass

    @contextmanager
    def _io(self):
        with self.lock:
            if self.closed:
                raise T.ProtocolError("shell port closed", fatal=True)
            try:
                yield
            except self._serial_exc as e:
                self.close()
                raise T.ProtocolError("shell USB lost: %s" % e, fatal=True) from e

    def _read(self, n: int, wait: float = 0.0, phase="reply") -> bytes:
        self.ser.timeout = 0.5 + wait
        data = self.ser.read(n)
        if len(data) != n:
            # There is no in-band resync after a partial native shell frame.
            # Stop the campaign; explicit Channel-B reset is the recovery path.
            hint = ("No complete Spartan command ACK. Check Channel A FIFO mode, the shell "
                    "Spartan bitstream and reset state. " + LED_HINT if phase == "Spartan ACK" else
                    "Spartan ACK was received, but Kintex did not return a complete word. "
                    "Check the matching sakura_shell_top.bit, inter-FPGA bus and clocks.")
            raise T.ProtocolError("%s timeout (%d/%d bytes). %s Use Reset & connect in the GUI, "
                                  "then reload DK/CT." % (phase, len(data), n, hint), fatal=True)
        return data

    @staticmethod
    def _header(cmd: int, address: int, words: int) -> bytes:
        if not 1 <= words <= 16 or not 0 <= address <= 0xFFFFFFFC or address % 4:
            raise ValueError("shell accesses need aligned addresses and 1..16 words")
        return ((8 << 44) | (cmd << 40) | ((words - 1) << 36) |
                (address << 4) | 1).to_bytes(6, "big")

    def _access(self, cmd, address, words, payload=b"", wait=0.0):
        with self._io():
            frame = self._header(cmd, address, words) + payload
            if self.ser.write(frame) != len(frame):
                raise T.ProtocolError("short shell write; hard reset required", fatal=True)
            ack = self._read(1, phase="Spartan ACK")
            if ack != b"\0":
                raise T.ProtocolError("shell rejected frame: 0x%02x; hard reset required" % ack[0], fatal=True)
            return self._read(words * 4, wait, phase="Kintex data") if cmd == 1 else b""

    def _pulse_rts(self):
        """Channel-B RTS_N low for 20 ms: resets the shell controller (FIFOs, command decoder)
        and, through o_bus_reset_n, the Kintex bridge. No effect on AIST's factory controller."""
        self.reset_ser.rts = True
        try:
            time.sleep(0.02)
        finally:
            self.reset_ser.rts = False
        time.sleep(0.05)
        self.ser.reset_input_buffer()

    def _probe_frame(self) -> bytes:
        """The unknown-command probe; returns the 1-byte reply (or b"" on timeout)."""
        self.ser.write(self._header(0xf, 0, 1))
        self.ser.timeout = 0.5
        return self.ser.read(1)

    def check_controller(self, report=None) -> str:
        """Which Spartan-6 controller answers on Channel A? Runs before ANY Kintex access.

        1. RTS reset -- clears a shell controller left inside an unfinished transaction;
        2. AIST's factory read ``00 FF FC``: two bytes back = the factory controller is running
           (the shell bit was lost or never loaded). Sent BEFORE any shell frame, because the
           first shell byte (0x8.) puts the factory decoder into its TRAP state for good;
        3. RTS reset again (drops those three bytes from the shell's input FIFO), then the
           unknown-command probe: the shell controller answers 0x02 without a bus transaction.
           0x01 means the shell parsed the three stray bytes, i.e. the RTS reset did not reach
           it; the frame boundary is then restored in-band.

        Returns ``"shell"`` or ``"shell-no-rts"``; raises a fatal ProtocolError otherwise.
        """
        def note(name, ok, detail):
            if report:
                report(name, ok, detail)
        with self._io():
            self._pulse_rts()
            note("Channel B reset", True, "RTS_N pulsed 20 ms and released; any unfinished shell transaction is cleared")
            self.ser.write(AIST_ID_READ)
            self.ser.timeout = 0.3
            got = self.ser.read(2)
            if len(got) == 2:
                msg = AIST_FOUND % got.hex(" ")
                note("Spartan controller", False, msg)
                raise T.ProtocolError(msg, fatal=True)
            self._pulse_rts()
            ack = self._probe_frame()
            if ack == b"\x02":
                note("Spartan controller", True, "sakura-x-shell controller answered (0x02 to the unknown-command probe); "
                     "PC -> FT2232H FIFO -> Spartan-6 path works")
                return "shell"
            if ack == b"\x01":
                self.ser.write(bytes(3))          # complete the misaligned frame: it is ACKed 0x01
                self.ser.timeout = 0.5
                self.ser.read(1)
                if self._probe_frame() == b"\x02":
                    note("Spartan controller", True, "shell controller answers, but the Channel-B RTS reset does "
                         "not reach it (stray bytes were parsed); hard reset will not work -- power-cycle to recover")
                    return "shell-no-rts"
            msg = NOTHING if not ack else ("unexpected Spartan reply 0x%02x to the probe. " % ack[0]) + LED_HINT
            note("Spartan controller", False, msg)
            raise T.ProtocolError(msg, fatal=True)

    def probe_controller(self):
        """Check S6 independently of K7 using a well-framed unknown command.

        Upstream command_control.v returns 0x02 for command type 0xf and
        goes straight back to WAIT without issuing any inter-FPGA transaction.
        A timeout leaves framing unknown; only explicit hard reset recovers it.
        """
        with self._io():
            ack = self._probe_frame()
            if len(ack) != 1:
                raise T.ProtocolError("Spartan ACK timeout (0/1 bytes). " + NOTHING, fatal=True)
            if ack != b"\x02":
                raise T.ProtocolError("unexpected Spartan probe response 0x%02x; expected shell "
                                      "response 0x02. Use Reset & connect; verify the S6 shell bit." % ack[0],
                                      fatal=True)

    def read_words(self, address, count=1, wait=0.0):
        """Read 1..16 native big-endian bus words."""
        return struct.unpack(">%dI" % count, self._access(1, address, count, wait=wait))

    def write_words(self, address, values):
        """Queue native bus words; a later read fences write completion."""
        values = tuple(values)
        self._access(2, address, len(values), struct.pack(">%dI" % len(values), *values))

    def identify(self):
        """Read identity without starting an operation."""
        value = self.read_words(IDENT)[0]
        if value != MAGIC:
            raise T.BitstreamError("wrong Kintex shell ABI: 0x%08x; program sakura_shell_top.bit" % value)
        return value

    def rd(self, name):
        """Read a named core CSR outside the START quiet interval."""
        return self.read_words(T.REG[name])[0]

    def wr(self, name, value):
        """Write a core CSR, enforcing the fixed Decaps layout."""
        if name == "CTRL" and value & T.CTRL_START:
            raise ValueError("use START/run(), not a direct CTRL.start write")
        fixed = {"SEC_LEVEL": 768, "OP_MODE": 2, "DATA_IN_ADDR": C.IN_BASE,
                 "DATA_IN_LEN": T.DK_LEN + T.CT_LEN, "DATA_OUT_ADDR": C.OUT_BASE}
        if name in fixed and value != fixed[name]:
            raise ValueError("shell %s must be %d" % (name, fixed[name]))
        with self.lock:
            self.write_words(T.REG[name], [value & 0xFFFFFFFF])
            self._check_fault()  # read fence: preceding write has reached the core

    def _check_fault(self):
        fault = self.read_words(FAULT)[0]
        if fault:
            raise T.ProtocolError("Kintex shell fault %d (1=access, 2=AXI, 3=timeout, "
                                  "4=busy, 5=incomplete inputs); RESET and reload DK/CT" % fault,
                                  fatal=fault in (2, 3, 4))

    def write_data(self, offset, data):
        """Upload aligned core-buffer bytes, translating word byte order."""
        if offset % 4 or len(data) % 4 or offset < 0 or offset + len(data) > 8192:
            raise ValueError("shell buffer writes must be word aligned within 8192 bytes")
        with self.lock:
            for pos in range(0, len(data), 64):
                block = data[pos:pos + 64]
                self.write_words(BUFFER + offset + pos,
                                 struct.unpack("<%dI" % (len(block) // 4), block))
            self._check_fault()

    def read_data(self, offset, length):
        """Read buffer bytes, including unaligned diagnostic slices."""
        if offset < 0 or length < 0 or offset + length > 8192:
            raise ValueError("buffer read outside 8192 bytes")
        if length == 0:
            return b""
        first, end = offset & ~3, (offset + length + 3) & ~3
        result = bytearray()
        with self.lock:
            for pos in range(first, end, 64):
                words = min(16, (end - pos) // 4)
                result.extend(struct.pack("<%dI" % words, *self.read_words(BUFFER + pos, words)))
        return bytes(result[offset - first:offset - first + length])

    def set_quiet_window(self, seconds):
        """Reserve at least this much quiet time AFTER core completion."""
        if not math.isfinite(seconds) or not 0 <= seconds <= 10:
            raise ValueError("quiet tail must be 0..10 seconds")
        self.quiet_ms = max(20.0, seconds * 1000)

    def start(self, wait=5.0):
        """START: wait for completion in one pending read, without status polls."""
        with self.lock:
            tail = math.ceil(50_000_000 * self.quiet_ms / 1000)
            # Reassert every time: a physical reset may have restored defaults.
            self.write_words(TAIL, [tail])
            if self.read_words(TAIL)[0] != tail:
                raise T.ProtocolError("quiet-tail register readback mismatch")
            status = self.read_words(START, wait=wait + self.quiet_ms / 1000)[0]
            if status & 0x80000000:
                self._check_fault()
                raise T.ProtocolError("START failed without a valid completion", fatal=True)
            return status

    def run(self, value, out_offset, out_count, wait, reg="CTRL"):
        """Adapt START and post-completion reads to the existing Target API."""
        if reg != "CTRL" or value != T.CTRL_START:
            raise ValueError("shell run only accepts START")
        with self.lock:
            status = self.start(wait)
            cycles, error = self.rd("CYCLE_COUNT"), self.rd("ERROR_CODE")
            out = self.read_data(out_offset, out_count) if status & T.ST_DONE and not status & T.ST_ERROR else b""
            return status, cycles, error, out

    def hard_reset(self):
        """Explicit recovery: pulse Channel-B RTS_N; resets S6 and K7 logic."""
        with self._io():
            self._pulse_rts()
    
    def resync(self):
        """Do not inject frames/reset into an unknown partial transaction."""
        return False


class ShellTarget(T.Target):
    """Decapsulation target with RESET / LOAD_DK / LOAD_CT / START / READ_SS."""

    def probe_run(self):
        """Require the shell ABI instead of probing the UART 0x05 command."""
        self.link.identify()
        self.has_run = True
        return True

    def info(self):
        """Return GUI metadata including FIFO mode and the quiet tail."""
        info = super().info()
        info.update(transport="sakura-shell", run_command="START", baud=None,
                    data_port=self.link.data_port, reset_port=self.link.reset_port,
                    quiet_tail_ms=self.link.quiet_ms)
        return info

    def configure(self, op=T.OP_DECAPS, in_len=T.DK_LEN + T.CT_LEN, force=False):
        """Configure only the fixed ML-KEM-768 decapsulation operation."""
        if op != T.OP_DECAPS or in_len != T.DK_LEN + T.CT_LEN:
            raise ValueError("the SAKURA shell command set supports ML-KEM-768 Decaps only")
        super().configure(op, in_len, force)

    def load_dk(self, dk):
        """LOAD_DK: upload exactly 2400 bytes, without decoding or computing."""
        self.load_secret_key(dk)

    def load_ct(self, ct):
        """LOAD_CT: upload exactly 1088 bytes, without starting the core."""
        self.load_ciphertext(ct)

    def start(self):
        """START processing, return completion status after the quiet window."""
        with self.link.lock:
            self.require_run()
            self.configure()
            return self.link.start(self.op_timeout)

    def read_shared_secret(self):
        """READ_SS: only return the 32-byte K of a successful completed run."""
        with self.link.lock:
            status = self.link.rd("STATUS")
            if self.link.read_words(RESULT_READY)[0] != 1 or status != T.ST_DONE:
                raise T.ProtocolError("READ_SS requires successful completion; STATUS=0x%x" % status)
            return super().read_shared_secret()

    def preflight(self):
        """Non-cryptographic Linux bring-up checks, only when explicitly requested."""
        with self.link.lock:
            self.link.identify()
            self.reset()
            pattern = bytes((i * 7 + 13) & 255 for i in range(1024))
            self.link.write_data(C.IN_BASE, pattern)
            ok = self.link.read_data(C.IN_BASE, len(pattern)) == pattern
            self.reset()  # invalidate partial inputs from the loopback
            return [("shell ABI", True, "SK ABI 1; native FIFO, Channel B reserved for RTS reset"),
                    ("bulk transfer", ok, "1024-byte buffer round trip"),
                    ("START completion", self.probe_run(), "blocking completion read; no READ_STATUS polling")]
