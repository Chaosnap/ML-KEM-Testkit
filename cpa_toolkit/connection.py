"""Non-blocking GUI connections: board UART / USB FIFO and scope VISA.

Every state change is instant from the caller's point of view:

- ``connect()`` starts a worker thread and returns at once (state
  ``connecting``); the page polls ``snapshot()``.
- ``disconnect()`` works in every state. Mid-connect it closes the half-open
  port (which aborts the worker's pending read) and the worker's late result
  is discarded: every attempt carries a generation number, and a worker whose
  generation is no longer current closes whatever it opened and stops.
- ``mark_lost()`` is how the link monitor (``LinkMonitor``) or a failing
  operation reports a link that died while connected: state ``lost``, with
  the reason, and the port is released so it can be reopened.

States: ``disconnected`` | ``connecting`` | ``connected`` | ``lost`` | ``error``.
"""

from __future__ import annotations

import threading
import time

import sakura_x
import uart_target as T


class _Cancelled(Exception):
    pass


def _close(handle) -> None:
    try:
        handle.close()
    except Exception:  # noqa: BLE001 - closing a dead link must never raise
        pass


def _close_in_background(handle) -> threading.Thread:
    """macOS takes ~0.5 s inside a serial port's close(); nobody should wait
    for it. Pending reads on the port still fail at once (the link marks
    itself closed before the OS call)."""
    th = threading.Thread(target=_close, args=(handle,), daemon=True, name="close")
    th.start()
    return th


class Managed:
    """One connection (board or scope) with an explicit state machine."""

    def __init__(self, kind: str, log):
        self.kind = kind
        self._log = log
        self._lock = threading.Lock()
        self._gen = 0
        self._pending = None          # handle opened by a worker that is still probing
        self.handle = None
        self.state = "disconnected"
        self.info: dict | None = None
        self.error = ""
        self.params: dict = {}
        self.checks: list[dict] = []
        self.since = time.time()
        self._closer: threading.Thread | None = None

    def snapshot(self) -> dict:
        with self._lock:
            return {"state": self.state, "info": self.info, "error": self.error,
                    "params": dict(self.params), "since": self.since,
                    "checks": [dict(c) for c in self.checks]}

    def connect(self, params: dict, opener) -> dict:
        """``opener(params, publish) -> (handle, info)``; it must call
        ``publish(handle)`` as soon as the port is open, so a disconnect
        during its probing can close it."""
        with self._lock:
            self._gen += 1
            gen = self._gen
            old = self.handle or self._pending
            self.handle = self._pending = None
            self.state, self.params, self.info, self.error = "connecting", dict(params), None, ""
            self.checks = []
            self.since = time.time()
        if old is not None:
            self._closer = _close_in_background(old)
        threading.Thread(target=self._worker, args=(gen, params, opener), daemon=True,
                         name="%s-connect" % self.kind).start()
        return self.snapshot()

    def _worker(self, gen: int, params: dict, opener) -> None:
        def publish(handle=None, check=None):
            with self._lock:
                current = gen == self._gen
                if current:
                    if handle is not None:
                        self._pending = handle
                    if check is not None:
                        self.checks.append(check)
            if not current:
                if handle is not None:
                    _close(handle)
                raise _Cancelled()

        closer = self._closer
        if closer is not None:
            closer.join(3.0)  # the OS must release the port before it can be reopened
        try:
            handle, info = opener(params, publish)
        except _Cancelled:
            return
        except Exception as e:  # noqa: BLE001 - any failure becomes the "error" state
            with self._lock:
                if gen != self._gen:
                    return
                pending, self._pending = self._pending, None
                self.state, self.error, self.since = "error", str(e), time.time()
            if pending is not None:
                _close(pending)
            self._log("[error] %s connect failed: %s" % (self.kind, e))
            return
        with self._lock:
            current = gen == self._gen
            if current:
                self.handle, self._pending = handle, None
                self.state, self.info, self.since = "connected", info, time.time()
        if not current:
            _close(handle)
            return
        self._log("%s connected: %s" % (self.kind, info))

    def disconnect(self, reason: str = "") -> dict:
        with self._lock:
            self._gen += 1
            handle = self.handle or self._pending
            self.handle = self._pending = None
            was = self.state
            self.state, self.info, self.error, self.since = "disconnected", None, "", time.time()
        if handle is not None:
            self._closer = _close_in_background(handle)
        if was != "disconnected":
            self._log("%s disconnected%s" % (self.kind, (" (%s)" % reason) if reason else ""))
        return self.snapshot()

    def mark_lost(self, reason: str, handle=None) -> None:
        """The link died while connected. ``handle``, if given, must still be
        the current one (a report about an already-replaced link is ignored)."""
        with self._lock:
            if self.state != "connected" or (handle is not None and handle is not self.handle):
                return
            self._gen += 1
            dead, self.handle = self.handle, None
            self.state, self.error, self.since = "lost", reason, time.time()
        self._closer = _close_in_background(dead)
        self._log("[error] %s link lost: %s" % (self.kind, reason))

    def update_info(self, info: dict) -> None:
        """Replace the info of the connected handle (after its settings changed)."""
        with self._lock:
            if self.state == "connected":
                self.info = info

    def require(self):
        with self._lock:
            if self.state != "connected":
                raise ValueError("%s is not connected (%s)" % (self.kind, self.state))
            return self.handle


