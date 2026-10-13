#!/usr/bin/env python3
"""Setup GUI: FPGA Communicator + Oscilloscope Setup, served as a local web page.

Cross-platform by construction: the only requirements are the Python
standard library (this file adds zero new dependencies -- not even Flask)
and a browser, both of which exist identically on Windows/macOS/Linux. This
replaces an earlier Tkinter version that crashed on this Mac because
Apple's system Tk.framework refuses to initialise on this exact macOS point
release (unrelated to this project's code); a browser has no such check.

This is a *setup and smoke-test* tool, not the acquisition driver -- it
answers "is the link to the board up, is the link to the scope up, what do
they report back" before you run a real campaign with ``acquire_cpa.py``.

    python3 web_gui.py                  # starts on http://127.0.0.1:8765
    python3 web_gui.py --port 8900      # a different port
    python3 web_gui.py --no-browser      # don't auto-open a browser tab

Binds to 127.0.0.1 only -- this controls real hardware (serial port,
oscilloscope), so it is never exposed on the network, only to this machine.
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np

import acquire_cpa as A
import config as C
import cpa_attack as CA
import hdf5_trace_store as HS
import mlkem_ref as R
import model_registry as M
import scope as S
import tvla_analysis as TV
import uart_target as T
from gui_config_io import save_config_values

STATIC_DIR = Path(__file__).with_name("web_static")
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                  ".js": "application/javascript; charset=utf-8"}
TRACES_DIR = Path(A.ROOT) / "sim_result" / "traces"
ANALYSIS_DIR = Path(A.ROOT) / "sim_result" / "analysis"
#: Offline-generated acquisition recipes ("Generate Dataset" step) -- see the
#: Trace Acquisition tab section below. Never holds dk or ciphertexts, only
#: the (seed, strategy, poly, pair, ...) parameters that describe how to
#: build a dataset to this spec. NOT a promise of byte-identical ciphertexts
#: on every use, though: only `dk` is actually seed-deterministic through
#: `pqc-testkit sca gen` (the "structured" strategy's ciphertexts are, via a
#: seeded numpy RNG in Python, but "valid"/"invalid" are not -- their random
#: class and shuffle order use real OS randomness on the Go side). The
#: authoritative ciphertext sequence for a campaign is whatever gets
#: generated once, at the start of "Acquire Real Traces", and immediately
#: committed into that file's own /intermediates/ct -- never regenerated
#: again after that, including across a stop/resume (see
#: hdf5_trace_store.TraceWriter's class docstring and
#: acquire_cpa.run_campaign's docstring for exactly why this matters).
INPUTSETS_DIR = Path(A.ROOT) / "sim_result" / "inputsets"

# --- shared hardware state (one browser tab talking to one board/scope) -----
_lock = threading.Lock()
# `busy`: None, or the job_id of a running acquisition. While set, nothing
# else may use `target`/`sc` directly (same serial port / same scope) --
# see _require_target()/_require_scope() below. The GUI's own acquisition
# job sets/clears it; it does not stop *other* programs from using the port.
_state = {"target": None, "sc": None, "model": None, "busy": None}
_log: list[str] = []
_log_lock = threading.Lock()

# --- background jobs (acquisition campaigns, CPA/TVLA analysis runs) --------
_jobs_lock = threading.Lock()
_jobs: dict[str, dict] = {}


def _new_job(kind: str) -> str:
    job_id = uuid.uuid4().hex[:12]
    with _jobs_lock:
        _jobs[job_id] = {"kind": kind, "status": "running", "progress": 0, "total": 0,
                          "message": "starting...", "result": None, "error": None,
                          "started_unix": time.time()}
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


def _json_default(o):
    """``json.dumps`` fallback for values that round-tripped through numpy/h5py.

    ``TraceReader.metadata`` (hdf5_trace_store.py) reads HDF5 attribute values
    back via h5py, which returns numpy scalar types even for attrs that were
    written from plain Python int/float/bool -- e.g. ``n_traces`` comes back
    as ``numpy.int64``, not ``int``. ``numpy.float64``/``numpy.bool_`` happen
    to be JSON-safe on CPython (float64 subclasses float), but numpy.integer
    does not subclass int, so a bare ``json.dumps`` chokes on it the moment a
    job's result (e.g. api_acquire_status after a campaign finishes) embeds
    that metadata dict. Same idea as the ``default=`` already used by
    cpa_attack.py/tvla_analysis.py's own json.dump() calls, extended to numpy
    bools/arrays too since GUI responses carry more shapes than those CLI
    reports do.
    """
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    return str(o)


def log(msg: str) -> None:
    with _log_lock:
        _log.append("[%s] %s" % (time.strftime("%H:%M:%S"), msg))
        del _log[:-500]  # keep the last 500 lines; this is a live setup tool, not an audit trail


# --- API handlers: each returns a JSON-able dict, or raises for an error response ---
def api_state(_body: dict) -> dict:
    with _lock:
        target, sc = _state["target"], _state["sc"]
    return {
        "fpga": {
            "connected": target is not None,
            "info": target.info() if target is not None else None,
            "port_default": C.SERIAL_PORT, "baud_default": C.BAUD,
            "registers": sorted(T.REG),
        },
        "scope": {
            "connected": sc is not None,
            "info": sc.info() if sc is not None else None,
            "ip_default": C.SCOPE_IP, "placeholder_ip": C.SCOPE_PLACEHOLDER_IP,
            "channel_default": C.SCOPE_CHANNEL, "dialect_default": C.SCOPE_DIALECT,
            "timeout_default": C.SCOPE_TIMEOUT_MS,
        },
    }


def api_log(body: dict) -> dict:
    since = int(body.get("since", 0))
    with _log_lock:
        lines = _log[since:]
        total = len(_log)
    return {"lines": lines, "next": total}


def api_fpga_ports(_body: dict) -> dict:
    return {"ports": [{"device": d, "description": desc} for d, desc in T.list_ports()]}


def api_fpga_connect(body: dict) -> dict:
    port, baud = body["port"], int(body.get("baud", C.BAUD))
    target = T.Target.open(port, baud)
    with _lock:
        if _state["target"] is not None:
            _state["target"].close()
        _state["target"] = target
    info = target.info()
    log("FPGA connected: %s @ %d baud -- %s" % (port, baud, info))
    return {"info": info}


def api_fpga_disconnect(_body: dict) -> dict:
    with _lock:
        if _state["target"] is not None:
            _state["target"].close()
            _state["target"] = None
    log("FPGA disconnected")
    return {}


def _require_target():
    with _lock:
        target, busy = _state["target"], _state["busy"]
    if target is None:
        raise ValueError("not connected to the board")
    if busy:
        raise ValueError("board is busy running acquisition job %s -- wait for it to finish "
                         "(the serial port can't be shared between two callers at once)" % busy)
    return target


def _require_scope():
    with _lock:
        sc, busy = _state["sc"], _state["busy"]
    if sc is None:
        raise ValueError("not connected to the scope")
    if busy:
        raise ValueError("scope is busy running acquisition job %s -- wait for it to finish" % busy)
    return sc


def api_fpga_preflight(_body: dict) -> dict:
    checks = _require_target().preflight()
    for name, ok, detail in checks:
        log(("  OK  " if ok else "  FAIL") + " " + name + " -- " + detail)
    return {"checks": [{"name": n, "ok": ok, "detail": d} for n, ok, d in checks]}


def api_fpga_read_reg(body: dict) -> dict:
    name = body["name"]
    val = _require_target().link.rd(name)
    log("READ  %-12s = 0x%08X (%d)" % (name, val, val))
    return {"name": name, "value": val, "hex": "0x%08X" % val}


def api_fpga_write_reg(body: dict) -> dict:
    name, val = body["name"], int(body["value"], 0) if isinstance(body["value"], str) else int(body["value"])
    _require_target().link.wr(name, val)
    log("WRITE %-12s <- 0x%08X" % (name, val))
    return {"name": name, "value": val}


def api_fpga_read_data(body: dict) -> dict:
    """Raw bytes from the 8 KB data buffer -- what a register read never shows."""
    offset = int(body["offset"], 0) if isinstance(body["offset"], str) else int(body["offset"])
    length = int(body["length"], 0) if isinstance(body["length"], str) else int(body["length"])
    if not 1 <= length <= 8192:
        raise ValueError("length must be 1..8192")
    data = _require_target().link.read_data(offset, length)
    log("READ_DATA  offset=0x%04X len=%d -> %s" % (offset, length, data[:32].hex() + ("..." if length > 32 else "")))
    return {"offset": offset, "length": length, "hex": data.hex()}


def api_fpga_write_data(body: dict) -> dict:
    offset = int(body["offset"], 0) if isinstance(body["offset"], str) else int(body["offset"])
    hex_str = body["hex"].strip().replace(" ", "")
    try:
        data = bytes.fromhex(hex_str)
    except ValueError as e:
        raise ValueError("hex data: %s" % e)
    if not data:
        raise ValueError("no data to write")
    _require_target().link.write_data(offset, data)
    log("WRITE_DATA offset=0x%04X len=%d <- %s" % (offset, len(data), data[:32].hex() + ("..." if len(data) > 32 else "")))
    return {"offset": offset, "length": len(data)}


def api_fpga_decaps(body: dict) -> dict:
    """Run a real Decaps and return exactly what comes back over UART.

    ``dk``/``ct`` are caller-supplied hex (e.g. random bytes for a protocol
    smoke test -- they will not be a valid key/ciphertext, so ERROR_CODE 2
    is the expected, successful outcome of that test: it proves the round
    trip works, not that decryption succeeded).
    """
    dk = bytes.fromhex(body["dk_hex"].strip().replace(" ", ""))
    ct = bytes.fromhex(body["ct_hex"].strip().replace(" ", ""))
    if len(dk) != T.DK_LEN or len(ct) != T.CT_LEN:
        raise ValueError("dk must be %d bytes (got %d), ct must be %d bytes (got %d)"
                         % (T.DK_LEN, len(dk), T.CT_LEN, len(ct)))
    resend_dk = bool(body.get("resend_dk", True))
    out = _require_target().decaps(dk, ct, resend_dk=resend_dk)
    log("DECAPS: %s -- output %s" % (out.describe(), out.output.hex() or "(none)"))
    return {"ok": out.ok, "status": out.status, "error_code": out.error_code, "cycles": out.cycles,
            "describe": out.describe(), "output_hex": out.output.hex(), "wall_time": out.wall_time}


def api_scope_resources(_body: dict) -> dict:
    found = S.list_resources()
    log("found %d VISA resource(s)" % len(found))
    return {"resources": [{"resource": r, "idn": idn} for r, idn in found]}


def api_scope_connect(body: dict) -> dict:
    ip = body["ip"].strip()
    if ip == C.SCOPE_PLACEHOLDER_IP:
        log("[warn] SCOPE_IP is still the config.py placeholder (%s) -- this will likely just time out" % ip)
    resource = S.resource_for(ip)
    points = int(body.get("points", 0) or 0)
    timeout_ms = int(body.get("timeout_ms", C.SCOPE_TIMEOUT_MS) or C.SCOPE_TIMEOUT_MS)
    log("connecting to %s (timeout %d ms) ..." % (resource, timeout_ms))
    sc = S.VisaScope(resource, channel=body.get("channel", C.SCOPE_CHANNEL), points=points,
                      dialect=body.get("dialect", C.SCOPE_DIALECT), timeout_ms=timeout_ms)
    sc.configure()
    with _lock:
        if _state["sc"] is not None:
            _state["sc"].close()
        _state["sc"] = sc
    info = sc.info()
    rate = sc.sample_rate()
    info["sample_rate"] = "%.4g Sa/s" % rate if rate else "unknown"
    log("scope connected: %s" % info)
    return {"info": info}


def api_scope_disconnect(_body: dict) -> dict:
    with _lock:
        if _state["sc"] is not None:
            _state["sc"].close()
            _state["sc"] = None
    log("scope disconnected")
    return {}


def api_scope_save_config(body: dict) -> dict:
    save_config_values(SCOPE_IP=body["ip"].strip(), SCOPE_CHANNEL=body["channel"].strip(),
                        SCOPE_DIALECT=body["dialect"].strip())
    log("saved SCOPE_IP / SCOPE_CHANNEL / SCOPE_DIALECT to config.py")
    return {}


# --- CPA Model tab ------------------------------------------------------------
def api_model_list(_body: dict) -> dict:
    with _lock:
        loaded = _state["model"].__name__ if _state["model"] is not None else None
    return {"modules": M.discover(), "loaded": loaded, "default_module": C.CPA_MODEL_MODULE,
            "default_target": C.CPA_TARGET, "default_rprev": C.CPA_RPREV}


def api_model_load(body: dict) -> dict:
    mod = M.load(body["module"])
    with _lock:
        _state["model"] = mod
    info = M.describe(mod)
    log("loaded CPA model: %s (Q=%d, K=%d, %d targets, %d R_prev modes)"
        % (info["module"], info["q"], info["k"], len(info["targets"]), len(info["rprevs"])))
    return info


def _require_model():
    with _lock:
        mod = _state["model"]
    if mod is None:
        raise ValueError("no CPA model loaded -- load one first")
    return mod


def api_model_selftest(_body: dict) -> dict:
    mod = _require_model()
    result = M.run_selftest(mod)
    log("self-test (%s): %s -- %s" % (mod.__name__, "PASS" if result["ok"] else
        ("N/A" if result["ok"] is None else "FAIL"), result["detail"]))
    return result


def api_model_save_default(body: dict) -> dict:
    mod = _require_model()
    target, rprev = body["target"], body["rprev"]
    if target not in mod.TARGETS:
        raise ValueError("%s has no target %r (have: %s)" % (mod.__name__, target, ", ".join(mod.TARGETS)))
    if rprev not in mod.RPREVS:
        raise ValueError("%s has no rprev %r (have: %s)" % (mod.__name__, rprev, ", ".join(mod.RPREVS)))
    save_config_values(CPA_MODEL_MODULE=mod.__name__, CPA_TARGET=target, CPA_RPREV=rprev)
    log("saved CPA_MODEL_MODULE=%s CPA_TARGET=%s CPA_RPREV=%s to config.py (cpa_attack.py's new defaults)"
        % (mod.__name__, target, rprev))
    return {}


def api_model_presets(_body: dict) -> dict:
    """mlkem_ref.MODEL_PRESETS directly -- what cpa_attack.py's --model actually uses.

    Independent of the CPA Model tab's "loaded module" state: cpa_attack.py
    is hard-wired to mlkem_ref (it does not yet support swapping in whatever
    module model_registry discovered/loaded -- that mechanism is discovery
    and self-test only so far). The Analysis tab reads presets from here.
    """
    return {
        "presets": [dict(name=n, bits=R.TARGETS[p["target"]], **p) for n, p in R.MODEL_PRESETS.items()],
        "targets": [{"name": n, "bits": b} for n, b in R.TARGETS.items()],
        "rprevs": [{"name": n, "desc": d} for n, d in R.RPREVS.items()],
    }


# --- Trace Acquisition tab -----------------------------------------------------
# Split per the GUI's own two steps: "Generate Dataset" (api_dataset_generate_*,
# below) builds and previews the ciphertext/class RECIPE entirely offline (no
# FPGA/scope -- just build/pqc-testkit); "Acquire Real Traces" (api_acquire_*)
# runs that recipe for real against *connected hardware only*, generating the
# actual ciphertext sequence exactly once at that point and committing it
# into the HDF5 file immediately (see hdf5_trace_store.TraceWriter's
# docstring) -- it is never regenerated again afterwards, including across a
# stop/resume, because only `dk` (not the "valid"/"invalid" ciphertexts
# themselves) is reliably reproducible from the same seed. The official GUI
# no longer offers a synthetic/dry-run source at all -- SimTarget/
# SyntheticScope still exist and still work (acquire_cpa.py's cpa-dry-run/
# tvla-dry-run CLI commands, unchanged) as the developer-test entry point
# (dev_regression_test.py), they are simply not reachable from this web GUI
# any more, so a file this GUI produces is always genuinely "Real Hardware".
def api_acquire_info(_body: dict) -> dict:
    """Static info for the tab's form: modes, strategies, and whether hardware is ready."""
    with _lock:
        hw_ready = _state["target"] is not None and _state["sc"] is not None
        busy = _state["busy"]
    return {
        "hardware_ready": hw_ready,  # both FPGA + scope connected (FPGA Connection / Oscilloscope Setup tabs)
        "busy": busy,
        "strategies": {
            "valid": "fresh, FIPS203-valid encapsulations (default)",
            "invalid": "random bytes as ciphertext (implicit-rejection path)",
            "structured": "isolates one BaseMul pair (zero u_hat elsewhere) -- needs poly (0-2) and pair (0-127)",
        },
    }


