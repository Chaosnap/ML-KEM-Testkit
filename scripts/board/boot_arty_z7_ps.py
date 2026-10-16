#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = ["pyftdi>=0.55"]
# ///
"""Bring up the Arty Z7-20 PS variant from the host, without xsdb/hw_server.

Does what scripts/vivado/load_arty_z7_ps.tcl does, but drives the board's on-board
FT2232H JTAG channel directly (pyftdi) instead of going through hw_server
and an XVC server. Written because hw_server's ARM-DAP accesses fail over the
openFPGALoader <= 1.1.1 XVC server ("Invalid DAP ACK value: 3": TDO bits read
in TMS mode are misaligned, fixed upstream in commit 4700951); it also needs
no Docker, xsdb or XVC at all.

Steps (run after every power-up, jumper JP4 on JTAG):
  1. program the PL with openFPGALoader (arty_z7_ps_top.bit)
  2. hold CPU0 in reset
  3. run ps7_init (MIO, PLLs, clocks, peripherals; DDR is skipped -- the
     forwarder lives in on-chip memory) and ps7_post_config (PS<->PL level
     shifters), register by register through the DAP's AHB-AP
  4. write the UART forwarder (sw/arty_z7_uart_fwd) into OCM at 0x0
  5. release CPU0: it starts at its reset vector 0x0, i.e. the forwarder
After this the host talks to the ML-KEM core over the board's on-board USB
serial (the ...1 interface), 115200 baud, exactly like the Arty A7.

Usage (from the repo root; nothing else may hold the board's JTAG channel,
so stop `openFPGALoader --xvc` / run_vivado.sh first):
  uv run scripts/board/boot_arty_z7_ps.py             # all steps
  uv run scripts/board/boot_arty_z7_ps.py --no-bit    # PL already programmed
"""
import argparse
import re
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

from pyftdi.ftdi import Ftdi

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "build/vivado/arty-z7-ps_mlkem/out"

# On-board Digilent JTAG = FT2232H interface A in MPSSE mode
# (TCK=AD0, TDI=AD1, TDO=AD2, TMS=AD3; same pin setup as openFPGALoader's
# "digilent" cable: low byte value/dir 0xe8/0xeb, high byte 0x00/0x60).
LOW_VAL, LOW_DIR, HIGH_VAL, HIGH_DIR = 0xE8, 0xEB, 0x00, 0x60
TCK_HZ = 6e6

# Zynq-7000 cascaded chain: TDI -> ARM DAP (IR 4) -> PL TAP (IR 6) -> TDO.
PL_IDCODE, DAP_IDCODE = 0x03727093, 0x4BA00477      # PL compared without its version nibble
PL_BYPASS_IR = [1] * 6
IR_DPACC, IR_APACC, IR_IDCODE = 0xA, 0xB, 0xE
ACK_OK, ACK_WAIT = 2, 1
AHB_AP = 0                                            # AP0: system bus (OCM, SLCR, peripherals)

SLCR_LOCK, SLCR_UNLOCK = 0xF8000004, 0xF8000008
A9_CPU_RST_CTRL = 0xF8000244                          # bit 0 A9_RST0, bit 4 A9_CLKSTOP0
DEVCFG_MCTRL = 0xF8007080                             # [31:28] PS silicon version
UART0_CR, UART1_CR = 0xE0000000, 0xE0001000
OCM_TOP = 0x30000                                     # OCM low mapping, see sw/arty_z7_uart_fwd/link.ld


def to_bits(v, n):
    return [(v >> k) & 1 for k in range(n)]


def to_int(bits):
    return sum(b << k for k, b in enumerate(bits))


def load_libusb():
    """pyusb searches for libusb on ctypes' default path, which on macOS does
    not include Homebrew's prefix. Load it once here; pyusb caches it."""
    import ctypes.util
    import usb.backend.libusb1

    def find(name):
        for p in ("/opt/homebrew/lib/libusb-1.0.dylib", "/usr/local/lib/libusb-1.0.dylib"):
            if Path(p).exists():
                return p
        return ctypes.util.find_library(name)

    if usb.backend.libusb1.get_backend(find_library=find) is None:
        sys.exit("libusb not found (macOS: brew install libusb)")


