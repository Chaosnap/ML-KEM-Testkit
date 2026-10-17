"""Oscilloscope access for trace acquisition.

The acquisition loop needs three things from an instrument: arm it, wait for
the board to trigger it, read the samples. Two drivers do that over VISA:

* :class:`LeCroyScope` -- Teledyne LeCroy MAUI scopes (HDO4000/6000/8000,
  WaveRunner, WavePro, WaveSurfer 3000z...) with LeCroy's own remote
  commands (``TRMD``, ``WAIT``, ``WF?`` with the binary WAVEDESC header),
  over VICP or LXI/VXI-11;
* :class:`VisaScope` -- generic SCPI ``:WAVeform:`` scopes (Keysight
  InfiniiVision, Rigol, Siglent) and Tektronix ``CURVe?``.

:func:`open_scope` picks one from the dialect name.

Analogue front-end settings (sample rate, memory depth, vertical scale,
trigger level, horizontal position) are a measurement decision made on the
bench with the waveform in view, so they are *not* driven from here. The
script records whatever the instrument reports back into the trace file.
"""

from __future__ import annotations

import re
import struct
import time
from abc import ABC, abstractmethod

import numpy as np

import config as C

#: Vendor-specific SCPI. ``data`` must return the raw samples of one capture.
DIALECTS = {
    "default": {  # Keysight InfiniiVision, Rigol DS/MSO, Siglent (close enough)
        "stop": ":STOP",
        "single": ":SINGle",
        "armed_query": ":TRIGger:STATus?",
        "done_values": ("STOP", "TD"),
        "source": ":WAVeform:SOURce {channel}",
        "format": ":WAVeform:FORMat BYTE",
        "points_mode": ":WAVeform:POINts:MODE RAW",
        "points": ":WAVeform:POINts {points}",
        "preamble": ":WAVeform:PREamble?",
        "data": ":WAVeform:DATA?",
        "datatype": "B",
    },
    "tektronix": {  # TDS / MDO: CURVe? with DATa: setup
        "stop": "ACQuire:STATE STOP",
        "single": "ACQuire:STOPAfter SEQuence;:ACQuire:STATE RUN",
        "armed_query": "ACQuire:STATE?",
        "done_values": ("0",),
        "source": "DATa:SOUrce {channel}",
        "format": "DATa:ENCdg RIBinary;:WFMOutpre:BYT_Nr 1",
        "points_mode": "",
        "points": "DATa:STARt 1;:DATa:STOP {points}",
        "preamble": "WFMOutpre?",
        "data": "CURVe?",
        "datatype": "b",
    },
}


#: Every dialect :func:`open_scope` accepts ("lecroy" is :class:`LeCroyScope`).
DIALECT_NAMES = ("lecroy",) + tuple(DIALECTS)


def resource_for(ip: str, transport: str = "") -> str:
    """VISA resource string for ``ip`` over ``transport`` (a key of config.SCOPE_RESOURCES)."""
    transport = transport or C.SCOPE_TRANSPORT
    if transport not in C.SCOPE_RESOURCES:
        raise ValueError("unknown scope transport %r (one of: %s)" % (transport, ", ".join(C.SCOPE_RESOURCES)))
    return C.SCOPE_RESOURCES[transport].format(ip=ip)


def open_scope(ip: str, channel: str = "", dialect: str = "", points: int = 0,
               timeout_ms: int = 0, transport: str = "") -> "Scope":
    """Open the scope at ``ip`` with the driver for ``dialect`` (defaults from config.py)."""
    dialect = dialect or C.SCOPE_DIALECT
    if dialect not in DIALECT_NAMES:
        raise ValueError("unknown scope dialect %r (one of: %s)" % (dialect, ", ".join(DIALECT_NAMES)))
    resource = resource_for(ip, transport)
    timeout_ms = int(timeout_ms or C.SCOPE_TIMEOUT_MS)
    if dialect == "lecroy":
        return LeCroyScope(resource, channel=channel or C.SCOPE_CHANNEL, points=points, timeout_ms=timeout_ms)
    return VisaScope(resource, channel=channel or C.SCOPE_CHANNEL, points=points, dialect=dialect,
                     timeout_ms=timeout_ms)