# --- Step 1: Generate Dataset (offline, no FPGA/scope needed) -----------------
def _run_generate_job(job_id: str, mode: str, strategy: str, poly: int, pair: int, n: int,
                       seed: bytes, invalid_random: bool) -> None:
    try:
        _update_job(job_id, message="generating (pqc-testkit sca gen)...")
        # This generation run is a one-off dry-fire to prove the recipe
        # actually produces something (and to preview it) -- its own dk/
        # items are discarded below, never persisted. Only "structured" is
        # actually reproduced byte-for-byte when "Acquire Real Traces" later
        # runs the same recipe for real (its ciphertexts come from a
        # seeded numpy RNG); "valid"/"invalid" go through `pqc-testkit sca
        # gen`, whose random-class content and shuffle order use real OS
        # randomness, so THIS preview will differ from what actually gets
        # captured -- preview_is_exact tells the GUI which case it is.
        if mode == "cpa":
            dk, items, _fixed = A.cpa_ciphertexts(seed, n, strategy, poly, pair)
            n_generated = len(items)
            preview = [ct[:24].hex() + "..." for ct in items[:3]]
            preview_is_exact = (strategy == "structured")
        else:
            dk, rows = A.tvla_vectors(seed, n, invalid_random=invalid_random)
            n_generated = len(rows)
            preview = [("fixed" if cls == 0 else "random") + ":" + ct[:24].hex() + "..."
                       for cls, ct in rows[:3]]
            preview_is_exact = False
        import hashlib
        recipe = {"mode": mode, "strategy": strategy, "poly": poly, "pair": pair,
                  "n_traces": n, "seed": seed.hex(), "invalid_random": invalid_random,
                  "dk_hash": hashlib.sha3_256(dk).digest().hex()[:16] + "...",
                  "created_unix": time.time()}
        INPUTSETS_DIR.mkdir(parents=True, exist_ok=True)
        suffix = "_%s.json" % mode
        out_path = INPUTSETS_DIR / (time.strftime("%Y%m%d_%H%M%S") + suffix)
        with open(out_path, "w") as f:
            json.dump(recipe, f, indent=2)
        result = dict(recipe, path=str(out_path), n_generated=n_generated, preview=preview,
                      preview_is_exact=preview_is_exact)
        _update_job(job_id, status="done", result=result, message="done")
        log("dataset generation job %s finished: %s (%d items)" % (job_id, out_path.name, n_generated))
    except (Exception, SystemExit) as e:  # noqa: BLE001 - see _run_acquire_job's comment: acquire_cpa.py
        # (via A.cpa_ciphertexts/A.tvla_vectors -> _sca_gen -> ensure_cli()) raises SystemExit for
        # user-facing errors (missing build/pqc-testkit), which does not subclass Exception.
        _update_job(job_id, status="error", error="%s: %s" % (type(e).__name__, e))
        log("[error] dataset generation job %s: %s" % (job_id, e))