class Jtag:
    """Minimal MPSSE JTAG: every scan starts and ends in Run-Test/Idle."""

    def __init__(self, url):
        load_libusb()
        self.f = Ftdi()
        self.f.open_mpsse_from_url(url, direction=LOW_DIR, initial=LOW_VAL, frequency=TCK_HZ)
        self.f.write_data(bytes([Ftdi.SET_BITS_HIGH, HIGH_VAL, HIGH_DIR]))
        self.f.purge_buffers()

    def close(self):
        self.f.close()

    def _tms(self, bits):
        for i in range(0, len(bits), 6):
            chunk = bits[i:i + 6]
            self.f.write_data(bytes([0x4B, len(chunk) - 1, to_int(chunk)]))

    def reset(self):
        self._tms([1, 1, 1, 1, 1, 0])

    def _shift(self, tdi, read):
        """In Shift-xR: shift `tdi` (first bit first; the last one with TMS=1),
        then Exit1 -> Update -> Idle. Returns the TDO bits if `read`."""
        body, last = tdi[:-1], tdi[-1]
        nbytes, rem = divmod(len(body), 8)
        cmd = bytearray()
        if nbytes:
            n = nbytes - 1
            cmd += bytes([0x39 if read else 0x19, n & 0xFF, n >> 8])
            cmd += bytes(to_int(body[8 * i:8 * i + 8]) for i in range(nbytes))
        if rem:
            cmd += bytes([0x3B if read else 0x1B, rem - 1, to_int(body[8 * nbytes:])])
        cmd += bytes([0x6B if read else 0x4B, 0, (last << 7) | 0x01])
        cmd += bytes([0x4B, 1, 0x01])
        if read:
            cmd.append(Ftdi.SEND_IMMEDIATE)
        self.f.write_data(bytes(cmd))
        if not read:
            return None
        want = nbytes + (1 if rem else 0) + 1
        raw = self.f.read_data_bytes(want, attempt=50)
        if len(raw) != want:
            raise IOError("JTAG: short read (%d of %d bytes)" % (len(raw), want))
        out = []
        for i in range(nbytes):
            out += to_bits(raw[i], 8)
        if rem:   # bit-mode reads arrive in the top `rem` bits
            out += [(raw[nbytes] >> (8 - rem + k)) & 1 for k in range(rem)]
        out.append(raw[-1] >> 7)
        return out

    def ir(self, bits, read=False):
        self._tms([1, 1, 0, 0])
        return self._shift(bits, read)

    def dr(self, bits, read=True):
        self._tms([1, 0, 0])
        return self._shift(bits, read)


class Dap:
    """ADIv5 JTAG-DP, one transaction at a time (no pipelining, no overrun
    detection). The PL TAP is kept in BYPASS: one pad bit in front of the
    35-bit DPACC/APACC register on TDI, one in front of the capture on TDO."""

    def __init__(self, jtag):
        self.j, self.cur_ir, self.select = jtag, None, None

    def _set_ir(self, ir):
        if ir != self.cur_ir:
            self.j.ir(PL_BYPASS_IR + to_bits(ir, 4))
            self.cur_ir = ir

    def idcode(self):
        self._set_ir(IR_IDCODE)
        return to_int(self.j.dr([0] * 33)[1:33])

    def _scan(self, ir, rnw, a, data=0):
        """Returns the data captured with an OK ack (the previous read's
        result). WAIT means the previous access is still busy and this
        request was dropped, so it is repeated."""
        self._set_ir(ir)
        bits = [0, rnw] + to_bits(a >> 2, 2) + to_bits(data, 32)
        for _ in range(1000):
            out = self.j.dr(bits)[1:]
            ack = to_int(out[0:3])
            if ack == ACK_OK:
                return to_int(out[3:35])
            if ack != ACK_WAIT:
                raise IOError("DAP: invalid ACK %d" % ack)
        raise IOError("DAP: access still WAITing after 1000 retries")

    def _rdbuff(self):
        return self._scan(IR_DPACC, 1, 0xC)

    def dp_read(self, a):
        self._scan(IR_DPACC, 1, a)
        return self._rdbuff()

    def dp_write(self, a, v):
        self._scan(IR_DPACC, 0, a, v)
        self._rdbuff()

    def _select(self, ap, reg):
        sel = (ap << 24) | (reg & 0xF0)
        if sel != self.select:
            self.dp_write(0x8, sel)
            self.select = sel

    def ap_read(self, ap, reg):
        self._select(ap, reg)
        self._scan(IR_APACC, 1, reg & 0xC)
        return self._rdbuff()

    def ap_write(self, ap, reg, v):
        self._select(ap, reg)
        self._scan(IR_APACC, 0, reg & 0xC, v)
        self._rdbuff()

    def power_up(self):
        self.dp_write(0x4, 0x50000032)       # CSYSPWRUPREQ|CDBGPWRUPREQ, clear sticky flags
        for _ in range(100):
            if self.dp_read(0x4) & 0xA0000000 == 0xA0000000:
                break
            time.sleep(0.01)
        else:
            raise IOError("DAP: debug/system power-up not acknowledged")
        csw = self.ap_read(AHB_AP, 0x00)
        self.ap_write(AHB_AP, 0x00, (csw & ~0x37) | 0x02)   # 32-bit, no address increment

    def check(self, where):
        cs = self.dp_read(0x4)
        if cs & 0x20:                         # STICKYERR: an AHB-AP access faulted
            self.dp_write(0x4, 0x50000032)
            raise IOError("DAP: bus error during %s (CTRL/STAT 0x%08x)" % (where, cs))

    def read32(self, addr):
        self.ap_write(AHB_AP, 0x04, addr)
        return self.ap_read(AHB_AP, 0x0C)

    def write32(self, addr, v):
        self.ap_write(AHB_AP, 0x04, addr)
        self.ap_write(AHB_AP, 0x0C, v & 0xFFFFFFFF)


