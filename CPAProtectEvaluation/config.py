"""Site configuration for the CPA evaluation: edit the lines marked <<< EDIT."""

# Every other module here declares this (acquire_cpa.py, hdf5_trace_store.py,
# mlkem_ref.py, tvla_analysis.py, web_gui.py) so that `X | Y` type hints work
# on the project's Python 3.9 (PEP 604 `|` on types needs 3.10+ at runtime;
# `from __future__ import annotations` defers every annotation to a string,
# so it's never actually evaluated) -- needed here too as soon as this file
# has its own annotated assignment (NTT_WINDOWS_CYCLES below).
from __future__ import annotations

# =============================================================================
# Oscilloscope (LAN)
# =============================================================================
#: <<< EDIT: placeholder. 192.0.2.0/24 is RFC 5737 "TEST-NET-1": reserved for
#: documentation, never routable, so nothing real can answer here by accident.
#: Replace with the oscilloscope's LAN address once you have it
#: (or pass --ip on the command line).
SCOPE_IP = "192.0.2.100"

#: Kept only so the scripts can tell that SCOPE_IP has not been edited yet.
SCOPE_PLACEHOLDER_IP = "192.0.2.100"

#: VISA resource template. VXI-11 (works with pyvisa-py, no vendor runtime):
#:   "TCPIP0::{ip}::INSTR"
#: Raw SCPI socket (most scopes listen on 5025; Rigol/Siglent use 5555):
#:   "TCPIP0::{ip}::5025::SOCKET"
SCOPE_RESOURCE = "TCPIP0::{ip}::INSTR"

#: <<< EDIT: waveform source. Keysight/Rigol/Siglent: "CHANnel1"; Tektronix: "CH1".
SCOPE_CHANNEL = "CHANnel1"

#: SCPI dialect, a key of scope.DIALECTS: "default" (Keysight InfiniiVision,
#: Rigol DS/MSO and close relatives) or "tektronix".
SCOPE_DIALECT = "default"

SCOPE_TIMEOUT_MS = 20000

# =============================================================================
# Board (UART bridge on the Arty A7)
# =============================================================================
#: <<< EDIT: the Arty enumerates two ports on one FTDI chip; the second is the
#: UART. macOS: /dev/cu.usbserial-XXXXB. `acquire_cpa.py ports` lists them.
SERIAL_PORT = "/dev/cu.usbserial-XXXXB"
BAUD = 115200

#: Core clock of the 206 MHz build (MMCM output, 100 MHz x 33/16).
CORE_MHZ = 206.25

# =============================================================================
# Where the three decryption BMULs sit, in core clock cycles after `start`.
# =============================================================================
#: ``BMUL s7 * s6 -> s3`` (acc for 1 and 2) of K-PKE.Decrypt, i.e. the three
#: products s_hat_i o u_hat_i. Taken from the instruction column of
#: sim_result/tvla/20261006_135545_decaps/raw/main/tvla_t.csv (each is 281
#: cycles: 128 words, one per 2 cycles, plus the 21-cycle pipeline). See
#: ATTACK_MODEL.md section 4.3. Regenerate after an RTL/microcode change by
#: running scripts/tvla_sim.py and reading the new cycle range for
#: "BMUL s7 * s6 -> s3" out of its raw/main/tvla_t.csv (there is no
#: automated regeneration command -- do this by hand, same as it was found).
BMUL_WINDOWS = [(1289, 1570), (2855, 3136), (4421, 4702)]

#: NTT (one per BMUL, computing u_hat_i before it) and the single INTT (after
#: the third BMUL, see ATTACK_MODEL.md section 4.1's microcode listing --
#: ADDR 235/239/243 = NTT, ADDR 247 = INTT) cycle ranges, same units as
#: BMUL_WINDOWS. Left unset (None) because -- unlike BMUL_WINDOWS -- these
#: have not actually been measured off a tvla_t.csv run yet; the Results
#: Viewer's window markers only draw what is here, never a guessed value, so
#: these stay None (and the NTT/INTT markers are simply omitted) until
#: someone runs scripts/tvla_sim.py and reads the real ranges out, the same
#: by-hand process BMUL_WINDOWS itself came from (see the comment above).
NTT_WINDOWS_CYCLES: list[tuple[int, int]] | None = None
INTT_WINDOW_CYCLES: tuple[int, int] | None = None

#: Trigger = trig_out (status_busy), broken out on Pmod JA pin 1 / pkg pin
#: G13 -- see hdl/xilinx/arty_a7/arty_a7_top.sv and constraints.xdc. Same
#: signal as LD4 (pin H5), kept as a fallback probe point if you'd rather
#: clip onto the LED than the Pmod header. Allow this many cycles of slack
#: when the analysis slices a window out of a trace, for the trigger/start
#: latency.
TRIGGER_SLACK_CYCLES = 40

# =============================================================================
# ML-KEM-768 core, data buffer
# =============================================================================
IN_BASE = 0x0000
OUT_BASE = 0x0E00

# =============================================================================
# CPA leakage model default (set from the GUI's "CPA Model" tab, or by hand)
# =============================================================================
#: Which leakage-model module cpa_attack.py / the GUI load by default. A
#: module qualifies as a model by exposing Q, K, TARGETS, RPREVS, decrypt()
#: and selftest() -- see mlkem_ref.py.
CPA_MODEL_MODULE = "mlkem_ref"

#: Default --target / --rprev for cpa_attack.py (R.TARGETS / R.RPREVS keys
#: in the module above). Matches what acquire_cpa.py's SyntheticScope
#: leaks, so the positive-control dry-run recovers the key out of the box.
CPA_TARGET = "word"
CPA_RPREV = "prev-word"
