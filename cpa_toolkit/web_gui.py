#!/usr/bin/env python3
"""CPA Toolkit web GUI: board + scope connection, test vectors, trace acquisition, CPA/TVLA analysis.

Served as a local web page from the Python standard library (no web
framework) plus the vendored Plotly.js in ``web_static/vendor/``:

    uv run python cpa_toolkit/web_gui.py              # http://127.0.0.1:8765
    uv run python cpa_toolkit/web_gui.py --port 8900
    uv run python cpa_toolkit/web_gui.py --no-browser

Binds to 127.0.0.1 only -- it drives real hardware (serial port, scope).

Tabs: Connection (board + scope) -> Test Vectors -> Trace Acquisition ->
CPA / TVLA Analysis, plus Advanced (raw registers / data buffer). Long work
(vector generation, board verification, acquisition, analysis) runs as
background jobs the page polls; connecting and disconnecting never block.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import threading
import time
import uuid
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

import numpy as np

import acquire_cpa as A
import config as C
import connection as CN
import cpa_attack as CA
import hdf5_trace_store as HS
import models as LM
import scope as S
import testvectors as TV
import tvla_analysis as TVA
import uart_target as T
from gui_config_io import save_config_values

STATIC_DIR = Path(__file__).with_name("web_static")
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                 ".js": "application/javascript; charset=utf-8", ".svg": "image/svg+xml",
                 ".png": "image/png"}
DATA_DIR = Path(A.ROOT) / "data"
TRACES_DIR = DATA_DIR / "traces"
ANALYSIS_DIR = DATA_DIR / "analysis"
VECTORS_DIR = TV.VECTORS_DIR


# --- log -----------------------------------------------------------------------
_log: list[str] = []
_log_lock = threading.Lock()


def log(msg: str) -> None:
    with _log_lock:
        _log.append("[%s] %s" % (time.strftime("%H:%M:%S"), msg))
        del _log[:-500]


# --- shared state ----------------------------------------------------------------
board = CN.Managed("board", log)
scope_conn = CN.Managed("scope", log)
_lock = threading.Lock()
# `busy`: job_id of the running job that owns the board + scope (acquisition,
# board verification), or None. Nothing else may talk to the board meanwhile.
_state = {"busy": None}

_jobs_lock = threading.Lock()
_jobs: dict[str, dict] = {}
_live_lock = threading.Lock()
_live_cond = threading.Condition(_live_lock)   # notified on every new trace (live-preview long poll)
_live: dict[str, dict] = {}   # job_id -> running trace statistics for the live preview

#: Longest a /api/acquire/live request waits for a new trace before answering "unchanged".
LIVE_WAIT_MAX_S = 2.0


def _new_job(kind: str, **extra) -> str:
    job_id = uuid.uuid4().hex[:12]
    with _jobs_lock:
        _jobs[job_id] = {"job_id": job_id, "kind": kind, "status": "running", "progress": 0, "total": 0,
                         "message": "starting...", "result": None, "error": None,
                         "started_unix": time.time(), **extra}
    return job_id


def _update_job(job_id: str, **kv) -> None:
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id].update(kv)


def _get_job(job_id: str) -> dict:
    with _jobs_lock:
        if job_id not in _jobs:
            raise ValueError("no such job: %s" % job_id)
        return dict(_jobs[job_id])


def _stop_requested(job_id: str) -> bool:
    with _jobs_lock:
        return bool(_jobs.get(job_id, {}).get("stop_requested"))


def _json_default(o):
    """numpy scalars/arrays (h5py returns numpy types even for plain attrs)."""
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    return str(o)


def _busy() -> str | None:
    with _lock:
        return _state["busy"]


def _claim_hardware(job_id: str) -> None:
    with _lock:
        if _state["busy"]:
            raise ValueError("another job (%s) is using the board" % _state["busy"])
        _state["busy"] = job_id


def _release_hardware(job_id: str) -> None:
    with _lock:
        if _state["busy"] == job_id:
            _state["busy"] = None


@dataclass
class Download:
    """A route result sent as a file instead of JSON: ``data`` in memory, or
    ``file`` streamed from disk in chunks (campaign files can be large)."""
    filename: str
    content_type: str
    data: bytes = b""
    file: Path | None = None


def envelope(y: np.ndarray, max_points: int) -> tuple[np.ndarray, np.ndarray]:
    """Peak-preserving decimation: per bin, its min and its max (in sample order).

    A plain stride would drop narrow spikes -- exactly what a power trace's
    interesting features are -- so each bin contributes both extremes.
    """
    n = int(y.shape[-1])
    if n <= max_points:
        return np.arange(n), y
    bins = max(1, max_points // 2)
    starts = np.linspace(0, n, bins + 1).astype(np.int64)[:-1]
    lo = np.minimum.reduceat(y, starts)
    hi = np.maximum.reduceat(y, starts)
    width = np.diff(np.append(starts, n))
    x = np.empty(2 * bins, dtype=np.int64)
    v = np.empty(2 * bins, dtype=np.float64)
    x[0::2], x[1::2] = starts, starts + np.maximum(width // 2, 0)
    v[0::2], v[1::2] = lo, hi
    return x, v


def _series(y: np.ndarray, rate: float, max_points: int) -> dict:
    """``{x, y}`` ready for Plotly: x in microseconds when the sample rate is known."""
    idx, val = envelope(np.asarray(y, dtype=np.float64), max_points)
    x = idx / rate * 1e6 if rate else idx
    return {"x": np.round(x, 6).tolist(), "y": np.round(val, 6).tolist()}


def _axis(rate: float, n_samples: int) -> dict:
    windows = A.window_sample_ranges(rate, n_samples)
    scale = (1e6 / rate) if rate else 1.0
    shown = [(a, b) for a, b in windows if b > a]  # windows past the trace end are not drawn
    return {"unit": "us" if rate else "sample", "sample_rate_hz": rate, "n_samples": n_samples,
            "bmul_windows": [[round(a * scale, 6), round(b * scale, 6)] for a, b in shown],
            "bmul_windows_samples": [[int(a), int(b)] for a, b in shown],
            "bmul_outside": len(windows) - len(shown),
            "duration_us": round(n_samples / rate * 1e6, 3) if rate else None,
            "needed_us": round((C.BMUL_WINDOWS[-1][1] + C.TRIGGER_SLACK_CYCLES) / C.CORE_MHZ, 2)}


# =============================================================================
# State, log
# =============================================================================
def api_state(_body: dict) -> dict:
    with _lock:
        busy = _state["busy"]
    return {
        "board": board.snapshot(), "scope": scope_conn.snapshot(), "busy": busy,
        "defaults": {"port": C.SERIAL_PORT, "baud": C.BAUD, "scope_ip": C.SCOPE_IP,
                     "scope_placeholder_ip": C.SCOPE_PLACEHOLDER_IP, "scope_channel": C.SCOPE_CHANNEL,
                     "scope_dialect": C.SCOPE_DIALECT, "scope_timeout_ms": C.SCOPE_TIMEOUT_MS,
                     "scope_transport": C.SCOPE_TRANSPORT, "scope_resources": C.SCOPE_RESOURCES,
                     "core_mhz": C.CORE_MHZ},
        "registers": sorted(T.REG),
    }


def api_log(body: dict) -> dict:
    since = int(body.get("since", 0))
    with _log_lock:
        return {"lines": _log[since:], "next": len(_log)}


# =============================================================================
# Board connection
# =============================================================================
def api_fpga_ports(_body: dict) -> dict:
    return {"ports": [{"device": d, "description": desc, "hint": hint, "recommended": rec}
                      for d, desc, hint, rec in T.list_ports()]}


#: Host-side link settings (Advanced page), applied to every board connection:
#: how long the host waits for the 0x05 Run reply of one operation.
LINK_SETTINGS = {"op_timeout": 5.0}


def api_fpga_connect(body: dict) -> dict:
    port = str(body.get("port", "")).strip()
    if not port:
        raise ValueError("choose a serial port")
    baud = int(body.get("baud") or C.BAUD)
    if _busy():
        raise ValueError("a job is using the board -- stop it first")
    log("connecting to the board on %s @ %d baud..." % (port, baud))
    return board.connect({"port": port, "baud": baud, **LINK_SETTINGS}, CN.open_board)


def _stop_hardware_job(reason: str) -> None:
    job = _busy()
    if job:
        with _jobs_lock:
            if job in _jobs:
                _jobs[job]["stop_requested"] = True
        log("stopping job %s (%s)" % (job, reason))


def api_fpga_disconnect(_body: dict) -> dict:
    _stop_hardware_job("board disconnected")
    return board.disconnect()


class _BoardOp:
    """``with _BoardOp() as target:`` -- a connected, idle board; a dead link
    found during the operation is reported as ``lost``."""

    def __enter__(self) -> T.Target:
        if _busy():
            raise ValueError("the board is busy with job %s" % _busy())
        self.target = board.require()
        return self.target

    def __exit__(self, exc_type, exc, tb):
        if isinstance(exc, T.ProtocolError) and exc.fatal:
            board.mark_lost(str(exc), self.target)
            raise ValueError("board link lost: %s" % exc)
        return False


def api_fpga_preflight(_body: dict) -> dict:
    with _BoardOp() as target:
        checks = target.preflight()
    for name, ok, detail in checks:
        log(("  OK  " if ok else "  FAIL") + " " + name + " -- " + detail)
    return {"checks": [{"name": n, "ok": ok, "detail": d} for n, ok, d in checks]}


def _int(v) -> int:
    return int(v, 0) if isinstance(v, str) else int(v)


def api_fpga_read_reg(body: dict) -> dict:
    name = body["name"]
    with _BoardOp() as target:
        val = target.link.rd(name)
    log("READ  %-12s = 0x%08X (%d)" % (name, val, val))
    return {"name": name, "value": val, "hex": "0x%08X" % val}


def api_fpga_write_reg(body: dict) -> dict:
    name, val = body["name"], _int(body["value"])
    with _BoardOp() as target:
        target.link.wr(name, val)
        target.forget_config()  # the next Decaps rewrites the layout it relies on
    log("WRITE %-12s <- 0x%08X" % (name, val))
    return {"name": name, "value": val}


def api_fpga_read_data(body: dict) -> dict:
    offset, length = _int(body["offset"]), _int(body["length"])
    if not 1 <= length <= 8192:
        raise ValueError("length must be 1..8192")
    with _BoardOp() as target:
        data = target.link.read_data(offset, length)
    log("READ_DATA  offset=0x%04X len=%d" % (offset, length))
    return {"offset": offset, "length": length, "hex": data.hex()}


def api_fpga_write_data(body: dict) -> dict:
    offset = _int(body["offset"])
    try:
        data = bytes.fromhex(body["hex"].strip().replace(" ", ""))
    except ValueError as e:
        raise ValueError("hex data: %s" % e)
    if not data:
        raise ValueError("no data to write")
    with _BoardOp() as target:
        target.link.write_data(offset, data)
    log("WRITE_DATA offset=0x%04X len=%d" % (offset, len(data)))
    return {"offset": offset, "length": len(data)}


def _decaps_result(out: T.Outcome, expected: bytes | None = None) -> dict:
    return {"ok": out.ok, "status": out.status, "error_code": out.error_code, "cycles": out.cycles,
            "describe": out.describe(), "output_hex": out.output.hex(), "wall_time": out.wall_time,
            "expected_hex": expected.hex() if expected else "",
            "k_match": (out.output == expected) if expected else None}


def api_fpga_decaps(body: dict) -> dict:
    """One Decaps with caller-supplied dk/ct (Advanced tab)."""
    dk = bytes.fromhex(body["dk_hex"].strip().replace(" ", ""))
    ct = bytes.fromhex(body["ct_hex"].strip().replace(" ", ""))
    if len(dk) != T.DK_LEN or len(ct) != T.CT_LEN:
        raise ValueError("dk must be %d bytes (got %d), ct must be %d bytes (got %d)"
                         % (T.DK_LEN, len(dk), T.CT_LEN, len(ct)))
    with _BoardOp() as target:
        out = target.decaps(dk, ct, resend_dk=bool(body.get("resend_dk", True)))
    if out.link_fatal:
        board.mark_lost(out.link_error)
    import mlkem768 as KEM
    expected = KEM.decaps(dk, ct)
    log("DECAPS: %s -- K %s" % (out.describe(), "matches FIPS 203" if out.output == expected else "MISMATCH"))
    return _decaps_result(out, expected)


# --- Advanced: link settings and the protocol steps (docs/UART_PROTOCOL.md) ---

def _refresh_board_info(target: T.Target) -> None:
    """Update the published board info after a settings change, without link I/O."""
    info = dict(board.snapshot().get("info") or {})
    if info:
        info.update(has_run=bool(target.has_run), op_timeout=target.op_timeout)
        board.update_info(info)


def api_fpga_settings(body: dict) -> dict:
    """GET (empty body): current settings. POST: change op_timeout."""
    if body:
        if "op_timeout" in body:
            t = float(body["op_timeout"])
            if not 0.1 <= t <= 60:
                raise ValueError("operation timeout must be 0.1 .. 60 s")
            LINK_SETTINGS["op_timeout"] = t
        try:
            target = board.require()
        except ValueError:
            target = None
        if target is not None:
            target.op_timeout = LINK_SETTINGS["op_timeout"]
            _refresh_board_info(target)
        log("link settings: operation timeout %.1f s" % LINK_SETTINGS["op_timeout"])
    layout = {"in_base": C.IN_BASE, "out_base": C.OUT_BASE, "dk_len": T.DK_LEN, "ct_len": T.CT_LEN,
              "ops": [{"op": op, "name": n, "in_len": i, "out_len": o} for op, (n, i, o) in sorted(T.OPS.items())]}
    return dict(LINK_SETTINGS, layout=layout)


def api_fpga_reprobe(_body: dict) -> dict:
    with _BoardOp() as target:
        target.probe_run()
    _refresh_board_info(target)
    log("0x05 Run probe: %s" % ("supported" if target.has_run else "not in this bitstream"))
    return {"has_run": bool(target.has_run)}


_REG_NOTES = {"CTRL": "WO: bit0 START, bit1 RESET", "STATUS": "busy | done | error", "ALG_ID": "1 = ML-KEM",
              "SEC_LEVEL": "768 only", "OP_MODE": "0 KeyGen, 1 Encaps, 2 Decaps", "CYCLE_COUNT": "last operation",
              "VERSION": "major.minor.patch", "ERROR_CODE": "", "DATA_IN_ADDR": "input offset",
              "DATA_IN_LEN": "input length", "DATA_OUT_ADDR": "output offset", "DATA_OUT_LEN": "output length"}


def _decode_reg(name: str, v: int) -> str:
    if name == "STATUS":
        return " | ".join(T.status_flags(v)) or "idle"
    if name == "ERROR_CODE":
        return T.ERRORS.get(v, "?")
    if name == "OP_MODE":
        return T.OPS.get(v, ("?",))[0]
    if name == "VERSION":
        return "%d.%d.%d" % ((v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF)
    if name.startswith("DATA_") or name == "CTRL":
        return "0x%04X" % v
    return str(v)


def api_fpga_read_all(_body: dict) -> dict:
    rows = []
    with _BoardOp() as target:
        with target.link.lock:
            for name, addr in T.REG.items():
                v = target.link.rd(name)
                rows.append({"name": name, "addr": "0x%02X" % addr, "value": v, "hex": "0x%08X" % v,
                             "decoded": _decode_reg(name, v), "note": _REG_NOTES.get(name, "")})
    log("read all %d registers" % len(rows))
    return {"registers": rows}


def _hex_bytes(body: dict, key: str, length: int | None = None) -> bytes:
    try:
        data = bytes.fromhex(str(body.get(key, "")).strip().replace(" ", "").replace("\n", ""))
    except ValueError as e:
        raise ValueError("%s: %s" % (key, e))
    if length is not None and len(data) != length:
        raise ValueError("%s must be %d bytes (got %d)" % (key, length, len(data)))
    return data


def api_fpga_step(body: dict) -> dict:
    """One step of an operation, as in the SimpleSerial-style command set:
    reset (x), configure (m), load_sk (s), load_ct (c), load_input, run (e), read_output (a)."""
    step = body.get("step")
    with _BoardOp() as target:
        if step == "reset":
            target.reset()
            st = target.link.rd("STATUS")
            msg = "CTRL.reset -- STATUS 0x%X (%s)" % (st, " | ".join(T.status_flags(st)) or "idle")
            res = {"status": st}
        elif step == "configure":
            op = int(body.get("op", T.OP_DECAPS))
            if op not in T.OPS:
                raise ValueError("unknown OP_MODE %d" % op)
            name, in_len, out_len = T.OPS[op]
            target.configure(op, in_len, force=True)
            msg = ("SEC_LEVEL=768 OP_MODE=%d (%s) DATA_IN_ADDR=0x%04X DATA_IN_LEN=%d DATA_OUT_ADDR=0x%04X"
                   % (op, name, C.IN_BASE, in_len, C.OUT_BASE))
            res = {"op": op, "in_len": in_len, "out_len": out_len}
        elif step == "load_sk":
            target.load_secret_key(_hex_bytes(body, "hex", T.DK_LEN))
            msg = "dk (%d B) written at 0x%04X" % (T.DK_LEN, C.IN_BASE)
            res = {}
        elif step == "load_ct":
            target.load_ciphertext(_hex_bytes(body, "hex", T.CT_LEN))
            msg = "c (%d B) written at 0x%04X" % (T.CT_LEN, C.IN_BASE + T.DK_LEN)
            res = {}
        elif step == "load_input":
            data = _hex_bytes(body, "hex")
            if not data:
                raise ValueError("no input bytes")
            target.link.write_data(C.IN_BASE, data)
            msg = "input (%d B) written at 0x%04X" % (len(data), C.IN_BASE)
            res = {}
        elif step == "run":
            out_len = int(body.get("out_len", T.K_LEN))
            if not 0 <= out_len <= 8192:
                raise ValueError("output length must be 0..8192")
            t0 = time.time()
            st, cycles, err, out = target.run(out_len)
            wall = time.time() - t0
            msg = "0x05 Run -- STATUS 0x%X (%s), %d cycles, ERROR_CODE %d, %d output bytes, %.1f ms" % (
                st, " | ".join(T.status_flags(st)) or "idle", cycles, err, len(out), 1000 * wall)
            res = {"status": st, "flags": T.status_flags(st), "cycles": cycles, "error_code": err,
                   "error": T.ERRORS.get(err, "?"), "output_hex": out.hex(), "wall_time": wall,
                   "us": cycles / C.CORE_MHZ}
        elif step == "read_output":
            n = int(body.get("length", T.K_LEN))
            out = target.link.read_data(C.OUT_BASE, n)
            msg = "%d bytes read at 0x%04X" % (n, C.OUT_BASE)
            res = {"output_hex": out.hex()}
        else:
            raise ValueError("unknown step %r" % step)
    log("STEP %-11s %s" % (step, msg))
    return dict(res, step=step, message=msg)


# =============================================================================
# Scope connection
# =============================================================================
def api_scope_resources(_body: dict) -> dict:
    found = S.list_resources()
    log("found %d VISA resource(s)" % len(found))
    return {"resources": [{"resource": r, "idn": idn} for r, idn in found]}


def api_scope_connect(body: dict) -> dict:
    ip = str(body.get("ip", "")).strip()
    if not ip:
        raise ValueError("enter the scope's IP address")
    if ip == C.SCOPE_PLACEHOLDER_IP:
        log("[warn] SCOPE_IP is still the config.py placeholder (%s) -- this will just time out" % ip)
    params = {"ip": ip, "channel": body.get("channel") or C.SCOPE_CHANNEL,
              "dialect": body.get("dialect") or C.SCOPE_DIALECT, "points": int(body.get("points") or 0),
              "timeout_ms": int(body.get("timeout_ms") or C.SCOPE_TIMEOUT_MS),
              "transport": body.get("transport") or C.SCOPE_TRANSPORT}
    if params["dialect"] not in S.DIALECT_NAMES:
        raise ValueError("unknown scope dialect %r" % params["dialect"])
    resource = S.resource_for(ip, params["transport"])        # also rejects an unknown transport
    if _busy():
        raise ValueError("a job is using the scope -- stop it first")
    log("connecting to scope %s (%s driver) ..." % (resource, params["dialect"]))
    return scope_conn.connect(params, CN.open_scope)


def api_scope_disconnect(_body: dict) -> dict:
    _stop_hardware_job("scope disconnected")
    return scope_conn.disconnect()


def api_scope_save_config(body: dict) -> dict:
    transport = str(body.get("transport") or C.SCOPE_TRANSPORT).strip()
    if transport not in C.SCOPE_RESOURCES:
        raise ValueError("unknown scope transport %r" % transport)
    save_config_values(SCOPE_IP=body["ip"].strip(), SCOPE_CHANNEL=body["channel"].strip(),
                       SCOPE_DIALECT=body["dialect"].strip(), SCOPE_TRANSPORT=transport)
    log("saved SCOPE_IP / SCOPE_CHANNEL / SCOPE_DIALECT / SCOPE_TRANSPORT to config.py")
    return {}


# =============================================================================
# CPA model
# =============================================================================
def _default_model() -> tuple[str, str]:
    """config.py's CPA_MODEL / CPA_RPREV, if they still name a model and a choice it accepts."""
    name = LM.resolve(C.CPA_MODEL) or "hd_basemul_word"
    try:
        return name, LM.get(name).resolve_rprev(C.CPA_RPREV)
    except ValueError:
        return name, ""