def _open_visa(resource: str, timeout_ms: int):
    """``(ResourceManager, instrument)`` for ``resource``, with actionable errors."""
    try:
        import pyvisa
    except ImportError:
        raise SystemExit("pyvisa is missing: uv sync --extra scope")
    rm = pyvisa.ResourceManager()
    try:
        dev = rm.open_resource(resource)
    except Exception as e:  # noqa: BLE001
        hint = "check SCOPE_IP in config.py, or pass --ip"
        if resource.upper().startswith("VICP"):
            hint = ("VICP needs the pyvicp package (uv sync --extra scope); or set the scope's "
                    "Utilities > Remote to LXI (VXI11) and choose the VXI-11 transport")
        raise SystemExit("cannot open %s: %s\n(%s)" % (resource, e, hint))
    dev.timeout = timeout_ms
    return rm, dev


def parse_sample_rate(preamble: str) -> float:
    """Sample rate (Sa/s) from a waveform preamble, or 0.0 if unknown."""
    m = re.search(r"XINCR\s+([0-9.eE+-]+)", preamble, re.I)  # Tektronix
    if m:
        xinc = float(m.group(1))
    else:  # Keysight / Rigol: fmt,type,points,count,xincrement,xorigin,...
        parts = [p.strip() for p in preamble.split(",")]
        try:
            xinc = float(parts[4])
        except (IndexError, ValueError):
            return 0.0
    return 1.0 / xinc if xinc > 0 else 0.0


class Scope(ABC):
    samples: int = 0

    @abstractmethod
    def arm(self) -> None: ...

    @abstractmethod
    def read(self, timeout: float = 10.0) -> np.ndarray: ...

    def info(self) -> dict:
        return {"id": type(self).__name__, "samples": self.samples}

    def triggered(self) -> bool | None:
        """Has the armed acquisition triggered? None: this driver cannot tell."""
        return None

    def sample_rate(self) -> float:
        return 0.0

    def record_start_s(self) -> float | None:
        """Time of the first sample relative to the trigger, from the last capture
        (negative = pre-trigger data). None: this driver cannot tell."""
        return None

    def close(self) -> None:
        pass


class VisaScope(Scope):
    """A VISA/SCPI oscilloscope on the LAN (or USB)."""

    def __init__(self, resource: str, channel: str = C.SCOPE_CHANNEL, points: int = 0,
                 dialect: str = C.SCOPE_DIALECT, timeout_ms: int = C.SCOPE_TIMEOUT_MS):
        self.rm, self.dev = _open_visa(resource, timeout_ms)
        if "SOCKET" in resource.upper():
            self.dev.read_termination = "\n"
            self.dev.write_termination = "\n"
        self.resource, self.channel, self.dialect = resource, channel, dialect
        self.d = DIALECTS[dialect]
        self.samples = points  # 0 = whatever the scope's memory depth gives
        self._idn = self.dev.query("*IDN?").strip()
        self.preamble = ""

    def configure(self) -> None:
        """Select the waveform source and transfer format (nothing analogue)."""
        d = self.d
        for cmd in (d["stop"], d["source"].format(channel=self.channel), d["format"], d["points_mode"]):
            if cmd:
                self.dev.write(cmd)
        if self.samples:
            self.dev.write(d["points"].format(points=self.samples))
        try:
            self.preamble = self.dev.query(d["preamble"]).strip()
        except Exception:  # noqa: BLE001 - informational only
            self.preamble = ""

    def arm(self) -> None:
        self.dev.write(self.d["single"])
        try:  # make sure the scope is really armed before the board runs
            self.dev.query("*OPC?")
        except Exception:  # noqa: BLE001
            pass

    def triggered(self) -> bool:
        """One trigger-state query, no waiting (True once the single acquisition is done)."""
        state = self.dev.query(self.d["armed_query"]).strip().upper()
        return any(state.startswith(v) for v in self.d["done_values"])

    def read(self, timeout: float = 10.0) -> np.ndarray:
        d, deadline = self.d, time.time() + timeout
        while True:
            state = self.dev.query(d["armed_query"]).strip().upper()
            if any(state.startswith(v) for v in d["done_values"]):
                break
            if time.time() > deadline:
                raise TimeoutError("scope did not trigger within %.1f s (state %s): check trigger "
                                   "source/level and that the board is running" % (timeout, state))
        raw = self.dev.query_binary_values(d["data"], datatype=d["datatype"], container=np.array)
        try:  # the record as captured: the sample rate follows timebase changes made on the bench
            self.preamble = self.dev.query(d["preamble"]).strip()
        except Exception:  # noqa: BLE001 - keep the previous one
            pass
        return np.asarray(raw)

    def sample_rate(self) -> float:
        return parse_sample_rate(self.preamble)

    def info(self) -> dict:
        return {"id": self._idn, "resource": self.resource, "channel": self.channel,
                "dialect": self.dialect, "samples": self.samples, "preamble": self.preamble}

    def close(self) -> None:
        try:
            self.dev.close()
            self.rm.close()
        except Exception:  # noqa: BLE001
            pass


