"""What every leakage model shares.

- :class:`Guess`: the BaseMul pair of one secret coefficient ``s_hat[poly][j]``
  with that coefficient replaced by each candidate value -- the register
  values a hypothesis is built on (``REGISTERS``) and what the register held
  before (``RPREVS``).
- :func:`hw` / :func:`hd`: Hamming weight and distance.
- :class:`LeakageModel`: the base class. A model file next to this one only
  names the register it looks at, the R_prev choices it accepts and how the
  register leaks (``leak``).
- :class:`PipelineModel`: a model that follows the RTL schedule instead of naming one
  register -- the transitions of every register of the multiplier pipeline, row by row
  (``mlkem_pipeline.py``). It needs per-cycle trace features and is attacked row-aligned
  (``aligned_cpa.py``); ``models/pipe_*.py`` are such models.
- :func:`check`: does a model build its hypotheses on the values the hardware
  computes? Compared against whole-polynomial BaseMuls (``mlkem_ref``).

Register names, pipeline stages and RTL lines: docs/ATTACK_MODEL.md sections 4-5.
"""

from __future__ import annotations

import numpy as np

import mlkem_pipeline as MP
import mlkem_ref as R

Q, K = R.Q, R.K

#: Registers a hypothesis can be built on, with their width in bits.
REGISTERS = {
    "p_self": 12,   # a_j * g mod q: the single Barrett product (registers p00 / p11)
    "c0": 12,       # even BaseMul output coefficient (+ accumulated C)
    "c1": 12,       # odd BaseMul output coefficient (+ accumulated C)
    "word": 24,     # {c1, c0}: the 24-bit RAM word / wb / rb_din register
    "pipeline": 0,  # not one register: a PipelineModel's flops (see its ``bits``)
}

#: What the register held before (R_prev).
RPREVS = {
    "zero": "R_prev = 0 (Hamming weight; precharged or cleared register)",
    "prev-word": "R_prev = the previous word of the same pass (register rewritten word after word)",
    "acc": "R_prev = C_old, the partial sum of the earlier polynomials (read-modify-write of the RAM word)",
}

_HW12 = np.array([bin(i).count("1") for i in range(4096)], dtype=np.int16)


def hw(x: np.ndarray, bits: int = 24) -> np.ndarray:
    """Hamming weight of the low ``bits`` bits (12 or 24)."""
    x = np.asarray(x)
    if bits == 12:
        return _HW12[x & 0xFFF]
    return _HW12[x & 0xFFF] + _HW12[(x >> 12) & 0xFFF]


def hd(a: np.ndarray, b: np.ndarray, bits: int = 24) -> np.ndarray:
    """Hamming distance over the low ``bits`` bits (12 or 24)."""
    return hw(np.bitwise_xor(a, b), bits)


def accumulated_before(u_hat: np.ndarray, s_hat: np.ndarray, poly: int) -> np.ndarray | None:
    """C_old for BMUL ``poly``: sum_{i<poly} u_hat_i o s_hat_i, ``(N, 256)``."""
    if poly == 0:
        return None
    acc = np.zeros(u_hat.shape[:1] + (256,), dtype=np.int64)
    for i in range(poly):
        acc = (acc + R.basemul(u_hat[:, i, :], s_hat[i][None, :])) % Q
    return acc