def api_model_list(_body: dict) -> dict:
    """Every leakage model in models/, re-read from disk (new or edited files show up)."""
    found, errors = LM.scan()
    for e in errors:
        log("[warn] leakage model %s not loaded: %s" % (e["file"], e["error"]))
    name, rprev = _default_model()
    return {"models": [LM.describe(m) for m in found], "errors": errors,
            "default_model": name, "default_rprev": rprev, "rprevs": LM.RPREVS}


def api_model_selftest(body: dict) -> dict:
    model = LM.get(str(body.get("model", "")), reload=True)
    rows = LM.selftest(model)
    for r in rows:
        log("self-test %s: %s %s -- %s" % (model.name, "PASS" if r["ok"] else "FAIL", r["name"], r["detail"]))
    return {"model": model.name, "checks": rows, "ok": all(r["ok"] for r in rows)}


def api_model_save_default(body: dict) -> dict:
    model = LM.get(str(body.get("model", "")))
    rprev = model.resolve_rprev(str(body.get("rprev", "")))
    values = {"CPA_MODEL": model.name}
    if rprev:
        values["CPA_RPREV"] = rprev
    save_config_values(**values)
    C.CPA_MODEL, C.CPA_RPREV = model.name, rprev or C.CPA_RPREV
    log("saved CPA_MODEL=%s%s to config.py" % (model.name, " CPA_RPREV=%s" % rprev if rprev else ""))
    return {}


