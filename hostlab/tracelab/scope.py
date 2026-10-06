"""Oscilloscope control for trace acquisition.

The campaign only needs three things from an instrument: arm it, wait for
the board to trigger it, read the captured samples. :class:`Scope` is that
interface; :class:`VisaScope` implements it over VISA/SCPI and
:class:`NullScope` fakes it so the whole pipeline can be exercised at the
desk before going to the bench.

SCPI is only loosely standardised beyond IEEE 488.2, so :class:`VisaScope`
keeps the vendor-specific strings in :data:`DIALECTS` instead of hard-coding
one manufacturer. The defaults follow the ``:WAVeform:`` subset used by
Keysight InfiniiVision, Rigol DS/MSO and most Tektronix TDS/MDO series. If a
scope needs something else, add a dialect rather than editing the flow.

Acquisition settings (sample rate, memory depth, vertical scale, trigger
level and position) are deliberately *not* driven from here beyond the few
commands in :meth:`VisaScope.configure`. Setting up the analogue front end
is a measurement decision that belongs on the bench with the waveform in
view; the campaign records whatever the instrument reports back so the
settings end up in ``meta.json`` with the data.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod

import numpy as np

#: Vendor-specific SCPI. ``read`` must return the raw samples of one capture.
DIALECTS = {
    # Keysight InfiniiVision / Rigol DS, and anything close enough to them.
    "default": {
        "stop": ":STOP",
        "single": ":SINGle",
        "armed_query": ":TRIGger:STATus?",
        "armed_values": ("WAIT", "RUN", "AUTO"),
        "done_values": ("STOP", "TD"),
        "source": ":WAVeform:SOURce {channel}",
        "format": ":WAVeform:FORMat BYTE",
        "points_mode": ":WAVeform:POINts:MODE RAW",
        "points": ":WAVeform:POINts {points}",
        "preamble": ":WAVeform:PREamble?",
        "data": ":WAVeform:DATA?",
        "datatype": "B",
    },
    # Tektronix TDS/MDO: CURVE? with DATa: setup.
    "tektronix": {
        "stop": "ACQuire:STATE STOP",
        "single": "ACQuire:STOPAfter SEQuence;:ACQuire:STATE RUN",
        "armed_query": "ACQuire:STATE?",
        "armed_values": ("1",),
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


class Scope(ABC):
    """Minimal instrument interface used by a campaign."""

    #: Samples returned by one capture.
    samples: int = 0

    @abstractmethod
    def arm(self) -> None:
        """Put the scope in single-shot mode, waiting for the trigger."""

    @abstractmethod
    def read(self, timeout: float = 10.0) -> np.ndarray:
        """Wait for the capture to complete and return its samples."""

    def info(self) -> dict:
        """Settings worth recording in the campaign metadata."""
        return {"id": type(self).__name__, "samples": self.samples}

    def close(self) -> None:
        pass

    def __enter__(self) -> "Scope":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class NullScope(Scope):
    """A scope that is not there: synthetic traces for pipeline testing.

    Lets the full campaign (vectors, board, storage, analysis) run without
    an instrument, so that a bench session starts from a flow that is known
    to work. By default it returns pure Gaussian noise, which is also a
    useful negative control: a TVLA over a ``NullScope`` campaign must
    *not* report leakage, and if it does the analysis is at fault.

    :param leak: amplitude of a synthetic, input-dependent component. Only
        for testing that the analysis *can* detect something; it is not a
        model of the core and says nothing about the real implementation.
    """

    def __init__(self, samples: int = 5000, noise: float = 8.0, leak: float = 0.0, seed: int = 0):
        self.samples = samples
        self.noise = noise
        self.leak = leak
        self._rng = np.random.default_rng(seed)
        self._input = b""

    def set_input(self, data: bytes) -> None:
        """Called by the campaign so that ``leak`` can depend on the input."""
        self._input = data

    def arm(self) -> None:
        pass

    def read(self, timeout: float = 10.0) -> np.ndarray:
        t = self._rng.normal(0.0, self.noise, self.samples)
        if self.leak and self._input:
            # A bump in the middle of the window whose height follows the
            # Hamming weight of a digest of the whole input. Hashing matters:
            # a Decaps input is dk||c and dk is identical in both classes, so
            # a model reading the first bytes would show no class difference
            # at all and the self-test would silently pass.
            import hashlib

            digest = hashlib.sha256(self._input).digest()[:4]
            hw = float(bin(int.from_bytes(digest, "little")).count("1"))
            centre = self.samples // 2
            width = max(8, self.samples // 50)
            x = np.arange(self.samples)
            t += self.leak * (hw - 16.0) * np.exp(-0.5 * ((x - centre) / width) ** 2)
        return t.astype(np.int16)

    def info(self) -> dict:
        return {
            "id": "NullScope (synthetic)",
            "samples": self.samples,
            "noise": self.noise,
            "leak": self.leak,
        }


class VisaScope(Scope):
    """A VISA/SCPI oscilloscope.

    :param resource: VISA resource string, e.g.
        ``USB0::0x0957::0x17A6::MY1234::INSTR`` or ``TCPIP0::192.168.1.5::INSTR``.
        :func:`list_resources` prints what is connected.
    :param channel: waveform source, e.g. ``CHANnel1`` (Keysight/Rigol) or
        ``CH1`` (Tektronix).
    :param points: memory depth to transfer per capture.
    :param dialect: key into :data:`DIALECTS`.
    """

    def __init__(
        self,
        resource: str,
        channel: str = "CHANnel1",
        points: int = 5000,
        dialect: str = "default",
        timeout_ms: int = 20000,
    ):
        try:
            import pyvisa
        except ImportError:  # pragma: no cover - depends on the environment
            raise SystemExit(
                "pyvisa is missing: run with `uv run python ...`, or "
                "install it with `python3 -m pip install pyvisa pyvisa-py`"
            )
        self.rm = pyvisa.ResourceManager()
        try:
            self.dev = self.rm.open_resource(resource)
        except Exception as e:
            raise SystemExit("cannot open %s: %s" % (resource, e))
        self.dev.timeout = timeout_ms
        self.resource = resource
        self.channel = channel
        self.samples = points
        self.d = DIALECTS[dialect]
        self.dialect = dialect
        self._idn = self._query("*IDN?")
        self.preamble = ""

    # --- SCPI helpers -----------------------------------------------------
    def _write(self, cmd: str) -> None:
        if cmd:
            self.dev.write(cmd)

    def _query(self, cmd: str) -> str:
        return self.dev.query(cmd).strip()

    # --- setup ------------------------------------------------------------
    def configure(self) -> None:
        """Select the waveform source and transfer format."""
        d = self.d
        self._write(d["stop"])
        self._write(d["source"].format(channel=self.channel))
        self._write(d["format"])
        self._write(d["points_mode"])
        self._write(d["points"].format(points=self.samples))
        try:
            self.preamble = self._query(d["preamble"])
        except Exception:  # noqa: BLE001 - preamble is informational only
            self.preamble = ""

    # --- capture ----------------------------------------------------------
    def arm(self) -> None:
        self._write(self.d["single"])
        # Make sure the instrument has processed it before the board runs,
        # otherwise the trigger can arrive while the scope is still stopped.
        try:
            self._query("*OPC?")
        except Exception:  # noqa: BLE001 - not every scope answers during arming
            pass

    def read(self, timeout: float = 10.0) -> np.ndarray:
        d = self.d
        deadline = time.time() + timeout
        while True:
            state = self._query(d["armed_query"]).upper()
            if any(state.startswith(v) for v in d["done_values"]):
                break
            if time.time() > deadline:
                raise TimeoutError(
                    "scope did not trigger within %.1f s (state %s); check the "
                    "trigger source, level and that the board is running" % (timeout, state)
                )
        raw = self.dev.query_binary_values(
            d["data"], datatype=d["datatype"], container=np.array
        )
        return np.asarray(raw)

    def info(self) -> dict:
        return {
            "id": self._idn,
            "resource": self.resource,
            "channel": self.channel,
            "dialect": self.dialect,
            "samples": self.samples,
            "preamble": self.preamble,
        }

    def close(self) -> None:
        try:
            self.dev.close()
            self.rm.close()
        except Exception:  # noqa: BLE001 - closing must not fail a campaign
            pass


def list_resources() -> list:
    """VISA resources currently visible, as ``(resource, identification)``."""
    try:
        import pyvisa
    except ImportError:  # pragma: no cover
        raise SystemExit("pyvisa is missing: run with `uv run python ...`")
    rm = pyvisa.ResourceManager()
    out = []
    for res in rm.list_resources():
        idn = "?"
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