def ps7_ops(path, section):
    """Register operations of one ps7_init.tcl proc (mask_write / mwr /
    mask_poll / mask_delay -- the only commands its data procs use)."""
    m = re.search(r"^proc %s \{\} \{\n(.*?)^\}" % re.escape(section), path.read_text(), re.M | re.S)
    if not m:
        raise ValueError("%s: no proc %s" % (path, section))
    ops = []
    for line in m.group(1).splitlines():
        tok = line.split()
        if not tok or tok[0].startswith("#"):
            continue
        if tok[0] == "mwr":
            tok = [t for t in tok if t != "-force"]
        if tok[0] not in ("mask_write", "mwr", "mask_poll", "mask_delay"):
            raise ValueError("%s: unsupported command in %s: %s" % (path, section, line.strip()))
        ops.append((tok[0], [int(t, 16) for t in tok[1:]]))
    return ops


def silicon_suffix(path, mctrl):
    """Mirror ps7_init.tcl's choice between its _1_0 / _2_0 / _3_0 procs."""
    ver = dict(re.findall(r'set PCW_SILICON_VER_(\d_0) "(0x\w+)"', path.read_text()))
    v = mctrl >> 28
    if v == int(ver["1_0"], 16):
        return "1_0"
    if v == int(ver["2_0"], 16):
        return "2_0"
    return "3_0"


def run_ps7(dap, ops, where):
    for op, a in ops:
        if op == "mask_write":
            addr, mask, val = a
            cur = 0 if mask == 0xFFFFFFFF else dap.read32(addr)
            dap.write32(addr, (cur & ~mask) | (val & mask))
        elif op == "mwr":
            dap.write32(a[0], a[1])
        elif op == "mask_poll":
            deadline = time.time() + 2.0
            while not dap.read32(a[0]) & a[1]:
                if time.time() > deadline:
                    raise IOError("%s: mask_poll 0x%08x & 0x%08x timed out" % (where, a[0], a[1]))
        elif op == "mask_delay":
            time.sleep(a[1] / 1000.0)        # milliseconds; host time instead of the global timer
    dap.check(where)


def elf_segments(path):
    """(address, bytes) of each loadable segment of a 32-bit little-endian ELF."""
    data = path.read_bytes()
    if data[:4] != b"\x7fELF" or data[4] != 1 or data[5] != 1:
        raise ValueError("%s: not a 32-bit little-endian ELF" % path)
    entry, phoff = struct.unpack_from("<II", data, 24)
    phentsize, phnum = struct.unpack_from("<HH", data, 42)
    segs = []
    for i in range(phnum):
        p_type, p_off, _, p_paddr, p_filesz = struct.unpack_from("<5I", data, phoff + i * phentsize)
        if p_type == 1 and p_filesz:
            segs.append((p_paddr, data[p_off:p_off + p_filesz]))
    return entry, segs


def slcr_unlocked(dap, fn):
    dap.write32(SLCR_UNLOCK, 0xDF0D)
    try:
        fn()
    finally:
        dap.write32(SLCR_LOCK, 0x767B)


