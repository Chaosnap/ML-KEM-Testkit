"""The acquisition loop.

One iteration is: load the input into the core's data buffer, arm the scope,
start the operation, read back the result and the captured samples, store
everything. Repeated a few thousand to a few hundred thousand times, that is
a trace set.

Two properties of this core make the loop simpler than it is for a software
target. The core is constant-time, so every trace has the same length and no
resampling or alignment is needed afterwards; and it reports ``CYCLE_COUNT``
for every run, so the campaign can verify that property continuously instead
of assuming it.

Integrity checking is not optional in a long campaign. Hours of traces are
worthless if the board reset halfway through, the link dropped a frame, or
the key changed. Every run is therefore checked three ways:

* the core must report DONE without an error code;
* ``CYCLE_COUNT`` must stay constant (any variation is reported, and is
  itself a timing-leakage finding worth following up);
* every trace of the *fixed* class must produce the same output as the first
  one. With a fixed decapsulation key and a fixed ciphertext the shared key
  K is fixed too, so this is a free known-answer test, roughly every other
  run, against silent corruption.

Interrupting with Ctrl-C flushes and closes the set cleanly: the campaign can
be analysed as far as it got, and ``meta.json`` records how many traces that
was.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime

import numpy as np

from . import protocol as P
from .scope import NullScope, Scope
from .storage import TraceSet
from .target import Target
from .vectors import VectorSet


class CampaignError(RuntimeError):
    pass


def _fmt_eta(seconds: float) -> str:
    seconds = int(max(seconds, 0))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return "%d:%02d:%02d" % (h, m, s) if h else "%d:%02d" % (m, s)


class Campaign:
    """Run a vector set on the board while the scope captures each operation.

    :param target: the board.
    :param scope: the instrument, or a :class:`~tracelab.scope.NullScope`.
    :param vectors: the shuffled fixed/random input set.
    :param out_dir: campaign directory to create.
    :param scope_timeout: how long to wait for one capture.
    :param max_errors: abort after this many consecutive failed runs.
    """

    def __init__(
        self,
        target: Target,
        scope: Scope,
        vectors: VectorSet,
        out_dir: str,
        scope_timeout: float = 10.0,
        max_errors: int = 5,
        progress_every: float = 2.0,
    ):
        self.target = target
        self.scope = scope
        self.vectors = vectors
        self.out_dir = out_dir
        self.scope_timeout = scope_timeout
        self.max_errors = max_errors
        self.progress_every = progress_every

        self.reference_output: bytes = b""
        self.reference_cycles: int = 0
        self.stats = {
            "completed": 0,
            "failed": 0,
            "retried": 0,
            "cycle_varied": 0,
            "output_mismatch": 0,
        }

    # --- one trace --------------------------------------------------------
    def _one(self, vec) -> tuple:
        """Capture a single trace; returns ``(samples, outcome)``."""
        # The input goes in first: at 115200 baud a 3488-byte Decaps input
        # takes about 0.3 s, and the scope must not be armed during that.
        self.target.link.write_data(P.IN_BASE, vec.data)
        self.target.link.wr("DATA_IN_ADDR", P.IN_BASE)
        self.target.link.wr("DATA_IN_LEN", len(vec.data))
        self.target.link.wr("DATA_OUT_ADDR", P.OUT_BASE)
        self.target.link.wr("OP_MODE", self.vectors.op)

        if isinstance(self.scope, NullScope):
            self.scope.set_input(vec.data)
        self.scope.arm()

        t0 = time.time()
        self.target.link.wr("CTRL", P.CTRL_START)
        deadline = time.time() + self.target.op_timeout
        while True:
            st = self.target.link.rd("STATUS")
            if st & (P.ST_DONE | P.ST_ERROR):
                break
            if time.time() > deadline:
                raise P.ProtocolError("operation did not finish (STATUS=0x%x)" % st)

        err = self.target.link.rd("ERROR_CODE")
        cycles = self.target.link.rd("CYCLE_COUNT")
        out = b""
        if not st & P.ST_ERROR:
            out = self.target.link.read_data(P.OUT_BASE, self.target.link.rd("DATA_OUT_LEN"))

        from .target import Outcome

        outcome = Outcome(st, err, cycles, out, time.time() - t0)
        samples = self.scope.read(self.scope_timeout)
        return samples, outcome

    # --- integrity --------------------------------------------------------
    def _check(self, vec, outcome, log) -> None:
        if outcome.cycles:
            if not self.reference_cycles:
                self.reference_cycles = outcome.cycles
            elif outcome.cycles != self.reference_cycles:
                self.stats["cycle_varied"] += 1
                if self.stats["cycle_varied"] <= 3:
                    log(
                        "  ! trace %d: CYCLE_COUNT %d, first run had %d "
                        "(the core should be constant-time)"
                        % (vec.index, outcome.cycles, self.reference_cycles)
                    )
        if vec.cls == 0 and outcome.output:
            if not self.reference_output:
                self.reference_output = outcome.output
            elif outcome.output != self.reference_output:
                self.stats["output_mismatch"] += 1
                raise CampaignError(
                    "trace %d: the fixed input produced a different output than the "
                    "first fixed trace. The board, the key or the link changed; the "
                    "traces captured so far cannot be trusted." % vec.index
                )

    # --- the loop ---------------------------------------------------------
    def run(self, log=print) -> TraceSet:
        n = len(self.vectors)
        scope_info = self.scope.info()
        samples = int(scope_info.get("samples") or 0)
        if samples <= 0:
            raise CampaignError("the scope reports no sample count")

        meta = dict(self.vectors.meta())
        meta.update(
            {
                "scope": scope_info,
                "scope_timeout_s": self.scope_timeout,
                "target": self.target.info(),
                "core_version": self.target.info().get("version", "?"),
            }
        )
        ts = TraceSet.create(
            self.out_dir,
            n_traces=n,
            n_samples=samples,
            input_len=len(self.vectors[0].data),
            output_len=max(32, len(self.reference_output) or 32),
            dtype="int16",
            meta=meta,
        )

        log("capturing %d traces -> %s" % (n, self.out_dir))
        log("  %s" % scope_info.get("id", "?"))
        started = time.time()
        last_report = started
        consecutive_errors = 0
        interrupted = False

        try:
            for i, vec in enumerate(self.vectors):
                for attempt in range(2):
                    try:
                        samples_arr, outcome = self._one(vec)
                        break
                    except (P.ProtocolError, TimeoutError) as e:
                        if attempt == 0 and self.target.link.resync():
                            self.stats["retried"] += 1
                            continue
                        consecutive_errors += 1
                        self.stats["failed"] += 1
                        log("  ! trace %d failed: %s" % (vec.index, e))
                        if consecutive_errors >= self.max_errors:
                            raise CampaignError(
                                "%d consecutive failures; stopping" % consecutive_errors
                            )
                        samples_arr, outcome = None, None
                        break
                if samples_arr is None:
                    continue
                if not outcome.ok:
                    consecutive_errors += 1
                    self.stats["failed"] += 1
                    log("  ! trace %d: %s" % (vec.index, outcome.describe()))
                    if consecutive_errors >= self.max_errors:
                        raise CampaignError("%d consecutive failures; stopping" % consecutive_errors)
                    continue

                consecutive_errors = 0
                self._check(vec, outcome, log)
                ts.append(
                    index=i,
                    trace=samples_arr,
                    input_bytes=vec.data,
                    cls=vec.cls,
                    cycles=outcome.cycles,
                    output=outcome.output,
                )
                self.stats["completed"] += 1

                now = time.time()
                if now - last_report >= self.progress_every or i + 1 == n:
                    done = i + 1
                    rate = done / max(now - started, 1e-9)
                    log(
                        "  %6d/%d  %5.1f traces/s  elapsed %s  eta %s"
                        % (
                            done,
                            n,
                            rate,
                            _fmt_eta(now - started),
                            _fmt_eta((n - done) / max(rate, 1e-9)),
                        )
                    )
                    last_report = now
                    ts.flush()
        except KeyboardInterrupt:
            interrupted = True
            log("\ninterrupted: flushing %d traces" % self.stats["completed"])
        finally:
            ts.close(
                {
                    "stats": self.stats,
                    "interrupted": interrupted,
                    "duration_s": round(time.time() - started, 1),
                    "reference_cycles": self.reference_cycles,
                }
            )

        log("")
        log(
            "captured %d of %d traces in %s"
            % (self.stats["completed"], n, _fmt_eta(time.time() - started))
        )
        if self.stats["failed"]:
            log("  %d runs failed and were dropped" % self.stats["failed"])
        if self.stats["retried"]:
            log("  %d run%s recovered after a link resync (check cabling and baud rate "
                "if this keeps happening)"
                % (self.stats["retried"], "" if self.stats["retried"] == 1 else "s"))
        if self.stats["cycle_varied"]:
            log(
                "  WARNING: CYCLE_COUNT varied in %d runs: the core is not "
                "constant-time for these inputs" % self.stats["cycle_varied"]
            )
        elif self.reference_cycles:
            log(
                "  CYCLE_COUNT constant at %d (%.1f us at %.0f MHz)"
                % (self.reference_cycles, self.reference_cycles / P.CORE_MHZ, P.CORE_MHZ)
            )
        return ts