class Guess:
    """The BaseMul pair of secret coefficient ``s_hat[poly][j]``, guessed.

    The guess ``g`` replaces coefficient ``j``; its partner in the pair and
    the accumulated value of earlier polynomials come from ``s_hat``. In an
    oracle-assisted evaluation that is the true key (the usual known-key
    evaluation: it measures how far the *leakage* is from key recovery, not
    how good a search is); in a blind attack it is the array of guesses
    recovered so far, updated in place, so ``self.s`` is a view of it.
    """

    def __init__(self, u_hat: np.ndarray, s_hat: np.ndarray, poly: int, j: int = 0):
        self.u = u_hat[:, poly, :]
        self.s = s_hat[poly]
        self.c_old = accumulated_before(u_hat, s_hat, poly)
        self.poly = poly
        self.at(j)

    def at(self, j: int) -> "Guess":
        """Move to coefficient ``j`` of the same polynomial."""
        self.j, self.w, self.e = j, j >> 1, j & 1
        self.true_value = int(self.s[j])
        return self

    # -- BaseMul pair w with coefficient j replaced by g ----------------------
    def _pair(self, g: np.ndarray, rows, w: int):
        e = self.e
        a0 = self.u[rows, 2 * w][:, None]
        a1 = self.u[rows, 2 * w + 1][:, None]
        b0 = int(self.s[2 * w])
        b1 = int(self.s[2 * w + 1])
        if g is not None:
            if e == 0:
                b0 = g[None, :]
            else:
                b1 = g[None, :]
        c0 = (a0 * b0 + (a1 * b1 % Q) * int(R.GAMMAS[w])) % Q
        c1 = (a0 * b1 + a1 * b0) % Q
        if self.c_old is not None:
            c0 = (c0 + self.c_old[rows, 2 * w][:, None]) % Q
            c1 = (c1 + self.c_old[rows, 2 * w + 1][:, None]) % Q
        return a0, a1, c0, c1

    def register(self, name: str, g: np.ndarray | None, rows=slice(None), w: int | None = None) -> np.ndarray:
        """Hypothetical content of register ``name``, ``(N, G)`` (``G = 1`` if ``g`` is None)."""
        w = self.w if w is None else w
        a0, a1, c0, c1 = self._pair(g, rows, w)
        if name == "p_self":
            if g is None:
                gv = int(self.s[2 * w + self.e])
                return ((a0 if self.e == 0 else a1) * gv) % Q
            return ((a0 if self.e == 0 else a1) * g[None, :]) % Q
        if name == "c0":
            return c0
        if name == "c1":
            return c1
        if name == "word":
            return c0 | (c1 << 12)
        raise ValueError("unknown register %r" % name)

    def previous(self, name: str, mode: str, rows=slice(None)) -> np.ndarray:
        """R_prev of register ``name`` for every trace, ``(N,)``, from ``s_hat``."""
        n = self.u[rows].shape[0]
        zero = np.zeros(n, dtype=np.int64)
        if mode == "zero":
            return zero
        if mode == "acc":
            if self.c_old is None or name == "p_self":
                return zero
            lo, hi = self.c_old[rows, 2 * self.w], self.c_old[rows, 2 * self.w + 1]
            return {"c0": lo, "c1": hi, "word": lo | (hi << 12)}[name]
        if mode == "prev-word":
            if self.w == 0:
                return zero
            return self.register(name, None, rows, self.w - 1)[:, 0]
        raise ValueError("unknown R_prev mode %r" % mode)


class LeakageModel:
    """One leakage model: subclass it in a file of its own in this folder.

    Set the class attributes and override :meth:`leak`; the file must end
    with ``MODEL = YourClass()``. The registry (``models/__init__.py``) names
    the model after its file.
    """

    title = ""               # one line: the GUI's dropdown label
    family = ""              # "HW", "HD", ...: groups the dropdown
    register = "word"        # key of REGISTERS: the value the hypothesis is built on
    rprevs: tuple = ()       # accepted R_prev choices (keys of RPREVS), first = default; () = none
    equation = ""            # the hypothesis, for display
    rtl_register = ""        # RTL register name(s), mlkem_poly_alu.sv
    pipeline_stage = ""      # when in the BMUL pipeline it is valid (T+n)
    required_inputs = ""     # what the hypothesis needs besides the guess
    limitations = ""         # what this model does NOT capture
    name = ""                # set by the registry: the file name
    source = ""              # set by the registry: the file path
    aligned = False          # True: a PipelineModel (row-aligned attack on per-cycle features)

    @property
    def bits(self) -> int:
        return REGISTERS[self.register]

    def leak(self, x: np.ndarray, r_prev: np.ndarray) -> np.ndarray:
        """Leakage of register values ``x`` ``(N, G)`` that held ``r_prev`` ``(N, 1)`` before."""
        raise NotImplementedError

    def resolve_rprev(self, rprev: str | None) -> str:
        """The R_prev to use: ``rprev``, the model's default if empty, "" if it takes none."""
        if not self.rprevs:
            return ""
        if not rprev:
            return self.rprevs[0]
        if rprev not in self.rprevs:
            raise ValueError("model %s takes R_prev %s, not %r" % (self.name, " / ".join(self.rprevs), rprev))
        return rprev

    def hypotheses(self, guess: Guess, rprev: str, candidates: np.ndarray, rows=slice(None)) -> np.ndarray:
        """Hypothetical leakage of every trace for every candidate, float32 ``(N, G)``."""
        x = guess.register(self.register, candidates, rows)
        r = guess.previous(self.register, rprev or "zero", rows)[:, None]
        return np.asarray(self.leak(x, r), dtype=np.float32)