def program_pl(loader, bit):
    print("== programming the PL: %s" % bit.relative_to(REPO))
    r = subprocess.run([loader, "-b", "arty_z7_20", str(bit)], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("openFPGALoader failed:\n%s%s" % (r.stdout, r.stderr))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--bit", type=Path, default=OUT / "arty_z7_ps_top.bit")
    ap.add_argument("--ps7-init", type=Path, default=OUT / "ps7_init.tcl")
    ap.add_argument("--elf", type=Path, default=REPO / "build/arty_z7_uart_fwd/uart_fwd.elf")
    ap.add_argument("--no-bit", action="store_true", help="skip programming the PL")
    ap.add_argument("--openfpgaloader", default="openFPGALoader")
    ap.add_argument("--url", default="ftdi://ftdi:2232/1",
                    help="pyftdi URL of the board's JTAG channel (interface A)")
    args = ap.parse_args()

    for what, path, hint in (("bitstream", args.bit, "run scripts/vivado/build_arty_z7_ps.tcl"),
                             ("ps7_init", args.ps7_init, "run scripts/vivado/build_arty_z7_ps.tcl"),
                             ("program", args.elf, "run `make -C sw/arty_z7_uart_fwd`")):
        if (what != "bitstream" or not args.no_bit) and not path.exists():
            sys.exit("%s not found: %s (%s first)" % (what, path, hint))
    entry, segs = elf_segments(args.elf)
    if entry != 0 or any(a < 0 or a + len(b) > OCM_TOP or a % 4 for a, b in segs):
        sys.exit("%s: must be linked for OCM at 0x0 with entry 0x0" % args.elf)

    if not args.no_bit:
        loader = shutil.which(args.openfpgaloader)
        if not loader:
            sys.exit("openFPGALoader not found (brew install openfpgaloader, or --openfpgaloader PATH)")
        program_pl(loader, args.bit)

    try:
        jtag = Jtag(args.url)
    except Exception as e:  # noqa: BLE001 - pyusb/pyftdi raise several types
        sys.exit("cannot open the board's JTAG channel (%s): %s\nIs another program using it? "
                 "Stop `openFPGALoader --xvc` (run_vivado.sh) first." % (args.url, e))
    try:
        jtag.reset()
        ids = jtag.dr([1] * 64)
        pl_id, dap_id = to_int(ids[0:32]), to_int(ids[32:64])
        if pl_id & 0x0FFFFFFF != PL_IDCODE or dap_id != DAP_IDCODE:
            sys.exit("unexpected JTAG chain 0x%08x 0x%08x (expected xc7z020 + ARM DAP)" % (pl_id, dap_id))
        dap = Dap(jtag)
        dap.power_up()
        t0 = time.time()

        print("== holding CPU0 in reset")
        slcr_unlocked(dap, lambda: dap.write32(A9_CPU_RST_CTRL, dap.read32(A9_CPU_RST_CTRL) | 0x11))

        mctrl = dap.read32(DEVCFG_MCTRL)
        try:
            dap.check("the first PS register accesses")
        except IOError:
            # Every AHB access faults (even before ps7_init): the BootROM keeps the ARM DAP
            # locked after a failed SD/QSPI boot. A wrong silicon version would follow.
            sys.exit("the ARM debug port cannot reach the PS (every access faults): the BootROM keeps it\n"
                     "locked after a failed SD/QSPI boot. Put jumper JP4 on JTAG, power the board off\n"
                     "completely (USB and any external supply), then run this again.")
        suffix = silicon_suffix(args.ps7_init, mctrl)
        print("== initialising the PS (ps7_init %s, DDR skipped): %s"
              % (suffix, args.ps7_init.relative_to(REPO)))
        for part in ("mio", "pll", "clock", "peripherals"):
            run_ps7(dap, ps7_ops(args.ps7_init, "ps7_%s_init_data_%s" % (part, suffix)), part)
        run_ps7(dap, ps7_ops(args.ps7_init, "ps7_post_config_%s" % suffix), "post_config")

        print("== loading the UART forwarder into OCM: %s" % args.elf.relative_to(REPO))
        for addr, blob in segs:
            blob += b"\0" * (-len(blob) % 4)
            words = struct.unpack("<%dI" % (len(blob) // 4), blob)
            for i, w in enumerate(words):
                dap.write32(addr + 4 * i, w)
            back = [dap.read32(addr + 4 * i) for i in range(len(words))]
            if list(words) != back:
                sys.exit("OCM read-back mismatch at 0x%08x" % addr)
        dap.check("loading the program")

        print("== starting CPU0")
        for cr in (UART0_CR, UART1_CR):      # TX/RX disabled; only the forwarder re-enables them
            dap.write32(cr, 0x28)
        def release():
            v = dap.read32(A9_CPU_RST_CTRL)
            dap.write32(A9_CPU_RST_CTRL, (v | 0x10) & ~0x01)   # out of reset, clock still stopped
            dap.write32(A9_CPU_RST_CTRL, v & ~0x11)            # clock on
        slcr_unlocked(dap, release)
        time.sleep(0.05)
        cr0, cr1 = dap.read32(UART0_CR), dap.read32(UART1_CR)
        dap.check("starting CPU0")
        if cr0 & 0x3C != 0x14 or cr1 & 0x3C != 0x14:
            sys.exit("CPU0 did not start the forwarder (UART0 CR 0x%x, UART1 CR 0x%x)" % (cr0, cr1))
        print("   forwarder running (UART0/UART1 enabled), PS bring-up took %.1f s" % (time.time() - t0))
    finally:
        jtag.close()

    print("\nDone. Host side: connect to the board's on-board USB serial (...1 interface), 115200 baud.")


if __name__ == "__main__":
    main()
