"""Site configuration for the CPA evaluation: edit the lines marked <<< EDIT."""

# Every other module here declares this (acquire_cpa.py, hdf5_trace_store.py,
# mlkem_ref.py, tvla_analysis.py, web_gui.py) so that `X | Y` type hints work
# on the project's Python 3.9 (PEP 604 `|` on types needs 3.10+ at runtime;
# `from __future__ import annotations` defers every annotation to a string,
# so it's never actually evaluated) -- needed here too as soon as this file
# has its own annotated assignment (NTT_WINDOWS_CYCLES below).
from __future__ import annotations

# =============================================================================
# Oscilloscope (LAN) -- Teledyne LeCroy HDO6104 (1 GHz, 2.5 GS/s, 12-bit)
# =============================================================================
#: <<< EDIT: the HDO6104's address, as shown on the scope under
#: Utilities > Utilities Setup > Remote > TCP/IP > "IP Address(es)"
#: ("0.0.0.0, 192.168.0.174" on 2026-10-16: the second one is the connected
#: port). If the scope gets its address by DHCP it can change: check that
#: screen again. The Mac must be on the same subnet (192.168.0.x).
SCOPE_IP = "192.168.0.174"

#: Kept only so the scripts can tell that SCOPE_IP has not been edited yet.
#: 192.0.2.0/24 is RFC 5737 "TEST-NET-1": documentation only, never routable.
SCOPE_PLACEHOLDER_IP = "192.0.2.100"

#: <<< EDIT: how the host reaches the scope, a key of SCOPE_RESOURCES. Must
#: match the scope's Utilities > Remote "Control from" button:
#:   "vicp"   TCPIP (VICP) -- LeCroy's protocol; needs pyvicp (uv sync --extra scope)
#:   "vxi11"  LXI (VXI11)  -- LeCroy, Keysight, Rigol, Siglent, Tektronix; pyvisa-py only
#:   "socket" raw SCPI on port 5025 (Rigol / Siglent use 5555) -- not LeCroy
SCOPE_TRANSPORT = "vicp"

#: VISA resource template per transport.
SCOPE_RESOURCES = {
    "vicp": "VICP::{ip}::INSTR",
    "vxi11": "TCPIP0::{ip}::INSTR",
    "socket": "TCPIP0::{ip}::5025::SOCKET",
}

#: <<< EDIT: waveform source (the measured power signal, NOT the trigger).
#: LeCroy: "C1".."C4" (on the 2026-10-16 bench screen C2 was the 20 mV/div DC50
#: trace and C4 the trigger source). Keysight/Rigol/Siglent: "CHANnel1"; Tektronix: "CH1".
SCOPE_CHANNEL = "C2"

#: Driver, a name in scope.DIALECT_NAMES: "lecroy" (Teledyne LeCroy MAUI:
#: HDO / WaveRunner / WavePro / WaveSurfer), "default" (Keysight
#: InfiniiVision, Rigol DS/MSO and close relatives) or "tektronix".
SCOPE_DIALECT = "lecroy"

SCOPE_TIMEOUT_MS = 20000

# =============================================================================
# Board (UART bridge; Arty A7, Arty Z7 or SAKURA-X -- see README.md)
# =============================================================================
#: <<< EDIT: the Arty enumerates two ports on one FTDI chip; the second (...1)
#: is the UART. `acquire_cpa.py ports` lists them (* = recommended); the GUI
#: picks the right one by itself. SAKURA-X: the port of the external 2.5 V
#: USB-UART adapter on CN8 -- NOT the board's own USB (CN15), which belongs
#: to the Spartan-6 control FPGA.
SERIAL_PORT = "/dev/cu.usbserial-XXXXB"
BAUD = 115200

#: Core clock of the 206 MHz build (MMCM output, 100 MHz x 33/16).
CORE_MHZ = 206.25

# =============================================================================
# Where the three decryption BMULs sit, in core clock cycles after `start`.
# =============================================================================
#: ``BMUL s7 * s6 -> s3`` (acc for 1 and 2) of K-PKE.Decrypt, i.e. the three
#: products s_hat_i o u_hat_i. Measured in a cycle-accurate RTL simulation of
#: Decaps (each is 281 cycles: 128 words, one per 2 cycles, plus the 21-cycle
#: pipeline); see docs/ATTACK_MODEL.md section 4.3. After an RTL/microcode
#: change, re-measure the cycle range of "BMUL s7 * s6 -> s3" in simulation
#: (testbench/core_sim) and update these by hand.
BMUL_WINDOWS = [(1289, 1570), (2855, 3136), (4421, 4702)]

#: NTT (one per BMUL, computing u_hat_i before it) and the single INTT (after
#: the third BMUL, see docs/ATTACK_MODEL.md section 4.1's microcode listing --
#: ADDR 235/239/243 = NTT, ADDR 247 = INTT) cycle ranges, same units as
#: BMUL_WINDOWS. Left unset (None) because -- unlike BMUL_WINDOWS -- these
#: have not been measured yet; the GUI's window markers only draw what is
#: here, never a guessed value, so these stay None (and the NTT/INTT markers
#: are simply omitted) until they are measured the same way BMUL_WINDOWS was
#: (see the comment above).
NTT_WINDOWS_CYCLES: list[tuple[int, int]] | None = None
INTT_WINDOW_CYCLES: tuple[int, int] | None = None

#: Trigger = trig_out (STATUS.busy): Pmod JA pin 1 / pkg pin G13 on the Arty
#: A7, Pmod JA pin 3 / Y16 on the Arty Z7 (3.3 V logic), CN8 pin 3 / N16 on
#: the SAKURA-X (2.5 V logic: trigger level ~1.25 V, not 1.6 V or 2.5 V)
#: -- see the boards' constraints.xdc.
#: No LED carries it: the status LEDs are frozen while the core is busy.
#: Allow this many samples of slack when the analysis slices a window out of
#: a trace, for the trigger/start latency.
TRIGGER_SLACK_CYCLES = 40

# =============================================================================
# ML-KEM-768 core, data buffer
# =============================================================================
IN_BASE = 0x0000
OUT_BASE = 0x0E00

# =============================================================================
# CPA leakage model default (set from the GUI's Analysis page, or by hand)
# =============================================================================
#: The leakage model cpa_attack.py uses without --model: a file in models/
#: (one per model; `cpa_attack.py --list-models` lists them). The default is
#: HD(R_prev, {c1, c0}) of the 24-bit RAM word -- see docs/ATTACK_MODEL.md.
CPA_MODEL = "hd_basemul_word"

#: Its R_prev (a choice that model accepts: zero / prev-word / acc).
CPA_RPREV = "prev-word"