# =============================================================================
# Teledyne LeCroy (MAUI)
# =============================================================================
#: WAVEDESC fields used here: name -> (byte offset, struct code), from the
#: LECROY_2_3 template (``TMPL?`` on the scope prints the full template).
_WAVEDESC = {
    "comm_type": (32, "h"),          # 0 = byte, 1 = word samples
    "comm_order": (34, "h"),         # 0 = big-endian, 1 = little-endian
    "wave_descriptor": (36, "i"),    # lengths in bytes of the blocks that follow
    "user_text": (40, "i"),
    "trigtime_array": (48, "i"),
    "ris_time_array": (52, "i"),
    "res_array1": (56, "i"),
    "wave_array_1": (60, "i"),
    "wave_array_2": (64, "i"),
    "wave_array_count": (116, "i"),
    "vertical_gain": (156, "f"),     # volts = gain * code - offset
    "vertical_offset": (160, "f"),
    "nominal_bits": (172, "h"),
    "horiz_interval": (176, "f"),    # seconds per sample
    "horiz_offset": (180, "d"),      # time of the first sample relative to the trigger
}
_WAVEDESC_LEN = 346


def parse_wavedesc(block: bytes) -> dict:
    """Decode a LeCroy ``WF?`` block: the WAVEDESC fields above, plus ``samples``
    (the first data array as an int8/int16 numpy array, or None if absent)."""
    i = block.find(b"WAVEDESC")
    if i < 0 or len(block) < i + _WAVEDESC_LEN:
        raise ValueError("not a LeCroy waveform: no WAVEDESC header (%d bytes)" % len(block))
    block = block[i:]
    end = "<" if block[34] == 1 else ">"   # COMM_ORDER, readable in either byte order
    d = {k: struct.unpack_from(end + code, block, off)[0] for k, (off, code) in _WAVEDESC.items()}
    d["instrument"] = block[76:92].split(b"\0")[0].decode("ascii", "replace").strip()
    start = d["wave_descriptor"] + d["user_text"] + d["trigtime_array"] + d["ris_time_array"] + d["res_array1"]
    n_bytes = d["wave_array_1"]
    if n_bytes and start + n_bytes > len(block):
        raise ValueError("truncated LeCroy waveform: %d of %d data bytes" % (len(block) - start, n_bytes))
    dt = np.dtype(end + ("i2" if d["comm_type"] == 1 else "i1"))
    d["samples"] = np.frombuffer(block, dtype=dt, count=n_bytes // dt.itemsize, offset=start) \
        .astype(dt.newbyteorder("=")) if n_bytes else None
    return d


class LeCroyScope(Scope):
    """A Teledyne LeCroy MAUI oscilloscope (e.g. HDO6104) over VICP or LXI/VXI-11.

    Uses the legacy remote commands every MAUI scope understands:
    ``CHDR OFF`` / ``CFMT DEF9,WORD,BIN`` / ``CORD LO`` / ``WFSU`` once, then per
    trace ``TRMD SINGLE`` (arm) -> board runs -> ``WAIT`` + ``*OPC?`` ->
    ``TRMD?`` (``STOP`` = the single acquisition happened) -> ``<ch>:WF? ALL``.
    Samples are the raw ADC codes (16-bit words; 12 significant bits on an HDO)
    -- CPA and TVLA are invariant to the volts scale, which is recorded in
    :meth:`info` (``vertical_gain`` / ``vertical_offset``) anyway.

    :param channel: ``C1``..``C4`` (or a math/zoom trace such as ``F1``).
    """

    CHANNEL_RE = re.compile(r"^(C[1-8]|F[1-9]|F1[0-2]|M[1-4]|Z[1-8])$")

    def __init__(self, resource: str, channel: str = "C1", points: int = 0,
                 timeout_ms: int = C.SCOPE_TIMEOUT_MS, dev=None):
        channel = channel.strip().upper()
        if not self.CHANNEL_RE.match(channel):
            raise ValueError("LeCroy channel %r: use C1..C4 (math traces F1.., memories M1..)" % channel)
        if dev is None:
            self.rm, self.dev = _open_visa(resource, timeout_ms)
        else:  # tests: a stand-in instrument
            self.rm, self.dev = None, dev
        self.resource, self.channel, self.dialect = resource, channel, "lecroy"
        self.samples = points  # 0 = the whole record at the scope's current settings
        self._idn = self._q("*IDN?")
        self.desc: dict = {}
        self.settings: dict = {}

    # --- SCPI helpers ---------------------------------------------------------
    def _w(self, cmd: str) -> None:
        self.dev.write(cmd)

    def _q(self, cmd: str) -> str:
        return self.dev.query(cmd).strip()

    def _waveform(self, part: str) -> dict:
        raw = self.dev.query_binary_values("%s:WF? %s" % (self.channel, part), datatype="B",
                                           container=bytes)
        d = parse_wavedesc(bytes(raw))
        self.desc = {k: v for k, v in d.items() if k != "samples"}
        return d

    # --- setup ----------------------------------------------------------------
    def configure(self) -> None:
        """Response and transfer format, and what the front panel is set to (nothing analogue)."""
        self._w("CHDR OFF")                 # COMM_HEADER: bare responses
        self._w("CFMT DEF9,WORD,BIN")       # COMM_FORMAT: #9 block, 16-bit samples
        self._w("CORD LO")                  # COMM_ORDER: little-endian
        self._w("WFSU SP,0,NP,%d,FP,0,SN,0" % int(self.samples))  # every point (or the first NP)
        self.settings = {}
        for key, cmd in (("trigger", "TRSE?"), ("trigger_mode", "TRMD?"), ("timebase_s_div", "TDIV?"),
                         ("volts_div", "%s:VDIV?" % self.channel), ("coupling", "%s:CPL?" % self.channel)):
            try:
                self.settings[key] = self._q(cmd)
            except Exception:  # noqa: BLE001 - informational only
                pass
        src = re.search(r"SR,(C\d|EX10|EX5|EX|LINE)", self.settings.get("trigger", ""), re.I)
        if src:
            try:
                self.settings["trigger_level_v"] = self._q("%s:TRLV?" % src.group(1).upper())
            except Exception:  # noqa: BLE001
                pass
        try:  # the last acquisition's descriptor: sample rate and trigger position before a capture
            self._waveform("DESC")
        except Exception:  # noqa: BLE001 - nothing acquired on this channel yet
            pass

    # --- capture --------------------------------------------------------------
    def arm(self) -> None:
        self._w("TRMD SINGLE")
        self._q("*OPC?")

    def triggered(self) -> bool:
        """One trigger-mode query, no waiting: a single acquisition ends in STOP."""
        return self._q("TRMD?").upper().startswith("STOP")

    def read(self, timeout: float = 10.0) -> np.ndarray:
        old = self.dev.timeout
        self.dev.timeout = max(int(old or 0), int((timeout + 5.0) * 1000))
        try:
            self._w("WAIT %g" % timeout)    # holds the parser until the acquisition is done
            self._q("*OPC?")
        finally:
            self.dev.timeout = old
        if not self.triggered():
            raise TimeoutError("scope did not trigger within %.1f s (trigger mode %s): check trigger "
                               "source/level and that the board is running" % (timeout, self._q("TRMD?")))
        d = self._waveform("ALL")
        if d["samples"] is None:
            raise RuntimeError("%s returned no samples: is the channel switched on?" % self.channel)
        return d["samples"]

    def sample_rate(self) -> float:
        dt = float(self.desc.get("horiz_interval") or 0.0)
        return 1.0 / dt if dt > 0 else 0.0

    def record_start_s(self) -> float | None:
        return float(self.desc["horiz_offset"]) if "horiz_offset" in self.desc else None

    def info(self) -> dict:
        out = {"id": self._idn, "resource": self.resource, "channel": self.channel, "dialect": self.dialect,
               "samples": self.samples}
        out.update(self.settings)
        for k in ("nominal_bits", "vertical_gain", "vertical_offset", "wave_array_count"):
            if k in self.desc:
                out[k] = self.desc[k]
        start = self.record_start_s()
        if start is not None:
            out["record_start_s"] = start
        return out

    def close(self) -> None:
        try:
            self.dev.close()
            if self.rm is not None:
                self.rm.close()
        except Exception:  # noqa: BLE001
            pass


def list_resources() -> list:
    """VISA resources currently visible, as ``(resource, identification)``."""
    try:
        import pyvisa
    except ImportError:
        raise SystemExit("pyvisa is missing: uv sync --extra scope")
    rm = pyvisa.ResourceManager()
    out = []
    for res in rm.list_resources():
        try:
            dev = rm.open_resource(res)
            dev.timeout = 2000
            idn = dev.query("*IDN?").strip()
            dev.close()
        except Exception as e:  # noqa: BLE001 - a busy instrument is still worth listing
            idn = "(%s)" % type(e).__name__
        out.append((res, idn))
    rm.close()
    return out