def api_dataset_generate_start(body: dict) -> dict:
    mode = body.get("mode", "cpa")
    if mode not in ("cpa", "tvla"):
        raise ValueError("mode must be 'cpa' or 'tvla'")
    n = int(body.get("n_traces", 300))
    if n < 1:
        raise ValueError("n_traces must be >= 1")
    seed_hex = str(body.get("seed", "")).strip()
    seed = bytes.fromhex(seed_hex) if seed_hex else os.urandom(64)
    strategy = body.get("strategy", "valid")
    if mode == "cpa" and strategy not in ("valid", "invalid", "structured"):
        raise ValueError("strategy must be valid, invalid or structured")
    poly = int(body.get("poly", 0) or 0)
    if not 0 <= poly < R.K:
        raise ValueError("poly must be 0..%d" % (R.K - 1))
    pair = int(body.get("pair", 0) or 0)
    if not 0 <= pair < 128:
        raise ValueError("pair must be 0..127")
    invalid_random = bool(body.get("invalid_random", False))

    job_id = _new_job("generate")
    th = threading.Thread(target=_run_generate_job,
                          args=(job_id, mode, strategy, poly, pair, n, seed, invalid_random), daemon=True)
    th.start()
    log("started dataset generation job %s: mode=%s strategy=%s n=%d seed=%s"
        % (job_id, mode, strategy, n, seed.hex()[:16]))
    return {"job_id": job_id, "seed": seed.hex()}


