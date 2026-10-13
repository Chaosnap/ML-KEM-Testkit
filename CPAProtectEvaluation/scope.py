"""Oscilloscope access for trace acquisition.

The acquisition loop needs three things from an instrument: arm it, wait for
the board to trigger it, read the samples. :class:`VisaScope` does that over
VISA/SCPI (adapted from ``hostlab/tracelab/scope.py`` in git history);
:class:`SyntheticScope` fakes it so the whole pipeline -- acquisition, HDF5
storage, analysis -- can be exercised before there is an instrument.

Analogue front-end settings (sample rate, memory depth, vertical scale,
trigger level, horizontal position) are a measurement decision made on the
bench with the waveform in view, so they are *not* driven from here. The
script records whatever the instrument reports back into the trace file.
"""

from __future__ import annotations

import re
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


def resource_for(ip: str) -> str:
    return C.SCOPE_RESOURCE.format(ip=ip)


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

    def set_context(self, ct: bytes) -> None:
        """Called before each capture (only the synthetic scope uses it)."""

    @abstractmethod
    def arm(self) -> None: ...

    @abstractmethod
    def read(self, timeout: float = 10.0) -> np.ndarray: ...

    def info(self) -> dict:
        return {"id": type(self).__name__, "samples": self.samples}

    def sample_rate(self) -> float:
        return 0.0

    def close(self) -> None:
        pass


class VisaScope(Scope):
    """A VISA/SCPI oscilloscope on the LAN (or USB)."""

    def __init__(self, resource: str, channel: str = C.SCOPE_CHANNEL, points: int = 0,
                 dialect: str = C.SCOPE_DIALECT, timeout_ms: int = C.SCOPE_TIMEOUT_MS):
        try:
            import pyvisa
        except ImportError:
            raise SystemExit("pyvisa is missing: pip install -r requirements.txt")
        self.rm = pyvisa.ResourceManager()
        try:
            self.dev = self.rm.open_resource(resource)
        except Exception as e:  # noqa: BLE001
            raise SystemExit("cannot open %s: %s\n(check SCOPE_IP in config.py, or pass --ip)"
                             % (resource, e))
        self.dev.timeout = timeout_ms
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


class SyntheticScope(Scope):
    """Fabricated traces for exercising the pipeline before an instrument exists.

    This is NOT a measurement and says nothing about the real core. It is
    for checking that acquisition, HDF5 storage and the CPA analysis script
    agree on byte order, indices and array shapes, by giving the analysis
    something it provably should (or should not) be able to break.

    - ``leak_mv = 0``: pure Gaussian noise. The CPA analysis must then report
      chance-level key ranks (~128 of 256) -- a negative control. If it
      reports a confident recovery here, the analysis has a bug (e.g. an
      input/trace index that leaks the answer by accident).
    - ``leak_mv > 0``: every sample at the BMUL word positions gets a bump
      proportional to HW(prev_word XOR word) for the *true* key, i.e. an
      idealised version of the HD(R_prev, X) hypothesis the attack scores
      against. CPA must then recover the true key with a small, known
      trace count -- a positive control, proving the analysis *can* detect
      the modelled leakage before it is pointed at real traces.
    """

    def __init__(self, samples: int, noise_mv: float = 5.0, leak_mv: float = 0.0, seed: int = 0):
        self.samples = samples
        self.noise_mv = noise_mv
        self.leak_mv = leak_mv
        self._rng = np.random.default_rng(seed)
        self._bump_centres: list[int] = []
        self._bump_values: np.ndarray | None = None

    def set_leak(self, centres: list[int], hw_values: np.ndarray) -> None:
        """Called by the acquisition script with where/how much to leak.

        ``centres``: sample index of each word's bump (same length as
        ``hw_values``). ``hw_values``: HW(prev XOR word) for the true key,
        one value per centre, already in [0, 24].
        """
        self._bump_centres = centres
        self._bump_values = hw_values

    def arm(self) -> None:
        pass

    def read(self, timeout: float = 10.0) -> np.ndarray:
        t = self._rng.normal(0.0, self.noise_mv, self.samples)
        if self.leak_mv and self._bump_values is not None:
            x = np.arange(self.samples)
            for centre, hw in zip(self._bump_centres, self._bump_values):
                width = 2.0
                t += self.leak_mv * (float(hw) - 12.0) * np.exp(-0.5 * ((x - centre) / width) ** 2)
        return t.astype(np.float32)

    def info(self) -> dict:
        return {"id": "SyntheticScope (NOT a measurement)", "samples": self.samples,
                "noise_mv": self.noise_mv, "leak_mv": self.leak_mv}

    def sample_rate(self) -> float:
        return float(self.samples) * C.CORE_MHZ * 1e6 / self.samples  # placeholder, informational


def list_resources() -> list:
    """VISA resources currently visible, as ``(resource, identification)``."""
    try:
        import pyvisa
    except ImportError:
        raise SystemExit("pyvisa is missing: pip install -r requirements.txt")
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