class PipelineModel(LeakageModel):
    """A model that follows the RTL schedule: the Hamming distance of every register of one group
    of the multiplier pipeline, in each clock cycle after the attacked word is read.

    Subclass it in a file of its own, set ``group`` (a group of ``mlkem_pipeline.registers``) and the
    texts; the file ends with ``MODEL = YourClass()``. :meth:`row_hypotheses` replaces
    :meth:`hypotheses`: one hypothesis per cycle instead of one per model, and the attack sums their
    correlations at the known cycles (``aligned_cpa.py``) instead of taking the best sample.
    """

    aligned = True
    register = "pipeline"
    rprevs = ()
    group = "all"

    @property
    def regs(self) -> dict:
        return MP.registers(self.group)

    @property
    def bits(self) -> int:
        return MP.bits_of(self.regs)

    def leak(self, x, r_prev):
        raise NotImplementedError("a PipelineModel has no single register: use row_hypotheses()")

    def hypotheses(self, guess, rprev, candidates, rows=slice(None)):
        raise NotImplementedError("a PipelineModel is attacked row-aligned: use row_hypotheses()")

    def row_hypotheses(self, guess: Guess, candidates: np.ndarray, causal: bool = False) -> dict[int, np.ndarray]:
        """``{row: (N, G)}`` hypothetical toggle counts, ``row`` = cycles after the read of the word of ``guess``."""
        return MP.row_hypotheses(guess.u, guess.s, guess.j, candidates, self.regs, causal)

    def rows(self, j: int, causal: bool = False) -> list[int]:
        """The rows :meth:`row_hypotheses` returns for coefficient ``j``."""
        return MP.rows_for(j, causal, self.regs)


# --- check -----------------------------------------------------------------------
def _reference(u: np.ndarray, s: np.ndarray, poly: int, j: int, register: str, rprev: str):
    """Register value and R_prev of coefficient j, computed from whole-polynomial BaseMuls."""
    w, e = j >> 1, j & 1
    acc = np.zeros(u.shape[:1] + (256,), dtype=np.int64)
    for i in range(poly + 1):
        if i == poly:
            c_old = acc.copy()
        acc = (acc + R.basemul(u[:, i, :], s[i][None, :])) % Q

    def value(pair: int, source: np.ndarray) -> np.ndarray:
        if register == "p_self":
            k = 2 * pair + e
            return (u[:, poly, k] * s[poly, k]) % Q
        lo, hi = source[:, 2 * pair], source[:, 2 * pair + 1]
        return {"c0": lo, "c1": hi, "word": lo | (hi << 12)}[register]

    zero = np.zeros(u.shape[0], dtype=np.int64)
    prev = {"": zero, "zero": zero,
            "prev-word": value(w - 1, acc) if w else zero,
            "acc": zero if register == "p_self" else value(w, c_old)}[rprev]
    return value(w, acc), prev