# =============================================================================
# Test vectors
# =============================================================================
def api_vectors_info(_body: dict) -> dict:
    return {"strategies": TV.STRATEGIES, "generator": TV.GENERATOR, "directory": str(VECTORS_DIR)}


def _run_vectors_job(job_id: str, kw: dict) -> None:
    try:
        def cb(done, total, msg):
            _update_job(job_id, progress=done, total=total, message=msg)
        vs = TV.generate(progress_cb=cb, **kw)
        _update_job(job_id, message="verifying a sample in software...")
        check = vs.verify_software(sample=32)
        if not check["ok"]:
            raise RuntimeError("software Decaps disagrees with the generated K at rows %s" % check["mismatches"])
        path = vs.save(VECTORS_DIR / (vs.meta["id"] + ".npz"))      # where the GUI lists sets
        result = dict(vs.meta, path=str(path), filename=path.name, size_bytes=path.stat().st_size,
                      software_check=check)
        _update_job(job_id, status="done", result=result, message="done")
        log("test vectors %s: %d vectors (%s), expected K verified on %d rows"
            % (path.name, vs.n, vs.meta["strategy"], check["checked"]))
    except Exception as e:  # noqa: BLE001
        _update_job(job_id, status="error", error="%s: %s" % (type(e).__name__, e))
        log("[error] test-vector job %s: %s" % (job_id, e))


def api_vectors_generate(body: dict) -> dict:
    mode = body.get("mode", "cpa")
    n = int(body.get("n", 1000) or 0)
    if not 1 <= n <= 200000:
        raise ValueError("n must be 1..200000")
    seed_hex = str(body.get("seed", "")).strip().lower().replace(" ", "")
    try:
        seed = bytes.fromhex(seed_hex) if seed_hex else os.urandom(64)
    except ValueError:
        raise ValueError("seed must be hex")
    kw = {"mode": mode, "n": n, "seed": seed, "strategy": body.get("strategy", "valid"),
          "poly": int(body.get("poly") or 0), "pair": int(body.get("pair") or 0),
          "invalid_random": bool(body.get("invalid_random", False))}
    if mode not in ("cpa", "tvla") or (mode == "cpa" and kw["strategy"] not in TV.STRATEGIES):
        raise ValueError("unknown mode/strategy")
    job_id = _new_job("vectors", total=n)
    threading.Thread(target=_run_vectors_job, args=(job_id, kw), daemon=True).start()
    log("generating %d %s test vectors (seed %s...)" % (n, mode, seed.hex()[:16]))
    return {"job_id": job_id, "seed": seed.hex()}


def api_job_status(body: dict) -> dict:
    return _get_job(body["job_id"])


VEC_TRASH = VECTORS_DIR / ".trash"


def api_vectors_list(body: dict) -> dict:
    trash = str(body.get("trash", "")).lower() in ("1", "true")
    VEC_TRASH.mkdir(parents=True, exist_ok=True)
    return {"sets": TV.list_sets(VEC_TRASH if trash else VECTORS_DIR), "trash": trash,
            "storage": {"n_sets": len(list(VECTORS_DIR.glob("*.npz"))), "n_trash": len(list(VEC_TRASH.glob("*.npz"))),
                        "bytes": sum(q.stat().st_size for q in VECTORS_DIR.glob("*.npz"))}}


def _vector_file(path: str, directory: Path) -> Path:
    p = Path(path)
    if not p.is_absolute():
        p = directory / p
    if directory.resolve() != p.resolve().parent or p.suffix != ".npz" or not p.is_file():
        raise ValueError("no such vector set: %s" % p.name)
    return p


def api_vectors_update(body: dict) -> dict:
    """Name and/or tags of one vector set (kept in <set>.meta.json)."""
    p = _vector_file(body["path"], VECTORS_DIR)
    a = TV.write_annotations(p, label=str(body["label"])[:120] if "label" in body else None,
                             tags=_clean_tags(body["tags"]) if "tags" in body else None)
    log("updated %s of %s" % ("/".join(k for k in ("label", "tags") if k in body), p.name))
    return a


def api_vectors_tag(body: dict) -> dict:
    done = []
    for path in _paths(body):
        p = _vector_file(path, VECTORS_DIR)
        TV.write_annotations(p, tags=_edit_tags(TV.read_annotations(p)["tags"],
                                                body.get("add", []), body.get("remove", [])))
        done.append(p.name)
    log("tags changed on %d vector set(s)" % len(done))
    return {"done": done}


def _move_set(p: Path, dest_dir: Path) -> Path:
    side = TV.annotations_path(p)
    dest = _move_unique(p, dest_dir)
    if side.is_file():
        shutil.move(str(side), str(TV.annotations_path(dest)))
    return dest


def api_vectors_delete(body: dict) -> dict:
    """Move vector sets to data/vectors/.trash/. Campaigns captured with a set
    keep working: each campaign file stores its ciphertexts, expected keys and
    the recipe (seed) the key is rebuilt from."""
    done = [str(_move_set(_vector_file(path, VECTORS_DIR), VEC_TRASH)) for path in _paths(body)]
    log("moved %d vector set(s) to the trash" % len(done))
    return {"trashed": done}


def api_vectors_restore(body: dict) -> dict:
    done = []
    for path in _paths(body):
        p = _vector_file(path, VEC_TRASH)
        if (VECTORS_DIR / p.name).exists():
            raise ValueError("%s already exists" % p.name)
        done.append(_move_set(p, VECTORS_DIR).name)
    log("restored vector set(s) %s" % ", ".join(done))
    return {"restored": done}


def _purge_set(p: Path) -> None:
    TV.annotations_path(p).unlink(missing_ok=True)
    p.unlink()


def api_vectors_purge(body: dict) -> dict:
    paths = [_vector_file(path, VEC_TRASH) for path in _paths(body)]
    for p in paths:
        _purge_set(p)
    log("permanently deleted %d vector set(s)" % len(paths))
    return {"deleted": len(paths)}


def api_vectors_empty_trash(_body: dict) -> dict:
    VEC_TRASH.mkdir(parents=True, exist_ok=True)
    paths = list(VEC_TRASH.glob("*.npz"))
    for p in paths:
        _purge_set(p)
    log("emptied the vector-set trash: %d set(s)" % len(paths))
    return {"deleted": len(paths)}


def _load_set(path: str) -> TV.VectorSet:
    return TV.VectorSet.load(TV.resolve(path, VECTORS_DIR))


def api_vectors_view(body: dict) -> dict:
    vs = _load_set(body["path"])
    offset = max(0, int(body.get("offset", 0) or 0))
    limit = min(200, max(1, int(body.get("limit", 50) or 50)))
    cls_filter = body.get("class", "")
    idx = np.arange(vs.n)
    if cls_filter in ("0", "1", 0, 1):
        idx = idx[vs.cls == int(cls_filter)]
    page = idx[offset:offset + limit]
    rows = []
    for i in page:
        r = vs.row(int(i))
        r["ct_short"] = r["ct"][:32] + "…" + r["ct"][-8:]
        del r["ct"]
        rows.append(r)
    return {"meta": vs.meta, "total": int(idx.size), "offset": offset, "rows": rows,
            "dk_head": vs.dk[:16].hex(), "ek_head": vs.ek[:16].hex()}


def api_vectors_row(body: dict) -> dict:
    vs = _load_set(body["path"])
    i = int(body["index"])
    if not 0 <= i < vs.n:
        raise ValueError("index out of range")
    return dict(vs.row(i), dk=vs.dk.hex())


def api_vectors_download(body: dict):
    p = TV.resolve(body["path"], VECTORS_DIR)
    fmt = body.get("format", "csv")
    stem = p.stem
    if fmt == "npz":
        return Download(p.name, "application/octet-stream", p.read_bytes())
    vs = TV.VectorSet.load(p)
    if fmt == "csv":
        return Download(stem + ".csv", "text/csv; charset=utf-8", vs.to_csv().encode())
    if fmt == "json":
        return Download(stem + ".json", "application/json", vs.to_json().encode())
    if fmt in ("dk", "ek"):
        return Download("%s_%s.bin" % (stem, fmt), "application/octet-stream", vs.dk if fmt == "dk" else vs.ek)
    raise ValueError("format must be csv, json, npz, dk or ek")


def api_vectors_verify_software(body: dict) -> dict:
    vs = _load_set(body["path"])
    r = vs.verify_software(sample=int(body.get("sample", 0) or vs.n))
    log("software check of %s: %d rows, %s" % (Path(body["path"]).name, r["checked"],
                                               "all K correct" if r["ok"] else "MISMATCH"))
    return r