# --- openers ------------------------------------------------------------------
NO_ANSWER_HINT = (
    "Make sure that the port and the baud rate are correct. Arty A7: use the ...1 interface of the "
    "on-board USB. Arty Z7 PS build: after each power-up, run `uv run scripts/board/boot_arty_z7_ps.py`. "
    "Arty Z7 PL build: use the external 3.3 V USB-UART on Pmod JA.")


def open_board(params: dict, publish):
    """Connect with staged shell diagnostics; never retry an incomplete FIFO frame."""
    if sakura_x.is_port(params["port"]):
        from sakura_x import ShellLink, ShellTarget

        def report(name, ok, detail):
            publish(check={"name": name, "ok": ok, "detail": detail})

        target = ShellTarget(ShellLink(params["port"], report=report))
        target.op_timeout = float(params.get("op_timeout", target.op_timeout))
        publish(target)
        stage = "Spartan controller"
        try:
            # Every connect: RTS reset, then find out WHICH Spartan-6 controller answers (shell /
            # AIST factory / none) before any Kintex access. It reports its own checks.
            target.link.check_controller(report)
            stage = "Kintex shell identity"
            target.probe_run()
            report(stage, True, "The Kintex-7 replies (ABI 1). The bus between the FPGAs is OK.")
            stage = "ML-KEM core"
            info = target.info()
            if info["alg_id"] != 1:
                raise ValueError("ALG_ID is %d, not 1 (ML-KEM). Load sakura_x_top.bit on CN5." % info["alg_id"])
            report(stage, True, "ML-KEM core %s. Ready for Decaps." % info["version"])
            return target, info
        except Exception as e:
            if stage != "Spartan controller":      # check_controller reported its own failure
                report(stage, False, str(e))
            raise
    target = T.Target.open(params["port"], int(params["baud"]))
    target.op_timeout = float(params.get("op_timeout", target.op_timeout))
    publish(target)
    last: Exception | None = None
    for _ in range(3):  # a just-booted forwarder / a half-received frame: retry briefly
        try:
            info = target.info()
            break
        except T.ProtocolError as e:
            last = e
            if e.fatal:
                raise
            target.link.resync()
    else:
        raise ValueError("no response from the core on %s (%s). %s" % (params["port"], last, NO_ANSWER_HINT))
    if info["alg_id"] != 1:
        raise ValueError("unexpected ALG_ID %d on %s -- not the ML-KEM core" % (info["alg_id"], params["port"]))
    return target, info


def open_scope(params: dict, publish):
    import scope as S
    try:
        sc = S.open_scope(params["ip"], channel=params["channel"], dialect=params["dialect"],
                          points=int(params.get("points", 0)), timeout_ms=int(params["timeout_ms"]),
                          transport=params.get("transport", ""))
    except SystemExit as e:  # scope.py's command-line errors: here they must become the "error" state
        raise RuntimeError(str(e)) from None
    publish(sc)
    sc.configure()
    info = sc.info()
    rate = sc.sample_rate()
    info["sample_rate"] = "%.4g Sa/s" % rate if rate else "unknown"
    return sc, info


# --- link monitor ---------------------------------------------------------------
class LinkMonitor:
    """Pings an idle connected board and watches its port; reports a dead link.

    Never competes with real traffic: it only pings when nobody holds the
    link lock (non-blocking acquire) and skips while ``busy()`` is True (an
    acquisition reports link trouble itself).
    """

    def __init__(self, board: Managed, busy, interval: float = 1.5):
        self.board, self.busy, self.interval = board, busy, interval
        self._misses = 0
        threading.Thread(target=self._run, daemon=True, name="link-monitor").start()

    def _run(self) -> None:
        while True:
            time.sleep(self.interval)
            try:
                self._tick()
            except Exception:  # noqa: BLE001 - the monitor must never die
                pass

    def _tick(self) -> None:
        snap = self.board.snapshot()
        if snap["state"] != "connected" or self.busy():
            self._misses = 0
            return
        target = self.board.handle
        if target is None:
            return
        if not T.port_exists(snap["params"]["port"]):
            self.board.mark_lost("board USB device disappeared (USB unplugged or board powered off)", target)
            return
        if not target.link.lock.acquire(blocking=False):
            return  # an operation is talking to the board right now: it is alive
        fatal = ""
        try:
            ok = target.ping()
        except T.ProtocolError as e:
            ok, fatal = False, str(e) if e.fatal else ""
        finally:
            target.link.lock.release()
        if fatal:
            self.board.mark_lost(fatal, target)
            return
        self._misses = 0 if ok else self._misses + 1
        if self._misses >= 2:
            self._misses = 0
            hint = ("Click Connect. Then load DK and CT again." if
                    sakura_x.is_port(snap["params"]["port"]) else
                    "Board reset or reprogrammed? Arty Z7 PS build: rerun scripts/board/boot_arty_z7_ps.py.")
            self.board.mark_lost("The core does not reply. " + hint, target)