def check_pipeline(model: PipelineModel, n: int = 12, seed: int = 1) -> list[tuple[str, bool, str]]:
    """The pipeline twin against FIPS 203 and the model against the twin.

    1. The operations of the two multipliers, taken from the twin's own schedule (A0, A1, the
       Karatsuba sum K and gamma * A1), combine to the BaseCaseMultiply of ``mlkem_ref`` for every word:
       ``c0 = A0 + G``, ``c1 = K - A0 - A1``. The twin's operations are therefore the arithmetic the
       hardware does, not an unrelated formula.
    2. Every transition lands on a row inside the pipeline (4 .. 25 cycles after the read).
    3. The hypotheses depend on the guess (candidates are told apart), for an even and an odd
       coefficient, with the true key (oracle-assisted) and blind (causal).
    """
    rng = np.random.default_rng(seed)
    u = rng.integers(0, Q, (n, 256))
    s = rng.integers(0, Q, 256)
    out = []
    bad = []
    want = R.basemul(u, s[None, :])
    for w in (0, 1, 2, 3, 4, 17, 64, 100, 126, 127):
        ops = MP.WordOps(u, s, w)
        r = lambda op: MP.modmul_state(*op)["r"]
        a0, a1, kar, gam = r(ops.a0(w)), r(ops.a1(w)), r(ops.kar(w)), r(ops.gam(w))
        c0, c1 = (a0 + gam) % Q, (kar - a0 - a1) % Q
        if not (np.array_equal(c0[:, 0], want[:, 2 * w]) and np.array_equal(c1[:, 0], want[:, 2 * w + 1])):
            bad.append("word %d" % w)
    out.append(("operations match BaseCaseMultiply", not bad,
                "A0, A1, K and the gamma product combine to the FIPS 203 BaseMul at 10 words" if not bad
                else "differ at " + ", ".join(bad)))
    rows = sorted({k for j in (0, 1, 60, 61, 254, 255) for c in (False, True) for k in model.rows(j, c)})
    ok_rows = bool(rows) and rows[0] >= 4 and rows[-1] <= 25
    out.append(("rows inside the pipeline", ok_rows,
                "rows %d .. %d cycles after the word's read" % (rows[0], rows[-1]) if rows else "the model uses no register"))
    g = Guess(np.repeat(u[:, None, :], K, axis=1), np.repeat(s[None, :], K, axis=0), 0)
    cand = np.arange(0, Q, 41)
    varies = {}
    for causal in (False, True):
        for j in (60, 61):
            g.at(j)
            h = model.row_hypotheses(g, cand, causal)
            varies[(causal, j)] = bool(h) and any(float(np.ptp(v, axis=1).max()) > 0 for v in h.values())
    bad = ["%s j=%d" % ("blind" if c else "oracle", j) for (c, j), v in varies.items() if not v]
    out.append(("depends on the guess", not bad,
                "hypotheses differ between candidate values (oracle and blind, both parities)" if not bad
                else "no variation for " + ", ".join(bad)))
    return out


def check(model: LeakageModel, n: int = 8, seed: int = 1) -> list[tuple[str, bool, str]]:
    """Random û and ŝ; for coefficients of every BMUL, the model's hypothesis for
    the true value must equal ``leak`` of the register value and R_prev computed
    independently from whole-polynomial BaseMuls, and the hypotheses must
    depend on the guess. Returns ``[(name, ok, detail), ...]``."""
    if model.aligned:
        return check_pipeline(model)
    rng = np.random.default_rng(seed)
    u = rng.integers(0, Q, (n, K, 256))
    s = rng.integers(0, Q, (K, 256))
    out = []
    for rprev in model.rprevs or ("",):
        bad = []
        for poly in range(K):
            g = Guess(u, s, poly)
            for j in (0, 1, 2, 3, 100, 101, 254, 255):
                got = model.hypotheses(g.at(j), rprev, np.array([s[poly, j]]))[:, 0]
                x, r = _reference(u, s, poly, j, model.register, rprev)
                want = np.asarray(model.leak(x[:, None], r[:, None]), dtype=np.float32)[:, 0]
                if not np.array_equal(got, want):
                    bad.append("BMUL %d coefficient %d" % (poly, j))
        out.append(("register values%s" % (" (R_prev %s)" % rprev if rprev else ""), not bad,
                    "match whole-polynomial BaseMuls at 48 coefficients" if not bad
                    else "differ at " + ", ".join(bad[:4])))
    h = model.hypotheses(Guess(u, s, 1).at(7), model.resolve_rprev(""), np.arange(Q))
    varies = bool(np.ptp(h, axis=1).max() > 0)
    out.append(("depends on the guess", varies, "hypotheses differ between candidate values" if varies
                else "every candidate gives the same hypothesis: this model cannot rank guesses"))
    return out