def _run_verify_board_job(job_id: str, path: str, count: int) -> None:
    try:
        vs = _load_set(path)
        target = board.require()
        rows, n_ok = [], 0
        for j, i in enumerate(range(min(count, vs.n))):
            if _stop_requested(job_id):
                break
            out = target.decaps(vs.dk, vs.ct[i].tobytes(), resend_dk=(j == 0))
            if out.link_fatal:
                board.mark_lost(out.link_error, target)
                raise RuntimeError("board link lost: %s" % out.link_error)
            match = out.output == vs.k[i].tobytes()
            if not match:
                target.forget_config()
            n_ok += int(match and out.ok)
            rows.append({"index": i, "class": int(vs.cls[i]), "ok": out.ok, "k_match": match,
                         "cycles": out.cycles, "describe": out.describe(), "valid": bool(vs.valid[i])})
            _update_job(job_id, progress=j + 1, message="%d/%d match" % (n_ok, j + 1))
        cycles = sorted({r["cycles"] for r in rows if r["ok"]})
        result = {"path": path, "checked": len(rows), "passed": n_ok, "rows": rows,
                  "cycles_distinct": cycles[:10], "constant_time": len(cycles) <= 1}
        _update_job(job_id, status="done", result=result, message="done")
        log("board check of %s: %d/%d Decaps outputs equal the FIPS 203 expected K%s"
            % (Path(path).name, n_ok, len(rows), "" if len(cycles) <= 1 else
               " -- CYCLE_COUNT varied: %s" % cycles[:5]))
    except Exception as e:  # noqa: BLE001
        _update_job(job_id, status="error", error="%s: %s" % (type(e).__name__, e))
        log("[error] board check: %s" % e)
    finally:
        _release_hardware(job_id)


def api_vectors_verify_board(body: dict) -> dict:
    board.require().require_run()
    count = min(1000, max(1, int(body.get("count", 20) or 20)))
    path = body["path"]
    _load_set(path)  # fail fast on a bad path
    job_id = _new_job("verify_board", total=count)
    _claim_hardware(job_id)
    threading.Thread(target=_run_verify_board_job, args=(job_id, path, count), daemon=True).start()
    log("checking %d vectors of %s on the board..." % (count, Path(path).name))
    return {"job_id": job_id}


def api_job_stop(body: dict) -> dict:
    with _jobs_lock:
        if body["job_id"] not in _jobs:
            raise ValueError("no such job")
        _jobs[body["job_id"]]["stop_requested"] = True
    log("stop requested for job %s" % body["job_id"])
    return {}


# =============================================================================
# Trace acquisition
# =============================================================================
def api_acquire_info(_body: dict) -> dict:
    b, s = board.snapshot()["state"], scope_conn.snapshot()["state"]
    return {"board": b, "scope": s, "hardware_ready": b == "connected" and s == "connected", "busy": _busy()}


class _LiveStats:
    """Running per-sample statistics of a campaign, for the live preview."""

    def __init__(self, n_samples: int, rate: float, mode: str):
        self.n_samples, self.rate, self.mode = n_samples, rate, mode
        self.seq = 0
        self.last = None
        self.last_index = -1
        self.last_cls = None
        self.sums = {0: np.zeros(n_samples), 1: np.zeros(n_samples)}
        self.counts = {0: 0, 1: 0}
        self.n_bad = 0

    def add(self, index: int, trace: np.ndarray, cls, ok: bool) -> None:
        c = 1 if cls is None else int(cls)
        with _live_lock:
            self.last, self.last_index, self.last_cls = trace.astype(np.float64), index, cls
            if ok:
                self.sums[c] += trace
                self.counts[c] += 1
            else:
                self.n_bad += 1
            self.seq += 1
            _live_cond.notify_all()


def _run_acquire_job(job_id: str, vs_path: str, resume_path: str, samples: int, resend_dk_every: int) -> None:
    try:
        target = board.require()
        sc = scope_conn.require()
        if resume_path:
            out_path = Path(resume_path)
            s = HS.summarize(out_path)
            mode, n_samples = s["mode"], int(s["n_samples"])     # the file's row length
            vectors = None
        else:
            vectors = _load_set(vs_path)
            mode = vectors.mode
            TRACES_DIR.mkdir(parents=True, exist_ok=True)
            out_path = TRACES_DIR / ("%s_%s.hdf5" % (time.strftime("%Y%m%d_%H%M%S"), mode))
            n_samples = getattr(sc, "samples", 0) or samples
            if not n_samples:                                       # 0: whatever the scope records
                _update_job(job_id, message="measuring the scope's record length...")
                n_samples = A.measure_record_length(target, sc)
            log("%d samples per trace" % n_samples)
        rate = sc.sample_rate()
        live = _LiveStats(n_samples, rate, mode)
        with _live_lock:
            _live[job_id] = live
        _update_job(job_id, file=str(out_path), mode=mode, message="acquiring...")
        summary = A.run_campaign(
            target, sc, str(out_path), n_samples, vectors=vectors, resume=bool(resume_path),
            vector_set_path=vs_path, resend_dk_every=resend_dk_every, quiet=True,
            progress_cb=lambda d, t, m: _update_job(job_id, progress=d, total=t, message=m),
            trace_cb=live.add, stop_cb=lambda: _stop_requested(job_id))
        if summary.get("link_lost"):
            board.mark_lost("link lost during acquisition", target)
        msg = ("link lost -- partial file, resumable" if summary.get("link_lost") else
               "stopped -- partial file, resumable" if summary.get("stopped_early") else "done")
        _update_job(job_id, status="done", result=summary, message=msg)
        log("acquisition %s: %s (%d/%d traces, %d K mismatches)" % (out_path.name, msg, summary["n_completed"],
            summary["n_traces_planned"], summary.get("k_mismatches", 0)))
    except Exception as e:  # noqa: BLE001
        _update_job(job_id, status="error", error="%s: %s" % (type(e).__name__, e))
        log("[error] acquisition job %s: %s" % (job_id, e))
    finally:
        _release_hardware(job_id)


def api_acquire_start(body: dict) -> dict:
    board.require().require_run()
    scope_conn.require()
    resume_path = str(body.get("resume_file", "")).strip()
    vs_path = str(body.get("vector_set", "")).strip()
    if resume_path:
        p = Path(resume_path)
        if TRACES_DIR.resolve() not in p.resolve().parents or not p.is_file():
            raise ValueError("no such campaign file to resume")
        s = HS.summarize(p)
        if not s["resumable"]:
            raise ValueError("%s is not resumable (complete, or written before vector sets)" % p.name)
    elif not vs_path:
        raise ValueError("choose a test-vector set (Test Vectors tab) first")
    else:
        _load_set(vs_path)
    resend = int(body.get("resend_dk_every", 50) or 50)
    if resend < 1:
        raise ValueError("resend_dk_every must be >= 1")
    job_id = _new_job("acquire")
    _claim_hardware(job_id)
    threading.Thread(target=_run_acquire_job, daemon=True,
                     args=(job_id, vs_path, resume_path, int(body.get("samples", 0) or 0), resend)).start()
    log("acquisition started (%s)" % ("resuming %s" % Path(resume_path).name if resume_path
                                      else Path(vs_path).name))
    return {"job_id": job_id}


def api_acquire_live(body: dict) -> dict:
    """Latest trace and running means of a running/finished acquisition (decimated).

    With ``wait_ms`` the request is a long poll: it returns as soon as a trace
    newer than ``since`` is captured, or "unchanged" after ``wait_ms`` (at most
    LIVE_WAIT_MAX_S), so the preview follows every trace without busy polling.
    """
    job_id = body["job_id"]
    since = int(body.get("since", -1))
    max_points = min(20000, max(200, int(body.get("max_points", 3000) or 3000)))
    wait = min(LIVE_WAIT_MAX_S, max(0.0, float(body.get("wait_ms", 0) or 0) / 1000.0))

    def fresh() -> bool:
        lv = _live.get(job_id)
        return lv is not None and lv.last is not None and lv.seq != since

    with _live_cond:
        if wait and not fresh():
            _live_cond.wait_for(fresh, timeout=wait)
        live = _live.get(job_id)
        if live is None or live.last is None:
            return {"seq": 0, "ready": False}
        if live.seq == since:
            return {"seq": live.seq, "ready": True, "unchanged": True}
        last = live.last.copy()
        means = {c: (live.sums[c] / live.counts[c]) for c in (0, 1) if live.counts[c]}
        counts, seq, idx, cls, bad = dict(live.counts), live.seq, live.last_index, live.last_cls, live.n_bad
    out = {"seq": seq, "ready": True, "index": idx, "class": cls, "n_bad": bad, "mode": live.mode,
           "axis": _axis(live.rate, live.n_samples), "last": _series(last, live.rate, max_points),
           "means": {str(c): dict(_series(m, live.rate, max_points), n=counts[c]) for c, m in means.items()}}
    if live.mode == "tvla" and 0 in means and 1 in means:
        out["mean_diff"] = _series(means[0] - means[1], live.rate, max_points)
    return out


def api_acquire_capture_one(body: dict) -> dict:
    """One real capture with a fresh valid vector, for checking trigger / scale / timebase."""
    vs = TV.generate("cpa", 1, os.urandom(64), "valid")
    with _BoardOp() as target:
        sc = scope_conn.require()
        sc.arm()
        out = target.decaps(vs.dk, vs.ct[0].tobytes(), resend_dk=True)
        if out.link_fatal:
            board.mark_lost(out.link_error, target)
        if not out.ok:
            raise ValueError("capture failed: %s" % out.describe())
        trace = sc.read(timeout=10.0)
    rate = sc.sample_rate()
    max_points = min(20000, max(500, int(body.get("max_points", 4000) or 4000)))
    t = np.asarray(trace, dtype=np.float64)
    k_ok = out.output == vs.k[0].tobytes()
    log("capture: %s, K %s, %d samples @ %s Sa/s" % (out.describe(), "ok" if k_ok else "MISMATCH",
                                                     t.size, ("%.4g" % rate) if rate else "?"))
    _, start_ok, start_detail = _record_start_check(sc)
    query = getattr(sc, "record_start_s", None)
    return {"trace": _series(t, rate, max_points), "axis": _axis(rate, int(t.size)),
            "record_start": {"s": query() if callable(query) else None, "ok": start_ok, "detail": start_detail},
            "cycles": out.cycles, "describe": out.describe(), "k_match": k_ok,
            "stats": {"min": float(t.min()), "max": float(t.max()), "mean": float(t.mean()),
                      "std": float(t.std()), "n": int(t.size)},
            "captured_unix": time.time()}


#: Where trig_out comes out, for the trigger-check messages.
TRIG_PINS = ("Arty A7: Pmod JA1, Arty Z7: Pmod JA3 -- 3.3 V, level about 1.6 V; "
             "SAKURA-X: CN8 pin 3, ground on CN8 pin 11 -- 2.5 V, level about 1.25 V")