def api_dataset_generate_status(body: dict) -> dict:
    return _get_job(body["job_id"])


def api_dataset_list_input_sets(_body: dict) -> dict:
    """Persisted recipes from "Generate Dataset", newest first -- what "Acquire
    Real Traces" picks from. Never contains dk or ciphertexts, only the
    parameters a dataset to this spec is built from (see INPUTSETS_DIR's
    module comment on why that is NOT the same as "the exact ciphertexts")."""
    INPUTSETS_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for p in sorted(INPUTSETS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            recipe = json.loads(p.read_text())
        except (OSError, ValueError):  # noqa: BLE001 - skip unreadable/partial files
            continue
        out.append(dict(recipe, path=str(p), filename=p.name))
    return {"input_sets": out}


def _load_input_set(path: str) -> dict:
    p = Path(path)
    if INPUTSETS_DIR.resolve() not in p.resolve().parents:
        raise ValueError("refusing to read outside the input-set directory")
    if not p.is_file():
        raise ValueError("no such input set: %s" % path)
    return json.loads(p.read_text())


# --- Step 2: Acquire Real Traces (hardware only) -------------------------------
def _run_acquire_job(job_id: str, recipe: dict, samples: int, resend_dk_every: int,
                      resume_path: str | None) -> None:
    try:
        target = _require_target()
        sc = _require_scope()

        with _lock:
            if _state["busy"]:
                raise ValueError("another acquisition (job %s) is already running" % _state["busy"])
            _state["busy"] = job_id

        mode, seed = recipe["mode"], bytes.fromhex(recipe["seed"])
        n = int(recipe["n_traces"])
        if mode == "cpa":
            dk, items, _fixed = A.cpa_ciphertexts(seed, n, recipe["strategy"], recipe["poly"], recipe["pair"])
            strategy_meta = recipe["strategy"]
        else:
            dk, items = A.tvla_vectors(seed, n, invalid_random=recipe.get("invalid_random", False))
            strategy_meta = "invalid-random" if recipe.get("invalid_random") else "valid"

        n_samples = getattr(sc, "samples", 0) or samples or 10000
        if resume_path:
            out_path = Path(resume_path)
        else:
            TRACES_DIR.mkdir(parents=True, exist_ok=True)
            out_path = TRACES_DIR / (time.strftime("%Y%m%d_%H%M%S") + "_%s.hdf5" % mode)

        def cb(done: int, total: int, msg: str) -> None:
            _update_job(job_id, progress=done, total=total, message=msg)

        def should_stop() -> bool:
            with _jobs_lock:
                return _jobs.get(job_id, {}).get("stop_requested", False)

        _update_job(job_id, total=n, message="acquiring...")
        summary = A.run_campaign(target, sc, dk, items, str(out_path), n_samples, mode=mode,
                                  strategy=strategy_meta, resend_dk_every=resend_dk_every,
                                  quiet=True, progress_cb=cb, recipe=recipe,
                                  resume=bool(resume_path), stop_cb=should_stop)
        msg = "stopped (partial)" if summary.get("stopped_early") else "done"
        _update_job(job_id, status="done", result=summary, message=msg)
        log("acquisition job %s %s: %s (%d/%d traces)"
            % (job_id, msg, out_path.name, summary["n_completed"], summary["n_traces_planned"]))
    except (Exception, SystemExit) as e:  # noqa: BLE001 - acquire_cpa.py raises SystemExit for
        # user-facing CLI errors (missing build/pqc-testkit, too many core errors, ...); it does
        # NOT subclass Exception, so it must be caught explicitly here or the background thread
        # dies silently and the job stays "running" forever. Report it on the job instead.
        _update_job(job_id, status="error", error="%s: %s" % (type(e).__name__, e))
        log("[error] acquisition job %s: %s" % (job_id, e))
    finally:
        with _lock:
            _state["busy"] = None


def api_acquire_start(body: dict) -> dict:
    with _lock:
        if _state["busy"]:
            raise ValueError("another acquisition (job %s) is already running" % _state["busy"])
        if _state["target"] is None or _state["sc"] is None:
            raise ValueError("Acquire Real Traces needs both the board and the scope connected "
                             "first (FPGA Connection / Oscilloscope Setup tabs)")

    resume_path = body.get("resume_file", "").strip() or None
    if resume_path:
        # Resuming an existing file: re-derive the recipe from what THAT file
        # was actually generated from (never from whatever the form happens
        # to show right now) -- see hdf5_trace_store.summarize()'s
        # "resumable" flag and acquire_cpa.run_campaign(resume=True).
        if not Path(resume_path).is_file():
            raise ValueError("no such file to resume: %s" % resume_path)
        s = HS.summarize(resume_path)
        if not s["resumable"]:
            raise ValueError("%s is not resumable (already complete, or predates recipe "
                             "metadata -- start a fresh campaign instead)" % resume_path)
        if s["simulated"]:
            raise ValueError("%s is a synthetic (dev-test) file -- refusing to continue it with "
                             "real hardware, which would mix fabricated and real rows in one "
                             "campaign; start a fresh real campaign instead" % resume_path)
        recipe = json.loads(s["metadata"]["recipe"])
    else:
        input_set_path = body.get("input_set", "").strip()
        if not input_set_path:
            raise ValueError("input_set is required (generate one in Step 1 first)")
        recipe = _load_input_set(input_set_path)

    samples = int(body.get("samples", 0) or 0)
    resend_dk_every = int(body.get("resend_dk_every", 50) or 50)
    if resend_dk_every < 1:
        raise ValueError("resend_dk_every must be >= 1")

    job_id = _new_job("acquire")
    th = threading.Thread(target=_run_acquire_job,
                          args=(job_id, recipe, samples, resend_dk_every, resume_path), daemon=True)
    th.start()
    log("started acquisition job %s: mode=%s n=%d%s" % (job_id, recipe["mode"], recipe["n_traces"],
        (" (resuming %s)" % Path(resume_path).name) if resume_path else ""))
    return {"job_id": job_id}


def api_acquire_stop(body: dict) -> dict:
    job_id = body["job_id"]
    with _jobs_lock:
        if job_id not in _jobs:
            raise ValueError("no such job: %s" % job_id)
        _jobs[job_id]["stop_requested"] = True
    log("stop requested for acquisition job %s" % job_id)
    return {}


def api_acquire_status(body: dict) -> dict:
    return _get_job(body["job_id"])


def api_acquire_capture_one(_body: dict) -> dict:
    """One-shot capture for "Waveform Preview": a fresh, throwaway dk/ct pair
    (never persisted, never part of any campaign) so the bench setup
    (trigger, vertical scale, timebase) can be sanity-checked on a real
    waveform before committing to a full campaign. Hardware only, like the
    rest of this tab -- there is no synthetic path in the official GUI.
    """
    target = _require_target()
    sc = _require_scope()
    with _lock:
        if _state["busy"]:
            raise ValueError("board/scope busy running acquisition job %s" % _state["busy"])
    dk, rows = A.tvla_vectors(os.urandom(64), 4)  # smallest valid ask; only the random-class ct is used
    ct = next(ct for cls, ct in rows if cls == 1)
    sc.arm()
    out = target.decaps(dk, ct, resend_dk=True)
    if not out.ok:
        raise ValueError("capture failed: %s" % out.describe())
    trace = sc.read(timeout=10.0)
    rate = sc.sample_rate()
    log("capture-one-trace: %s (%d samples, %s Sa/s)"
        % (out.describe(), trace.shape[0], ("%.4g" % rate) if rate else "unknown"))
    return {"samples": [round(float(v), 5) for v in trace], "sample_rate_hz": rate,
            "cycles": out.cycles, "describe": out.describe()}


def api_datasets(_body: dict) -> dict:
    """HDF5 campaign files found under sim_result/traces/, newest first."""
    TRACES_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for p in sorted(TRACES_DIR.glob("*.hdf5"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            s = HS.summarize(p)
        except Exception:  # noqa: BLE001 - skip unreadable/partial files, don't fail the whole list
            continue
        out.append({
            "filename": p.name, "path": str(p), "mode": s.get("mode", "cpa"),
            "simulated": s.get("simulated", False), "n_completed": s["n_completed"],
            "n_traces_planned": s.get("n_traces_planned", s["n_completed"]),
            "resumable": s.get("resumable", False),
            "created_unix": s["metadata"].get("created_unix"),
            "strategy": s["metadata"].get("strategy", ""),
            "n_fixed": s.get("n_fixed"), "n_random": s.get("n_random"),
            "ok_fraction": s.get("ok_fraction"),
        })
    return {"datasets": out}


# --- CPA / TVLA Analysis tab ---------------------------------------------------
def _csv_paths(h5_path: str, job_id: str) -> tuple[Path, Path]:
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    stem = Path(h5_path).stem
    return (ANALYSIS_DIR / ("%s_%s_results.csv" % (stem, job_id)),
            ANALYSIS_DIR / ("%s_%s_curve.csv" % (stem, job_id)))


def _model_version() -> str:
    """A content hash of mlkem_ref.py, so a persisted report records exactly
    which version of the leakage model produced it (requirement: persist
    "analysis model, parameters, model version" per run) -- more trustworthy
    than a hand-maintained version string that could go stale.
    """
    import hashlib
    return "mlkem_ref:" + hashlib.sha256(Path(R.__file__).read_bytes()).hexdigest()[:12]


def _save_analysis_report(kind: str, file: str, job_id: str, summary: dict) -> str:
    """Persist a CPA/TVLA analysis's full result (dataset identity, model
    config, every computed metric and array) as its own JSON report under
    ANALYSIS_DIR -- independent of the job dict in memory (which is only
    kept for a running server's lifetime) and independent of the two
    narrower CSVs cpa_attack.py already writes. This is what lets the
    Results Viewer re-open a past analysis, and what the cross-campaign
    success-rate aggregation (api_analysis_success_rate) scans.
    """
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        dataset = HS.summarize(file)
    except Exception:  # noqa: BLE001 - the file existed when the job started; report what we can
        dataset = {"path": file}
    report = {
        "kind": kind, "job_id": job_id, "dataset": dataset,
        "model_version": _model_version(),
        "analysis_timestamp_unix": time.time(),
        **summary,
    }
    out_path = ANALYSIS_DIR / ("%s_%s_%s_report.json" % (Path(file).stem, job_id, kind))
    report["report_path"] = str(out_path)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2, default=_json_default)
    return str(out_path)


def api_results_list(body: dict) -> dict:
    """Persisted analysis reports, newest first; `file` (optional) filters to
    reports for one dataset -- used both by the Results Viewer's own list
    and by "selecting a dataset auto-loads its most recent result"."""
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    file_filter = body.get("file", "").strip()
    out = []
    for p in sorted(ANALYSIS_DIR.glob("*_report.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            rep = json.loads(p.read_text())
        except (OSError, ValueError):  # noqa: BLE001 - skip unreadable/partial files
            continue
        dataset_path = rep.get("dataset", {}).get("path", "")
        if file_filter and dataset_path != file_filter:
            continue
        out.append({
            "path": str(p), "filename": p.name, "kind": rep.get("kind"),
            "dataset_path": dataset_path, "dataset_filename": Path(dataset_path).name if dataset_path else "",
            "analysis_timestamp_unix": rep.get("analysis_timestamp_unix"),
            "model_config": rep.get("model_config"),
        })
    return {"results": out}


def api_results_load(body: dict) -> dict:
    path = Path(body["path"])
    if ANALYSIS_DIR.resolve() not in path.resolve().parents:
        raise ValueError("refusing to read outside the analysis output directory")
    if not path.is_file():
        raise ValueError("no such report: %s" % path)
    return json.loads(path.read_text())


def _run_cpa_analysis_job(job_id: str, file: str, model: str, target: str, rprev: str,
                           max_traces: int, dk_ref_path: str, curve: bool) -> None:
    try:
        dk_ref = open(dk_ref_path, "rb").read() if dk_ref_path else None
        _update_job(job_id, message="running CPA analysis (%s/%s)..." % (target, rprev))
        summary = CA.run_attack(file, target, rprev, max_traces, None, dk_ref)
        # Full model config this run actually used, independent of whatever the
        # Leakage Model section shows *now* -- a later model switch in the UI
        # must not retroactively change what an already-finished run reports.
        summary["model_config"] = {"preset": model or "custom", "target": target, "rprev": rprev,
                                   "max_traces_requested": max_traces, "oracle_assisted": dk_ref is not None}
        results_csv, curve_csv = _csv_paths(file, job_id)
        CA.write_results_csv(summary["results"], str(results_csv))
        summary["results_csv"] = str(results_csv)
        if curve:
            if dk_ref is None:
                raise ValueError("curve needs a dk-ref path (oracle-assisted evaluation)")
            _update_job(job_id, message="computing key-rank-vs-trace-count curve...")
            c = CA.run_curve(file, target, rprev, max_traces, dk_ref)
            CA.write_curve_csv(c, str(curve_csv))
            c["curve_csv"] = str(curve_csv)
            summary["curve"] = c
        summary["report_path"] = _save_analysis_report("cpa", file, job_id, summary)
        _update_job(job_id, status="done", result=summary, message="done")
        log("CPA analysis job %s finished (%s)" % (job_id, Path(file).name))
    except (Exception, SystemExit) as e:  # noqa: BLE001 - cpa_attack.py raises SystemExit for
        # CLI-style errors (too few traces, wrong --dk-ref); see _run_acquire_job's comment.
        _update_job(job_id, status="error", error="%s: %s" % (type(e).__name__, e))
        log("[error] CPA analysis job %s: %s" % (job_id, e))


def api_analysis_cpa_start(body: dict) -> dict:
    file = body["file"]
    if not Path(file).is_file():
        raise ValueError("no such file: %s" % file)
    model = body.get("model", "")
    if model:
        if model not in R.MODEL_PRESETS:
            raise ValueError("no such model preset: %s" % model)
        p = R.MODEL_PRESETS[model]
        target, rprev = p["target"], p["rprev"]
    else:
        target = body.get("target", C.CPA_TARGET)
        rprev = body.get("rprev", C.CPA_RPREV)
        if target not in R.TARGETS:
            raise ValueError("no such target: %s" % target)
        if rprev not in R.RPREVS:
            raise ValueError("no such rprev: %s" % rprev)
    max_traces = int(body.get("max_traces", 0) or 0)
    dk_ref_path = body.get("dk_ref_path", "").strip()
    if dk_ref_path and not Path(dk_ref_path).is_file():
        raise ValueError("no such dk-ref file: %s" % dk_ref_path)
    curve = bool(body.get("curve", False))

    job_id = _new_job("cpa_analysis")
    th = threading.Thread(target=_run_cpa_analysis_job,
                          args=(job_id, file, model, target, rprev, max_traces, dk_ref_path, curve),
                          daemon=True)
    th.start()
    log("started CPA analysis job %s: file=%s target=%s rprev=%s oracle_assisted=%s"
        % (job_id, Path(file).name, target, rprev, bool(dk_ref_path)))
    return {"job_id": job_id}


def _run_tvla_analysis_job(job_id: str, file: str, max_traces: int) -> None:
    try:
        _update_job(job_id, message="running TVLA (Welch's t-test)...")
        # chart_data() wraps run_tvla() (same max|t|/leakage_detected/etc.
        # summary) and adds the decimated t-curve, fixed/random mean traces,
        # and the detection-vs-trace-count sweep the Results Viewer needs --
        # one call, one consistent set of numbers for both the live status
        # response and the persisted report below.
        result = TV.chart_data(file, max_traces)
        result["report_path"] = _save_analysis_report("tvla", file, job_id, result)
        _update_job(job_id, status="done", result=result, message="done")
        log("TVLA analysis job %s finished (%s)" % (job_id, Path(file).name))
    except (Exception, SystemExit) as e:  # noqa: BLE001 - tvla_analysis.py raises SystemExit for
        # CLI-style errors (wrong mode, too few traces); see _run_acquire_job's comment.
        _update_job(job_id, status="error", error="%s: %s" % (type(e).__name__, e))
        log("[error] TVLA analysis job %s: %s" % (job_id, e))


def api_analysis_tvla_start(body: dict) -> dict:
    file = body["file"]
    if not Path(file).is_file():
        raise ValueError("no such file: %s" % file)
    max_traces = int(body.get("max_traces", 0) or 0)
    job_id = _new_job("tvla_analysis")
    th = threading.Thread(target=_run_tvla_analysis_job, args=(job_id, file, max_traces), daemon=True)
    th.start()
    log("started TVLA analysis job %s: file=%s" % (job_id, Path(file).name))
    return {"job_id": job_id}


def api_analysis_status(body: dict) -> dict:
    return _get_job(body["job_id"])


def api_analysis_correlation_curve(body: dict) -> dict:
    """One coefficient's full correlation-vs-sample curve, for the "Correlation
    vs. Time" chart. Synchronous (not a background job): a single coefficient
    is fast (~1/768th of a full run_attack() call), no progress bar needed.
    """
    file = body["file"]
    if not Path(file).is_file():
        raise ValueError("no such file: %s" % file)
    target = body.get("target", C.CPA_TARGET)
    rprev = body.get("rprev", C.CPA_RPREV)
    poly = int(body.get("poly", 0) or 0)
    coeff = int(body.get("coeff", 0) or 0)
    max_traces = int(body.get("max_traces", 0) or 0)
    dk_ref_path = body.get("dk_ref_path", "").strip()
    if dk_ref_path and not Path(dk_ref_path).is_file():
        raise ValueError("no such dk-ref file: %s" % dk_ref_path)
    dk_ref = open(dk_ref_path, "rb").read() if dk_ref_path else None
    try:
        return CA.correlation_curve_for_coefficient(file, target, rprev, poly, coeff, max_traces, dk_ref)
    except SystemExit as e:  # cpa_attack.py's CLI-style errors -- see _run_acquire_job's comment
        raise ValueError(str(e))


def api_analysis_download(body: dict) -> dict:
    """Returns a CSV's content inline (small enough -- at most 768 or so rows)."""
    path = Path(body["path"])
    if ANALYSIS_DIR.resolve() not in path.resolve().parents:
        raise ValueError("refusing to read outside the analysis output directory")
    if not path.is_file():
        raise ValueError("no such file: %s" % path)
    return {"filename": path.name, "csv": path.read_text()}


# --- Raw Trace Viewer ----------------------------------------------------------
def api_analysis_raw_traces(body: dict) -> dict:
    """Real waveforms straight out of the HDF5 file, for the Raw Trace
    Viewer's overlay chart -- never synthetic/fabricated data, and never run
    through any leakage model. Capped (MAX_TRACES traces, MAX_SAMPLES
    samples each, via plain strided decimation above that) to keep one
    response small enough for a browser tab with no virtualization.
    """
    MAX_TRACES, MAX_SAMPLES_DEFAULT, MAX_SAMPLES_CAP = 12, 4000, 20000
    file = body["file"]
    if not Path(file).is_file():
        raise ValueError("no such file: %s" % file)
    indices = body.get("indices")
    first_n = int(body.get("first_n", 0) or 0)
    max_samples = min(int(body.get("max_samples", MAX_SAMPLES_DEFAULT) or MAX_SAMPLES_DEFAULT), MAX_SAMPLES_CAP)

    with HS.TraceReader(file) as r:
        n_avail = r.n_completed
        if indices:
            idx_list = [int(i) for i in indices]
        else:
            idx_list = list(range(min(first_n or 4, n_avail)))
        idx_list = [i for i in idx_list if 0 <= i < n_avail][:MAX_TRACES]
        if not idx_list:
            raise ValueError("no valid trace indices (file has %d completed traces)" % n_avail)

        n_samples = r.f["traces"].shape[1]
        stride = max(1, n_samples // max_samples)
        sample_idx = np.arange(0, n_samples, stride)
        decimated = stride > 1
        time_axis = r.time_axis[sample_idx]
        sample_rate = r.sample_rate
        windows = A.window_sample_ranges(sample_rate, n_samples) if sample_rate else \
            A.window_sample_ranges(C.CORE_MHZ * 1e6, n_samples)

        traces_out = []
        all_traces = r.f["traces"]
        all_ct = r.ciphertexts
        all_cycles = r.cycles
        all_ok = r.ok
        for i in idx_list:
            samples = all_traces[i][sample_idx]
            traces_out.append({
                "index": i, "ok": bool(all_ok[i]), "cycles": int(all_cycles[i]),
                "ct_preview": bytes(all_ct[i][:16]).hex() + "...",
                "samples": [round(float(v), 5) for v in samples],
            })
    return {
        "file": file, "n_samples": int(n_samples), "sample_rate_hz": sample_rate,
        "decimated": decimated, "stride": int(stride),
        "time_axis": [round(float(v), 9) for v in time_axis],
        "sample_indices": sample_idx.tolist(),
        "bmul_windows_samples": windows, "traces": traces_out,
    }


# --- cross-campaign CPA success-rate aggregation -------------------------------
def api_analysis_success_rate(body: dict) -> dict:
    """Success-rate-vs-trace-count aggregated across >=2 INDEPENDENT campaigns
    (different dataset files -- different seeds/ciphertexts) that were each
    analysed, oracle-assisted, with the SAME (target, rprev) model and
    produced a --curve. This is never shown for a single campaign: one run's
    curve is already in "Key rank vs. trace count" above, and presenting it
    again labelled "success rate" invites reading noise from one seed as a
    general property -- see README.md section 7, point 5 ("change the seed
    and the result changes -- that's expected, not a fault").

    Returns {"available": False, "reason": ...} rather than any curve when
    fewer than 2 qualifying reports exist -- the Results Viewer must render
    that as "Not Available", never an empty/fabricated chart.
    """
    target = body.get("target", C.CPA_TARGET)
    rprev = body.get("rprev", C.CPA_RPREV)
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    curves, dataset_paths = [], []
    for p in sorted(ANALYSIS_DIR.glob("*_cpa_report.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            rep = json.loads(p.read_text())
        except (OSError, ValueError):  # noqa: BLE001
            continue
        mc = rep.get("model_config") or {}
        curve = rep.get("curve")
        if not curve or not curve.get("checkpoints") or mc.get("target") != target or mc.get("rprev") != rprev:
            continue
        ds_path = rep.get("dataset", {}).get("path", "")
        if not ds_path or ds_path in dataset_paths:  # one curve per dataset file (the newest wins via mtime order)
            continue
        dataset_paths.append(ds_path)
        curves.append(curve["checkpoints"])
    if len(curves) < 2:
        return {"available": False, "target": target, "rprev": rprev, "n_campaigns": len(curves),
                "reason": "need >=2 independent campaigns (different dataset files) analysed with "
                         "--curve/oracle-assisted at this exact model config; have %d" % len(curves)}

    # Union of every checkpoint n_traces seen anywhere, each curve linearly
    # interpolated (numpy.interp, clipped to that curve's own n_traces range
    # -- no extrapolation) onto that shared grid, then averaged. This is
    # real aggregation of real persisted numbers, not a fitted/guessed curve.
    grid = sorted({row["n_traces"] for ckpts in curves for row in ckpts})
    per_campaign = []
    for ckpts in curves:
        xs = [row["n_traces"] for row in ckpts]
        ys = [row["rank1_fraction"] for row in ckpts]
        per_campaign.append(np.interp(grid, xs, ys, left=np.nan, right=np.nan))
    stacked = np.array(per_campaign)  # (n_campaigns, len(grid))
    mean = np.nanmean(stacked, axis=0)
    lo = np.nanmin(stacked, axis=0)
    hi = np.nanmax(stacked, axis=0)
    rows = [{"n_traces": n, "mean_rank1_fraction": float(mean[i]) if np.isfinite(mean[i]) else None,
             "min_rank1_fraction": float(lo[i]) if np.isfinite(lo[i]) else None,
             "max_rank1_fraction": float(hi[i]) if np.isfinite(hi[i]) else None}
            for i, n in enumerate(grid)]
    return {"available": True, "target": target, "rprev": rprev, "n_campaigns": len(curves),
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
    ("GET", "/api/scope/resources"): api_scope_resources,
    ("POST", "/api/scope/connect"): api_scope_connect,
    ("POST", "/api/scope/disconnect"): api_scope_disconnect,
    ("POST", "/api/scope/save_config"): api_scope_save_config,
    ("GET", "/api/model/list"): api_model_list,
    ("POST", "/api/model/load"): api_model_load,
    ("POST", "/api/model/selftest"): api_model_selftest,
    ("POST", "/api/model/save_default"): api_model_save_default,
    ("GET", "/api/model/presets"): api_model_presets,
    ("GET", "/api/acquire/info"): api_acquire_info,
    ("POST", "/api/dataset/generate/start"): api_dataset_generate_start,
    ("GET", "/api/dataset/generate/status"): api_dataset_generate_status,
    ("GET", "/api/dataset/input_sets"): api_dataset_list_input_sets,
    ("POST", "/api/acquire/start"): api_acquire_start,
    ("POST", "/api/acquire/stop"): api_acquire_stop,
    ("GET", "/api/acquire/status"): api_acquire_status,
    ("POST", "/api/acquire/capture_one"): api_acquire_capture_one,
    ("GET", "/api/datasets"): api_datasets,
    ("POST", "/api/analysis/cpa/start"): api_analysis_cpa_start,
    ("POST", "/api/analysis/tvla/start"): api_analysis_tvla_start,
    ("GET", "/api/analysis/status"): api_analysis_status,
    ("POST", "/api/analysis/download"): api_analysis_download,
    ("POST", "/api/analysis/correlation_curve"): api_analysis_correlation_curve,
    ("POST", "/api/analysis/raw_traces"): api_analysis_raw_traces,
    ("POST", "/api/analysis/success_rate"): api_analysis_success_rate,
    ("POST", "/api/results/list"): api_results_list,
    ("POST", "/api/results/load"): api_results_load,
}


# --- HTTP plumbing -------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "CPAToolkit/1.0"

    def log_message(self, fmt, *args):  # noqa: A003 - silence per-request console spam
        pass

    def _send_json(self, status: int, obj) -> None:
        body = json.dumps(obj, default=_json_default).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        ctype = CONTENT_TYPES.get(path.suffix, "application/octet-stream")
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if (method, path) in ROUTES:
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                body = json.loads(raw) if raw else {}
                for k, v in parse_qs(parsed.query).items():  # GET's ?since=5 etc.
                    body.setdefault(k, v[0])
                result = ROUTES[(method, path)](body)
                self._send_json(200, result)
            except (ValueError, KeyError) as e:
                self._send_json(400, {"error": str(e)})
            except SystemExit as e:
                log("[error] %s" % e)
                self._send_json(502, {"error": str(e)})
            except Exception as e:  # noqa: BLE001 - surface it to the page, don't crash the server
                log("[error] %s: %s" % (type(e).__name__, e))
                self._send_json(502, {"error": "%s: %s" % (type(e).__name__, e)})
            return
        if method == "GET":
            self._serve_static(path)
            return
        self._send_json(404, {"error": "no such route"})

    def _serve_static(self, path: str) -> None:
        # web_static/ IS the static root -- index.html's /static/app.js etc.
        # map to web_static/app.js, not web_static/static/app.js.
        if path in ("/", ""):
            rel = "index.html"
        elif path.startswith("/static/"):
            rel = path[len("/static/") :]
        else:
            rel = path.lstrip("/")
        target = (STATIC_DIR / rel).resolve()
        if STATIC_DIR.resolve() not in target.parents and target != STATIC_DIR.resolve():
            self._send_json(403, {"error": "forbidden"})
            return
        if not target.is_file():
            self._send_json(404, {"error": "not found: %s" % path})
            return
        self._send_file(target)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's naming convention
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = "http://127.0.0.1:%d/" % args.port
    print("CPA Toolkit setup GUI: %s  (Ctrl-C to stop)" % url)
    if not args.no_browser:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        with _lock:
            if _state["target"] is not None:
                _state["target"].close()
            if _state["sc"] is not None:
                _state["sc"].close()
        server.shutdown()


if __name__ == "__main__":
    main()