#: A record may start this many core cycles off the trigger (LeCroy reports the
#: trigger time to a fraction of a sample) before the BMUL windows are misplaced.
RECORD_START_TOL_CYCLES = 5


def api_acquire_trigger_check(_body: dict) -> dict:
    """Does the scope trigger on the start of a Decaps -- and only then?

    The boards drive trig_out = STATUS.busy (Pmod JA1 on the Arty A7, JA3 on
    the Arty Z7, CN8 pin 3 on the SAKURA-X): it rises in the core clock cycle
    the operation starts and stays high for exactly CYCLE_COUNT core cycles
    (testbench/cdc/tb_bridge.sv checks that). Four checks on the real setup:

    1. armed, register and buffer traffic but no START: the scope must NOT
       trigger -- catches AUTO trigger mode, a wrong source, a level noise crosses;
    2. armed, one Decaps (0x05 Run): the scope MUST trigger;
    3. the captured record must reach past the last BMUL window;
    4. the record must start at the trigger (sample 0 = Decaps start), if the
       driver reports the trigger position (LeCroy does).
    """
    vs = TV.generate("cpa", 1, os.urandom(64), "valid")
    checks, trace, out = [], None, None
    with _BoardOp() as target:
        target.require_run()
        sc = scope_conn.require()
        query = getattr(sc, "triggered", None)
        name = "no trigger without an operation"
        if callable(query):
            sc.arm()
            target.link.rd("STATUS")                               # link traffic, no START
            target.link.write_data(C.OUT_BASE, bytes(256))
            target.link.rd("ALG_ID")
            time.sleep(0.3)
            fired = query()
            if fired is None:
                checks.append((name, None, "this scope driver cannot report its trigger state -- skipped"))
            elif fired:
                checks.append((name, False, "the scope triggered although no operation ran: set the trigger mode "
                               "to NORMAL / SINGLE (not AUTO), source = the channel probing trig_out, rising edge "
                               "(%s)" % TRIG_PINS))
            else:
                checks.append((name, True, "stayed armed through register and buffer traffic"))
        else:
            checks.append((name, None, "this scope driver cannot report its trigger state -- skipped"))

        sc.arm()
        out = target.decaps(vs.dk, vs.ct[0].tobytes(), resend_dk=True)
        if out.link_fatal:
            board.mark_lost(out.link_error, target)
        if not out.ok:
            raise ValueError("the Decaps itself failed: %s" % out.describe())
        try:
            trace = np.asarray(sc.read(timeout=3.0), dtype=np.float64)
            checks.append(("trigger on the Decaps start", True,
                           "triggered by one Decaps; trig_out was high for %d core cycles (%.1f us)"
                           % (out.cycles, out.cycles / C.CORE_MHZ)))
        except TimeoutError:
            checks.append(("trigger on the Decaps start", False,
                           "the Decaps ran (%d cycles) but the scope did not trigger within 3 s: probe trig_out "
                           "(%s), trigger source = that channel, rising edge" % (out.cycles, TRIG_PINS)))
    k_ok = out.output == vs.k[0].tobytes()
    checks.append(("Decaps result", k_ok, "K equals the FIPS 203 value" if k_ok else "K differs from the expected value"))
    result = {"pulse_us": out.cycles / C.CORE_MHZ, "cycles": out.cycles}
    if trace is not None:
        rate = sc.sample_rate()
        axis = _axis(rate, int(trace.size))
        if not axis.get("duration_us"):
            checks.append(("record covers the BMUL windows", None, "sample rate unknown -- cannot tell"))
        elif axis["bmul_outside"]:
            checks.append(("record covers the BMUL windows", False, "%.1f us captured, %d BMUL window(s) cut off: "
                           "lengthen the timebase to at least %s us after the trigger"
                           % (axis["duration_us"], axis["bmul_outside"], axis["needed_us"])))
        else:
            checks.append(("record covers the BMUL windows", True, "%.1f us captured, all three BMUL windows inside "
                           "(need %s us)" % (axis["duration_us"], axis["needed_us"])))
        checks.append(_record_start_check(sc))
        result.update(trace=_series(trace, rate, 4000), axis=axis)
    rows = [{"name": n, "ok": ok, "detail": d} for n, ok, d in checks]
    result.update(checks=rows, ok=all(r["ok"] is not False for r in rows))
    log("trigger check: " + "; ".join("%s %s" % ("PASS" if r["ok"] else "SKIP" if r["ok"] is None else "FAIL", r["name"])
                                      for r in rows))
    return result


def _record_start_check(sc) -> tuple:
    """Check 4 of the trigger check: does sample 0 sit on the trigger?"""
    name = "record starts at the trigger"
    query = getattr(sc, "record_start_s", None)
    start = query() if callable(query) else None
    if start is None:
        return (name, None, "this scope driver does not report the trigger position -- check it once by eye "
                "(docs/TRACE_ACQUISITION.md, 4.4)")
    cycles = start * C.CORE_MHZ * 1e6
    if abs(cycles) <= RECORD_START_TOL_CYCLES:
        return (name, True, "first sample %+.1f ns from the trigger (%+.1f core cycles)" % (start * 1e9, cycles))
    where = ("BEFORE the trigger (pre-trigger data: every BMUL window would be shifted by %.0f core cycles)"
             % -cycles if start < 0 else "AFTER the trigger (the start of the Decaps is missing)")
    return (name, False, "the record starts %.3f us %s. The analysis takes sample 0 as the Decaps start: set the "
            "horizontal delay so the time axis starts at 0 (trigger marker at the left edge of the grid)"
            % (abs(start) * 1e6, where))


def api_datasets(_body: dict) -> dict:
    TRACES_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for p in sorted(TRACES_DIR.glob("*.hdf5"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            s = HS.summarize(p)
        except Exception:  # noqa: BLE001 - skip unreadable/partial files
            continue
        md = s["metadata"]
        out.append({
            "filename": p.name, "path": str(p), "mode": s.get("mode", "cpa"),
            "simulated": bool(s.get("simulated", False)), "n_completed": s["n_completed"],
            "n_traces_planned": s.get("n_traces_planned", s["n_completed"]),
            "resumable": s.get("resumable", False), "created_unix": md.get("created_unix"),
            "strategy": md.get("strategy", ""), "vector_set": md.get("vector_set", ""),
            "n_fixed": s.get("n_fixed"), "n_random": s.get("n_random"),
            "ok_fraction": s.get("ok_fraction"), "k_match_fraction": s.get("k_match_fraction"),
            "n_samples": s.get("n_samples"), "sample_rate_hz": s.get("sample_rate_hz"),
            "has_recipe": "recipe" in md,
        })
    return {"datasets": out}


# =============================================================================
# Tags, multi-item requests, trash (shared by the trace library and vector sets)
# =============================================================================
MAX_TAGS, MAX_TAG_LEN = 16, 32


def _clean_tags(value) -> list[str]:
    """User tags: a list or a comma-separated string -> trimmed, no commas,
    at most MAX_TAG_LEN characters each, duplicates (ignoring case) dropped."""
    items = value.split(",") if isinstance(value, str) else list(value or [])
    out: list[str] = []
    for t in items:
        t = " ".join(str(t).replace(",", " ").split())[:MAX_TAG_LEN]
        if t and t.lower() not in (x.lower() for x in out):
            out.append(t)
    return out[:MAX_TAGS]


def _edit_tags(tags: list[str], add, remove) -> list[str]:
    drop = {t.lower() for t in _clean_tags(remove)}
    return _clean_tags([t for t in tags if t.lower() not in drop] + _clean_tags(add))


def _paths(body: dict) -> list[str]:
    paths = body.get("paths")
    if paths is None:
        paths = [body["path"]]
    if not isinstance(paths, list) or not paths:
        raise ValueError("no items given")
    return [str(x) for x in paths]


def _move_unique(src: Path, dest_dir: Path) -> Path:
    """Move a file into dest_dir; a name already there gets a timestamp suffix."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    if dest.exists():
        dest = dest_dir / ("%s_%d%s" % (src.stem, int(time.time()), src.suffix))
    shutil.move(str(src), str(dest))
    return dest


# =============================================================================
# Trace library (campaign files under data/traces/)
# =============================================================================
TRASH_DIR = TRACES_DIR / ".trash"


def _recording_path() -> str | None:
    """The campaign file a running acquisition is writing, if any."""
    with _jobs_lock:
        for j in _jobs.values():
            if j["kind"] == "acquire" and j["status"] == "running" and j.get("file"):
                return str(Path(j["file"]).resolve())
    return None


def _campaign(path: str, directory: Path | None = None) -> Path:
    p = Path(path)
    if not p.is_absolute():
        p = (directory or TRACES_DIR) / p
    if (directory or TRACES_DIR).resolve() != p.resolve().parent or p.suffix != ".hdf5" or not p.is_file():
        raise ValueError("no such campaign file: %s" % p.name)
    return p


def _not_recording(p: Path) -> None:
    if _recording_path() == str(p.resolve()):
        raise ValueError("%s is being recorded right now -- stop the acquisition first" % p.name)


def _reports_for(path: str) -> list[dict]:
    out = []
    for rp in sorted(ANALYSIS_DIR.glob("*_report.json"), key=lambda q: q.stat().st_mtime, reverse=True):
        try:
            rep = json.loads(rp.read_text())
        except (OSError, ValueError):
            continue
        if rep.get("dataset", {}).get("path") == path:
            r = {"path": str(rp), "kind": rep.get("kind"), "analysis_timestamp_unix": rep.get("analysis_timestamp_unix"),
                 "model_config": rep.get("model_config")}
            if rep.get("kind") == "cpa":
                r["headline"] = ("rank-1 %.1f%%" % (100 * rep["rank1_fraction"]) if rep.get("oracle_assisted")
                                 and rep.get("rank1_fraction") is not None else "blind attack")
                r.update(_cpa_summary(rep))
            elif rep.get("kind") == "tvla":
                r["headline"] = "max |t| %.2f, %s" % (rep.get("max_abs_t", 0),
                                                       "leakage" if rep.get("leakage_detected") else "no leakage")
            out.append(r)
    return out


def _cpa_summary(rep: dict) -> dict:
    """The numbers a CPA report is compared by (GUI: Compare models)."""
    best = [x["best_corr"] for x in rep.get("results", []) if x.get("best_corr") is not None]
    model, rprev = _report_model(rep)
    return dict({k: rep.get(k) for k in ("n_traces_used", "oracle_assisted", "rank1_fraction",
                                         "median_true_rank", "mean_true_corr")},
                model=model, rprev=rprev, mean_best_corr=float(np.mean(best)) if best else None,
                max_best_corr=float(np.max(best)) if best else None, mtd=(rep.get("curve") or {}).get("mtd"))


def _tags_of(md) -> list[str]:
    try:
        return _clean_tags(json.loads(str(md.get("tags", "[]") or "[]")))
    except ValueError:
        return []


def _row(p: Path, s: dict, n_reports: int) -> dict:
    md = s["metadata"]
    planned, done = s.get("n_traces_planned", s["n_completed"]), s["n_completed"]
    status = "complete" if done >= planned else ("resumable" if s.get("resumable") else "partial")
    rate = s.get("sample_rate_hz") or 0.0
    return {
        "filename": p.name, "path": str(p), "label": str(md.get("label", "") or ""),
        "notes": str(md.get("notes", "") or ""), "tags": _tags_of(md), "mode": s.get("mode", "cpa"), "status": status,
        "simulated": bool(s.get("simulated", False)), "n_completed": done, "n_traces_planned": planned,
        "resumable": bool(s.get("resumable", False)), "created_unix": md.get("created_unix"),
        "strategy": md.get("strategy", ""), "vector_set": md.get("vector_set", ""),
        "n_fixed": s.get("n_fixed"), "n_random": s.get("n_random"),
        "ok_fraction": s.get("ok_fraction"), "k_match_fraction": s.get("k_match_fraction"),
        "n_samples": s.get("n_samples"), "sample_rate_hz": rate,
        "duration_us": round(s["n_samples"] / rate * 1e6, 3) if rate and s.get("n_samples") else None,
        "size_bytes": p.stat().st_size, "has_recipe": "recipe" in md, "n_reports": n_reports,
    }


def api_traces_list(body: dict) -> dict:
    TRACES_DIR.mkdir(parents=True, exist_ok=True)
    trash = bool(body.get("trash", False) and str(body.get("trash")).lower() not in ("0", "false"))
    directory = TRASH_DIR if trash else TRACES_DIR
    directory.mkdir(parents=True, exist_ok=True)
    recording = _recording_path()
    report_counts: dict[str, int] = {}
    for rp in ANALYSIS_DIR.glob("*_report.json"):
        try:
            ds = json.loads(rp.read_text()).get("dataset", {}).get("path", "")
        except (OSError, ValueError):
            continue
        report_counts[ds] = report_counts.get(ds, 0) + 1
    rows = []
    for p in sorted(directory.glob("*.hdf5"), key=lambda q: q.stat().st_mtime, reverse=True):
        if recording == str(p.resolve()):
            with _jobs_lock:
                job = next(j for j in _jobs.values() if j.get("file") and str(Path(j["file"]).resolve()) == recording)
                done, total = job["progress"], job["total"]
            rows.append({"filename": p.name, "path": str(p), "label": "", "notes": "", "tags": [], "mode": job.get("mode", ""),
                         "status": "recording", "n_completed": done, "n_traces_planned": total,
                         "size_bytes": p.stat().st_size, "created_unix": p.stat().st_mtime, "n_reports": 0})
            continue
        try:
            # reports name the campaign by its library path, also while it is in the trash
            rows.append(_row(p, HS.summarize(p), report_counts.get(str(TRACES_DIR / p.name), 0)))
        except Exception:  # noqa: BLE001 - unreadable/partial file: still list it
            rows.append({"filename": p.name, "path": str(p), "status": "unreadable", "mode": "?", "tags": [],
                         "size_bytes": p.stat().st_size, "created_unix": p.stat().st_mtime, "n_reports": 0})
    usage = shutil.disk_usage(str(TRACES_DIR))
    total = sum(q.stat().st_size for q in TRACES_DIR.glob("*.hdf5"))
    return {"files": rows, "trash": trash,
            "storage": {"n_files": len(list(TRACES_DIR.glob("*.hdf5"))), "bytes": total,
                        "n_trash": len(list(TRASH_DIR.glob("*.hdf5"))), "disk_free": usage.free,
                        "directory": str(TRACES_DIR)}}


def api_traces_detail(body: dict) -> dict:
    p = _campaign(body["path"])
    _not_recording(p)
    s = HS.summarize(p)
    row = _row(p, s, 0)
    md = s["metadata"]

    def js(key):
        try:
            return json.loads(str(md.get(key, "null")))
        except ValueError:
            return None
    with HS.TraceReader(p) as r:
        ok = r.ok
        cyc = r.cycles[ok] if ok.any() else r.cycles
        values, counts = np.unique(cyc, return_counts=True)
        order = np.argsort(-counts)[:8]
        cycles = [{"cycles": int(values[i]), "count": int(counts[i])} for i in order]
        n_bad = int((~ok).sum())
    row.update({
        "recipe": js("recipe"), "board": js("board"), "scope": js("scope"),
        "core_mhz": md.get("core_mhz"), "cycles": cycles, "cycles_distinct": int(values.size),
        "n_failed_checks": n_bad, "dk_hash": s.get("dk_hash"), "reports": _reports_for(str(p)),
        "axis": _axis(row["sample_rate_hz"] or 0.0, int(row["n_samples"] or 0)),
    })
    return row


def _set_trace_meta(p: Path, label=None, notes=None, tags=None) -> None:
    import h5py
    with h5py.File(str(p), "r+") as f:
        attrs = f["metadata"].attrs
        if label is not None:
            attrs["label"] = str(label)[:120]
        if notes is not None:
            attrs["notes"] = str(notes)[:4000]
        if tags is not None:
            attrs["tags"] = json.dumps(_clean_tags(tags))


def api_traces_update(body: dict) -> dict:
    """Name, notes and/or tags of one campaign."""
    p = _campaign(body["path"])
    _not_recording(p)
    tags = _clean_tags(body["tags"]) if "tags" in body else None
    _set_trace_meta(p, body.get("label"), body.get("notes"), tags)
    log("updated %s of %s" % ("/".join(k for k in ("label", "notes", "tags") if k in body), p.name))
    return {"ok": True, "tags": tags}


def api_traces_tag(body: dict) -> dict:
    """Add and/or remove tags on several campaigns."""
    done = []
    for path in _paths(body):
        p = _campaign(path)
        _not_recording(p)
        with HS.TraceReader(p) as r:
            current = _tags_of(r.metadata)
        _set_trace_meta(p, tags=_edit_tags(current, body.get("add", []), body.get("remove", [])))
        done.append(p.name)
    log("tags changed on %d campaign(s)" % len(done))
    return {"done": done}


def api_traces_delete(body: dict) -> dict:
    """Move campaigns to the trash (data/traces/.trash/); they can be restored."""
    done = []
    for path in _paths(body):
        p = _campaign(path)
        _not_recording(p)
        done.append(str(_move_unique(p, TRASH_DIR)))
    log("moved %d campaign(s) to the trace library's trash" % len(done))
    return {"trashed": done}


def api_traces_restore(body: dict) -> dict:
    done = []
    for path in _paths(body):
        p = _campaign(path, TRASH_DIR)
        if (TRACES_DIR / p.name).exists():
            raise ValueError("%s already exists in the library" % p.name)
        shutil.move(str(p), str(TRACES_DIR / p.name))
        done.append(p.name)
    log("restored %s from the trash" % ", ".join(done))
    return {"restored": done}


def _purge_campaign(p: Path) -> int:
    """Delete a trashed campaign file and the analysis reports made from it."""
    original = str(TRACES_DIR / p.name)
    n = 0
    for rp in ANALYSIS_DIR.glob("*_report.json"):
        try:
            rep = json.loads(rp.read_text())
        except (OSError, ValueError):
            continue
        if rep.get("dataset", {}).get("path") == original:
            for extra in (rep.get("results_csv"), (rep.get("curve") or {}).get("curve_csv")):
                if extra and Path(extra).is_file() and ANALYSIS_DIR.resolve() in Path(extra).resolve().parents:
                    Path(extra).unlink()
            rp.unlink()
            n += 1
    p.unlink()
    return n


def api_traces_purge(body: dict) -> dict:
    """Permanently delete campaigns that are in the trash (and their reports)."""
    files = reports = 0
    for path in _paths(body):
        reports += _purge_campaign(_campaign(path, TRASH_DIR))
        files += 1
    log("permanently deleted %d campaign(s) and %d analysis report(s)" % (files, reports))
    return {"deleted": files, "reports": reports}


def api_traces_empty_trash(_body: dict) -> dict:
    TRASH_DIR.mkdir(parents=True, exist_ok=True)
    files = reports = 0
    for p in list(TRASH_DIR.glob("*.hdf5")):
        reports += _purge_campaign(p)
        files += 1
    log("emptied the trace library's trash: %d campaign(s), %d report(s)" % (files, reports))
    return {"deleted": files, "reports": reports}


def api_traces_download(body: dict):
    p = _campaign(body["path"])
    _not_recording(p)
    return Download(p.name, "application/x-hdf5", file=p)


def api_traces_export(body: dict):
    """A slice of a campaign as a self-contained .npz (traces + per-trace data + metadata)."""
    p = _campaign(body["path"])
    _not_recording(p)
    start = max(0, int(body.get("start", 0) or 0))
    count = max(1, int(body.get("count", 1000) or 1000))
    with HS.TraceReader(p) as r:
        stop = min(r.n_completed, start + count)
        if start >= stop:
            raise ValueError("start is past the last completed trace (%d)" % r.n_completed)
        sl = slice(start, stop)
        arrays = {"traces": r.f["traces"][sl], "time_s": r.time_axis, "ciphertext": r.f["intermediates/ct"][sl],
                  "cycles": r.f["intermediates/cycles"][sl], "output_k": r.f["intermediates/output"][sl],
                  "ok": r.f["intermediates/ok"][sl], "index": np.arange(start, stop)}
        if "class" in r.f["intermediates"]:
            arrays["class"] = r.f["intermediates/class"][sl]
        if "k_expected" in r.f["intermediates"]:
            arrays["expected_k"] = r.f["intermediates/k_expected"][sl]
        meta = json.dumps(r.metadata, default=_json_default)
    buf = io.BytesIO()
    np.savez(buf, metadata=np.array(meta), **arrays)
    return Download("%s_%d-%d.npz" % (p.stem, start, stop - 1), "application/octet-stream", buf.getvalue())


# =============================================================================
# CPA / TVLA analysis
# =============================================================================
def _csv_paths(h5_path: str, job_id: str) -> tuple[Path, Path]:
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    stem = Path(h5_path).stem
    return (ANALYSIS_DIR / ("%s_%s_results.csv" % (stem, job_id)),
            ANALYSIS_DIR / ("%s_%s_curve.csv" % (stem, job_id)))


def _model_version(model: str = "") -> str:
    """Content hash of the model file, models/base.py and mlkem_ref.py: which
    version of the leakage model produced a report."""
    import mlkem_ref
    files = [Path(LM.__file__).with_name("base.py"), Path(mlkem_ref.__file__)]
    if model:
        files.insert(0, Path(LM.get(model).source))
    h = hashlib.sha256(b"".join(f.read_bytes() for f in files)).hexdigest()[:12]
    return "%s:%s" % (model or "models", h)


def _save_analysis_report(kind: str, file: str, job_id: str, summary: dict) -> str:
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        dataset = HS.summarize(file)
    except Exception:  # noqa: BLE001
        dataset = {"path": file}
    report = {"kind": kind, "job_id": job_id, "dataset": dataset, "model_version": _model_version(summary.get("model", "")),
              "analysis_timestamp_unix": time.time(), **summary}
    out_path = ANALYSIS_DIR / ("%s_%s_%s_report.json" % (Path(file).stem, job_id, kind))
    report["report_path"] = str(out_path)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2, default=_json_default)
    return str(out_path)


def api_results_list(body: dict) -> dict:
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    file_filter = str(body.get("file", "")).strip()
    out = []
    for p in sorted(ANALYSIS_DIR.glob("*_report.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            rep = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        dataset_path = rep.get("dataset", {}).get("path", "")
        if file_filter and dataset_path != file_filter:
            continue
        row = {"path": str(p), "filename": p.name, "kind": rep.get("kind"),
               "dataset_path": dataset_path,
               "dataset_filename": Path(dataset_path).name if dataset_path else "",
               "analysis_timestamp_unix": rep.get("analysis_timestamp_unix"),
               "model_config": rep.get("model_config")}
        if rep.get("kind") == "cpa":
            row.update(_cpa_summary(rep))
        out.append(row)
    return {"results": out}


def _inside(path: Path, directory: Path) -> Path:
    if directory.resolve() not in path.resolve().parents or not path.is_file():
        raise ValueError("no such file in %s: %s" % (directory.name, path.name))
    return path


def api_results_load(body: dict) -> dict:
    rep = json.loads(_inside(Path(body["path"]), ANALYSIS_DIR).read_text())
    if rep.get("kind") == "cpa":
        rep["model"], rep["rprev"] = _report_model(rep)
    return rep


def api_results_delete(body: dict) -> dict:
    """Delete one saved report and the CSV files it produced."""
    p = _inside(Path(body["path"]), ANALYSIS_DIR)
    rep = json.loads(p.read_text())
    removed = [p.name]
    for extra in (rep.get("results_csv"), (rep.get("curve") or {}).get("curve_csv")):
        if extra:
            q = Path(extra)
            if q.is_file() and ANALYSIS_DIR.resolve() in q.resolve().parents:
                q.unlink()
                removed.append(q.name)
    p.unlink()
    log("deleted analysis report %s" % ", ".join(removed))
    return {"removed": removed}


def _dk_ref(file: str, source: str) -> bytes | None:
    """The key for an oracle-assisted evaluation: none, the campaign's own
    vector set (rebuilt from the seed in its recipe), or a dk file path."""
    source = (source or "").strip()
    if not source:
        return None
    if source == "vector_set":
        with HS.TraceReader(file) as r:
            recipe = r.metadata.get("recipe")
            dk_hash = r.dk_hash
        if not recipe:
            raise ValueError("%s has no vector-set recipe; give a dk file instead" % Path(file).name)
        dk = A.dk_from_recipe(json.loads(str(recipe)))
        if hashlib.sha3_256(dk).digest() != dk_hash:
            raise ValueError("the rebuilt key does not match this file's dk_hash")
        return dk
    if not Path(source).is_file():
        raise ValueError("no such dk file: %s" % source)
    return Path(source).read_bytes()


def _run_cpa_analysis_job(job_id: str, file: str, model: str, rprev: str,
                          max_traces: int, dk_source: str, curve: bool) -> None:
    try:
        dk_ref = _dk_ref(file, dk_source)
        _update_job(job_id, message="running CPA analysis (%s%s)..." % (model, ", R_prev " + rprev if rprev else ""))
        summary = CA.run_attack(file, model, rprev, max_traces, None, dk_ref)
        summary["model_config"] = {"model": model, "target": summary["target"], "rprev": rprev,
                                   "max_traces_requested": max_traces, "oracle_assisted": dk_ref is not None}
        results_csv, curve_csv = _csv_paths(file, job_id)
        CA.write_results_csv(summary["results"], str(results_csv))
        summary["results_csv"] = str(results_csv)
        if curve:
            if dk_ref is None:
                raise ValueError("the key-rank curve needs the true key (oracle-assisted evaluation)")
            _update_job(job_id, message="computing key-rank-vs-trace-count curve...")
            c = CA.run_curve(file, model, rprev, max_traces, dk_ref)
            CA.write_curve_csv(c, str(curve_csv))
            c["curve_csv"] = str(curve_csv)
            summary["curve"] = c
        summary["report_path"] = _save_analysis_report("cpa", file, job_id, summary)
        _update_job(job_id, status="done", result=summary, message="done")
        log("CPA analysis job %s finished (%s)" % (job_id, Path(file).name))
    except (Exception, SystemExit) as e:  # noqa: BLE001 - cpa_attack raises SystemExit for CLI errors
        _update_job(job_id, status="error", error="%s: %s" % (type(e).__name__, e))
        log("[error] CPA analysis job %s: %s" % (job_id, e))


def _model_of(body: dict) -> tuple[str, str]:
    """(model, R_prev) of a request: a model name (old preset names and, for
    old "custom" reports, a target register also work) and an R_prev it accepts."""
    name = LM.resolve(str(body.get("model", "") or ""), str(body.get("target", "") or ""))
    if not name and not body.get("model") and not body.get("target"):
        name = _default_model()[0]
    if not name:
        raise ValueError("no such leakage model: %s" % (body.get("model") or body.get("target")))
    model = LM.get(name, reload=True)
    return model.name, model.resolve_rprev(str(body.get("rprev", "") or ""))


def api_analysis_cpa_start(body: dict) -> dict:
    file = body["file"]
    if not Path(file).is_file():
        raise ValueError("no such file: %s" % file)
    model, rprev = _model_of(body)
    dk_source = str(body.get("dk_ref", body.get("dk_ref_path", ""))).strip()
    job_id = _new_job("cpa_analysis")
    threading.Thread(target=_run_cpa_analysis_job, daemon=True,
                     args=(job_id, file, model, rprev, int(body.get("max_traces", 0) or 0),
                           dk_source, bool(body.get("curve", False)))).start()
    log("started CPA analysis job %s: %s model=%s%s%s" % (job_id, Path(file).name, model,
        " R_prev=" + rprev if rprev else "", " (oracle-assisted)" if dk_source else ""))
    return {"job_id": job_id}


def _run_tvla_analysis_job(job_id: str, file: str, max_traces: int) -> None:
    try:
        _update_job(job_id, message="running TVLA (Welch's t-test)...")
        result = TVA.chart_data(file, max_traces)
        result["report_path"] = _save_analysis_report("tvla", file, job_id, result)
        _update_job(job_id, status="done", result=result, message="done")
        log("TVLA analysis job %s finished (%s)" % (job_id, Path(file).name))
    except (Exception, SystemExit) as e:  # noqa: BLE001
        _update_job(job_id, status="error", error="%s: %s" % (type(e).__name__, e))
        log("[error] TVLA analysis job %s: %s" % (job_id, e))


def api_analysis_tvla_start(body: dict) -> dict:
    file = body["file"]
    if not Path(file).is_file():
        raise ValueError("no such file: %s" % file)
    job_id = _new_job("tvla_analysis")
    threading.Thread(target=_run_tvla_analysis_job, args=(job_id, file, int(body.get("max_traces", 0) or 0)),
                     daemon=True).start()
    log("started TVLA analysis job %s: %s" % (job_id, Path(file).name))
    return {"job_id": job_id}


def api_analysis_correlation_curve(body: dict) -> dict:
    file = body["file"]
    if not Path(file).is_file():
        raise ValueError("no such file: %s" % file)
    dk_source = str(body.get("dk_ref", body.get("dk_ref_path", ""))).strip()
    try:
        model, rprev = _model_of(body)
        return CA.correlation_curve_for_coefficient(
            file, model, rprev, int(body.get("poly", 0) or 0), int(body.get("coeff", 0) or 0),
            int(body.get("max_traces", 0) or 0), _dk_ref(file, dk_source))
    except SystemExit as e:
        raise ValueError(str(e))


def api_analysis_download(body: dict):
    p = _inside(Path(body["path"]), ANALYSIS_DIR)
    return Download(p.name, "text/csv; charset=utf-8" if p.suffix == ".csv" else "application/json",
                    p.read_bytes())


def api_analysis_raw_traces(body: dict) -> dict:
    """Measured waveforms from an HDF5 campaign for the trace viewer.

    Selection: explicit ``indices``, or the first ``count`` traces from
    ``start`` (optionally only class ``cls`` for TVLA files). Each trace is
    decimated with the peak-preserving envelope. With ``stats``, also the
    per-sample mean +- std (per class for TVLA) over ``stats_n`` traces.
    """
    file = body["file"]
    if not Path(file).is_file():
        raise ValueError("no such file: %s" % file)
    max_points = min(20000, max(500, int(body.get("max_points", 4000) or 4000)))
    with HS.TraceReader(file) as r:
        n_avail, n_samples = r.n_completed, int(r.f["traces"].shape[1])
        if n_avail == 0:
            raise ValueError("the file has no completed traces yet")
        rate = r.sample_rate
        ok_all = r.ok
        cls_all = r.classes if r.mode == "tvla" else None
        cls_sel = body.get("cls", "")
        if body.get("indices"):
            idx = [int(i) for i in body["indices"] if 0 <= int(i) < n_avail]
        else:
            start = max(0, int(body.get("start", 0) or 0))
            count = min(20, max(1, int(body.get("count", 4) or 4)))
            cand = np.arange(n_avail)
            if cls_all is not None and cls_sel in ("0", "1", 0, 1):
                cand = cand[cls_all == int(cls_sel)]
            idx = [int(i) for i in cand[start:start + count]]
        if not idx:
            raise ValueError("no traces match (file has %d)" % n_avail)
        idx = idx[:20]
        ds = r.f["traces"]
        cycles, cts = r.cycles, r.ciphertexts
        traces = [{"index": i, "ok": bool(ok_all[i]), "cycles": int(cycles[i]),
                   "class": int(cls_all[i]) if cls_all is not None else None,
                   "ct_preview": bytes(cts[i][:12]).hex() + "…",
                   **_series(ds[i], rate, max_points)} for i in idx]
        stats = None
        if body.get("stats"):
            stats_n = min(n_avail, max(2, int(body.get("stats_n", 500) or 500)))
            block = ds[:stats_n].astype(np.float64)
            good = ok_all[:stats_n]
            groups = {"all": good} if cls_all is None else {
                "fixed": good & (cls_all[:stats_n] == 0), "random": good & (cls_all[:stats_n] == 1)}
            stats = {}
            for name, mask in groups.items():
                if mask.sum() < 2:
                    continue
                sel = block[mask]
                mean, std = sel.mean(axis=0), sel.std(axis=0)
                m = _series(mean, rate, max_points)
                hi = _series(mean + std, rate, max_points)
                lo = _series(mean - std, rate, max_points)
                stats[name] = {"n": int(mask.sum()), "x": m["x"], "mean": m["y"], "hi": hi["y"], "lo": lo["y"]}
    return {"file": file, "n_completed": n_avail, "mode": "tvla" if cls_all is not None else "cpa",
            "axis": _axis(rate, n_samples), "traces": traces, "stats": stats}


def _report_model(rep: dict) -> tuple[str, str]:
    """(model, R_prev) a CPA report was made with, also for reports from before models/."""
    mc = rep.get("model_config") or {}
    name = LM.resolve(mc.get("model") or rep.get("model") or mc.get("preset") or "", mc.get("target") or rep.get("target") or "")
    rprev = mc.get("rprev", rep.get("rprev", ""))
    if name == "hw_scalar":
        rprev = ""          # older HW reports said "zero": the same hypothesis
    return name, rprev or ""


def api_analysis_success_rate(body: dict) -> dict:
    """Success rate vs trace count across >= 2 independent, oracle-assisted
    campaigns analysed with the same model and R_prev -- never for a single one."""
    model, rprev = _model_of(body)
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    curves, dataset_paths = [], []
    for p in sorted(ANALYSIS_DIR.glob("*_cpa_report.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            rep = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        curve = rep.get("curve")
        if not curve or not curve.get("checkpoints") or _report_model(rep) != (model, rprev):
            continue
        ds_path = rep.get("dataset", {}).get("path", "")
        if not ds_path or ds_path in dataset_paths:
            continue
        dataset_paths.append(ds_path)
        curves.append(curve["checkpoints"])
    if len(curves) < 2:
        return {"available": False, "model": model, "rprev": rprev, "n_campaigns": len(curves),
                "reason": "need >=2 independent campaigns analysed oracle-assisted with --curve at "
                          "this model config; have %d" % len(curves)}
    grid = sorted({row["n_traces"] for ckpts in curves for row in ckpts})
    stacked = np.array([np.interp(grid, [r["n_traces"] for r in c], [r["rank1_fraction"] for r in c],
                                  left=np.nan, right=np.nan) for c in curves])
    mean, lo, hi = np.nanmean(stacked, axis=0), np.nanmin(stacked, axis=0), np.nanmax(stacked, axis=0)

    def f(v):
        return float(v) if np.isfinite(v) else None
    rows = [{"n_traces": n, "mean_rank1_fraction": f(mean[i]), "min_rank1_fraction": f(lo[i]),
             "max_rank1_fraction": f(hi[i])} for i, n in enumerate(grid)]
    return {"available": True, "model": model, "rprev": rprev, "n_campaigns": len(curves),
            "dataset_paths": dataset_paths, "checkpoints": rows}


ROUTES = {
    ("GET", "/api/state"): api_state,
    ("GET", "/api/log"): api_log,
    ("GET", "/api/fpga/ports"): api_fpga_ports,
    ("POST", "/api/fpga/connect"): api_fpga_connect,
    ("POST", "/api/fpga/disconnect"): api_fpga_disconnect,
    ("POST", "/api/fpga/preflight"): api_fpga_preflight,
    ("POST", "/api/fpga/read_reg"): api_fpga_read_reg,
    ("POST", "/api/fpga/write_reg"): api_fpga_write_reg,
    ("POST", "/api/fpga/read_data"): api_fpga_read_data,
    ("POST", "/api/fpga/write_data"): api_fpga_write_data,
    ("POST", "/api/fpga/decaps"): api_fpga_decaps,
    ("GET", "/api/fpga/settings"): api_fpga_settings,
    ("POST", "/api/fpga/settings"): api_fpga_settings,
    ("POST", "/api/fpga/reprobe"): api_fpga_reprobe,
    ("POST", "/api/fpga/read_all"): api_fpga_read_all,
    ("POST", "/api/fpga/step"): api_fpga_step,
    ("POST", "/api/acquire/trigger_check"): api_acquire_trigger_check,
    ("GET", "/api/scope/resources"): api_scope_resources,
    ("POST", "/api/scope/connect"): api_scope_connect,
    ("POST", "/api/scope/disconnect"): api_scope_disconnect,
    ("POST", "/api/scope/save_config"): api_scope_save_config,
    ("GET", "/api/model/list"): api_model_list,
    ("POST", "/api/model/selftest"): api_model_selftest,
    ("POST", "/api/model/save_default"): api_model_save_default,
    ("GET", "/api/jobs/status"): api_job_status,
    ("POST", "/api/jobs/stop"): api_job_stop,
    ("GET", "/api/vectors/info"): api_vectors_info,
    ("POST", "/api/vectors/generate"): api_vectors_generate,
    ("GET", "/api/vectors/list"): api_vectors_list,
    ("POST", "/api/vectors/view"): api_vectors_view,
    ("POST", "/api/vectors/row"): api_vectors_row,
    ("GET", "/api/vectors/download"): api_vectors_download,
    ("POST", "/api/vectors/verify_software"): api_vectors_verify_software,
    ("POST", "/api/vectors/verify_board"): api_vectors_verify_board,
    ("GET", "/api/acquire/info"): api_acquire_info,
    ("POST", "/api/acquire/start"): api_acquire_start,
    ("GET", "/api/acquire/live"): api_acquire_live,
    ("POST", "/api/acquire/capture_one"): api_acquire_capture_one,
    ("GET", "/api/datasets"): api_datasets,
    ("GET", "/api/traces/list"): api_traces_list,
    ("POST", "/api/traces/detail"): api_traces_detail,
    ("POST", "/api/traces/update"): api_traces_update,
    ("POST", "/api/traces/delete"): api_traces_delete,
    ("POST", "/api/traces/restore"): api_traces_restore,
    ("GET", "/api/traces/download"): api_traces_download,
    ("GET", "/api/traces/export"): api_traces_export,
    ("POST", "/api/analysis/cpa/start"): api_analysis_cpa_start,
    ("POST", "/api/analysis/tvla/start"): api_analysis_tvla_start,
    ("GET", "/api/analysis/download"): api_analysis_download,
    ("POST", "/api/analysis/correlation_curve"): api_analysis_correlation_curve,
    ("POST", "/api/analysis/raw_traces"): api_analysis_raw_traces,
    ("POST", "/api/analysis/success_rate"): api_analysis_success_rate,
    ("POST", "/api/results/list"): api_results_list,
    ("POST", "/api/results/load"): api_results_load,
    ("POST", "/api/results/delete"): api_results_delete,
    ("POST", "/api/traces/tag"): api_traces_tag,
    ("POST", "/api/traces/purge"): api_traces_purge,
    ("POST", "/api/traces/empty_trash"): api_traces_empty_trash,
    ("POST", "/api/vectors/update"): api_vectors_update,
    ("POST", "/api/vectors/tag"): api_vectors_tag,
    ("POST", "/api/vectors/delete"): api_vectors_delete,
    ("POST", "/api/vectors/restore"): api_vectors_restore,
    ("POST", "/api/vectors/purge"): api_vectors_purge,
    ("POST", "/api/vectors/empty_trash"): api_vectors_empty_trash,
}


# =============================================================================
# HTTP plumbing
# =============================================================================
class Handler(BaseHTTPRequestHandler):
    server_version = "CPAToolkit/2.0"

    def log_message(self, fmt, *args):  # noqa: A003 - silence per-request console spam
        pass

    def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _stream_file(self, path: Path, ctype: str, extra: dict) -> None:
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(path.stat().st_size))
        for k, v in extra.items():
            self.send_header(k, v)
        self.end_headers()
        with open(path, "rb") as f:
            shutil.copyfileobj(f, self.wfile, 1 << 20)

    def _send_json(self, status: int, obj) -> None:
        self._send(status, json.dumps(obj, default=_json_default).encode("utf-8"), "application/json")

    def _dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        route = ROUTES.get((method, parsed.path))
        if route is None:
            if method == "GET":
                self._serve_static(parsed.path)
            else:
                self._send_json(404, {"error": "no such route"})
            return
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw) if raw else {}
            for k, v in parse_qs(parsed.query).items():
                body.setdefault(k, v[0])
            result = route(body)
            if isinstance(result, Download):
                disp = {"Content-Disposition": "attachment; filename*=UTF-8''%s" % quote(result.filename)}
                if result.file is not None:
                    self._stream_file(result.file, result.content_type, disp)
                else:
                    self._send(200, result.data, result.content_type, disp)
            else:
                self._send_json(200, result)
        except (ValueError, KeyError, T.LinkError) as e:
            self._send_json(400, {"error": str(e).strip("'")})
        except (Exception, SystemExit) as e:  # noqa: BLE001 - surface it to the page, never crash
            log("[error] %s: %s" % (type(e).__name__, e))
            self._send_json(502, {"error": "%s: %s" % (type(e).__name__, e)})

    def _serve_static(self, path: str) -> None:
        rel = "index.html" if path in ("/", "") else path[len("/static/"):] if path.startswith("/static/") \
            else path.lstrip("/")
        target = (STATIC_DIR / rel).resolve()
        if STATIC_DIR.resolve() not in target.parents or not target.is_file():
            self._send_json(404, {"error": "not found: %s" % path})
            return
        self._send(200, target.read_bytes(), CONTENT_TYPES.get(target.suffix, "application/octet-stream"))

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    for d in (TRACES_DIR, ANALYSIS_DIR, VECTORS_DIR):
        d.mkdir(parents=True, exist_ok=True)
    CN.LinkMonitor(board, busy=lambda: _busy() is not None)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.daemon_threads = True
    url = "http://127.0.0.1:%d/" % args.port
    print("CPA Toolkit: %s  (Ctrl-C to stop)" % url)
    if not args.no_browser:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        board.disconnect()
        scope_conn.disconnect()
        server.server_close()


if __name__ == "__main__":
    main()
