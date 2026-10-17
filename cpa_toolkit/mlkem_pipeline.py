"""Software twin of the BMUL multiplier pipeline (``mlkem_poly_alu.sv`` + ``mlkem_modmul.sv``).

The leakage models of ``models/base.py`` hypothesise one register; the chip toggles
~1400 flops per cycle. This module reproduces, bit for bit, what the *stage registers of
the two Barrett multipliers and the registers that copy their output stream* hold, so a
hypothesis can be built from the transitions that really happen on them.
``scripts/sim/pipeline_check.py`` (and ``tests/test_pipeline.py`` when the Verilator
model is built) compare the twin with the RTL simulation: the toggle count of every
covered register matches at every row, for all three decryption BMULs.

Schedule (``mlkem_poly_alu.sv``; ``T`` = the cycle a word's read address is at the RAM,
one new word every 2 cycles; slot A holds s_hat, slot B u_hat)::

    mul0 operands   T+3: (s0,       u0)            a0 * b0          -> A0
                    T+4: (s0 + s1,  u0 + u1)       Karatsuba sum    -> K     (mod q)
    mul1 operands   T+3: (s1,       u1)            a1 * b1          -> A1
                    T+4: (A1 of the word read 3 words earlier, its gamma)   -> G

so every multiplier gets a new operation *every* cycle and the two kinds alternate:
``..., A0_w, K_w, A0_(w+1), K_(w+1), ...`` on mul0 and ``..., A1_w, G_(w-3), A1_(w+1),
G_(w-2), ...`` on mul1. A register that holds an operation ``d`` cycles after its
operands were presented toggles, in that cycle, by the Hamming distance between the
previous operation and this one: one transition per register per cycle, and the
transitions tile every row. (The textbook "previous word, same operation" is not what
these registers see: between A0_(w-1) and A0_w there is K_(w-1).)
"""

from __future__ import annotations

import numpy as np

import mlkem_ref as R

Q = R.Q
#: 37-bit mask of the ``pm = sa4 - sb4`` subtraction in mlkem_modmul.sv.
_M37 = (1 << 37) - 1

#: (register, bits, cycles after the operands are presented) of one mlkem_modmul.
MODMUL_REGS = (("a0", 12, 1), ("b0", 12, 1), ("p1", 24, 2), ("p2", 24, 3),
               ("sa4", 37, 4), ("sb4", 37, 4), ("pl4", 13, 4), ("qh5", 13, 5), ("pl5", 13, 5),
               ("qq6", 13, 6), ("pl6", 13, 6), ("r", 12, 7))

#: Registers outside mlkem_modmul that only copy a multiplier's output ``r``:
#: ``name -> [(multiplier, cycles after the operands are presented)]`` (a register holding
#: both halves, like ``mr``, lists both multipliers).
OUTPUT_COPIES = {
    "alu.p00": [(0, 8)], "alu.p11": [(1, 8)], "alu.p11d": [(1, 9)],
    "alu.p00d[0]": [(0, 9)], "alu.p00d[1]": [(0, 10)], "alu.p00d[2]": [(0, 11)],
    "alu.p00d[3]": [(0, 12)], "alu.p00d[4]": [(0, 13)], "alu.p00d[5]": [(0, 14)],
    "alu.mr": [(0, 8), (1, 8)], "alu.mr2": [(0, 9), (1, 9)],
}

#: Flops the twin covers, per physical group (what a near-field probe over one part of the
#: die couples to): the DSP48 operand / product registers, the Barrett shift-add stages
#: in LUT fabric, and the registers that carry the reduced products on.
GROUPS = {
    "dsp": ("a0", "b0", "p1", "p2"),
    "barrett": ("sa4", "sb4", "pl4", "qh5", "pl5", "qq6", "pl6"),
}


def registers(group: str = "all") -> dict[str, list[tuple[int, str, int]]]:
    """The registers the twin covers, named as in ``testbench/core_sim/bmul_regs.svh``:
    ``name -> [(multiplier, field of the modmul state, delay)]``.

    ``group``: ``"all"``, ``"dsp"`` (DSP48 operand / product registers), ``"barrett"``
    (shift-add stages of the reduction), ``"output"`` (the reduced products ``r`` and the
    registers that copy them).
    """
    if group not in ("all", "dsp", "barrett", "output"):
        raise ValueError("unknown register group %r" % group)
    out = {}
    for m in (0, 1):
        for field, _bits, d in MODMUL_REGS:
            keep = (group == "all" or (group == "output" and field == "r")
                    or (group in GROUPS and field in GROUPS[group]))
            if keep:
                out["mul%d.%s" % (m, field)] = [(m, field, d)]
    if group in ("all", "output"):
        for name, parts in OUTPUT_COPIES.items():
            out[name] = [(m, "r", d) for m, d in parts]
    return out


def bits_of(regs: dict[str, list[tuple[int, str, int]]]) -> int:
    """Total flops in a register selection."""
    width = {f: b for f, b, _ in MODMUL_REGS}
    return sum(width[field] for parts in regs.values() for _m, field, _d in parts)


def popcount(x: np.ndarray) -> np.ndarray:
    """Number of set bits of non-negative integers (up to 64 bit), as uint8."""
    return np.bitwise_count(np.asarray(x))


def _unsigned(x: np.ndarray, bits: int) -> np.ndarray:
    """``x`` (non-negative) in the smallest unsigned dtype that holds ``bits`` bits."""
    return x.astype(np.uint16 if bits <= 16 else np.uint32 if bits <= 32 else np.uint64, copy=False)


class _State:
    """The stage registers of one mlkem_modmul for the operands ``a, b`` (< q), computed on demand."""

    _BITS = {f: b for f, b, _ in MODMUL_REGS}

    def __init__(self, a, b):
        self.a = np.asarray(a, dtype=np.int64)
        self.b = np.asarray(b, dtype=np.int64)
        self._v: dict[str, np.ndarray] = {}

    def get(self, field: str) -> np.ndarray:
        v = self._v.get(field)
        if v is None:
            v = self._v[field] = _unsigned(self._compute(field), self._BITS[field])
        return v

    def _p(self) -> np.ndarray:
        if "_p" not in self._v:
            self._v["_p"] = self.a * self.b
        return self._v["_p"]

    def _compute(self, f: str) -> np.ndarray:
        if f == "a0":
            return self.a
        if f == "b0":
            return self.b
        p = self._p()
        if f in ("p1", "p2"):
            return p
        if f == "sa4":
            return (p << 12) + (p << 10)
        if f == "sb4":
            return (p << 6) + (p << 4) + p
        if f in ("pl4", "pl5", "pl6"):
            return p & 0x1FFF
        if f == "qh5":
            return (((p * 5039) & _M37) >> 24) & 0x1FFF        # pm = sa4 - sb4 = p * 5039
        if f == "qq6":
            qh = self.get("qh5").astype(np.int64)
            return ((qh << 11) + (qh << 10) + (qh << 8) + qh) & 0x1FFF
        if f == "r":
            return p % Q
        raise KeyError(f)


def modmul_state(a: np.ndarray, b: np.ndarray) -> dict[str, np.ndarray]:
    """What every stage register of ``mlkem_modmul`` holds for the operands ``a, b`` (< q)."""
    st = _State(a, b)
    return {f: st.get(f).astype(np.int64) for f, _b, _d in MODMUL_REGS}


class WordOps:
    """The operations of the two multipliers around one attacked word ``w`` of one BMUL.

    ``u``: ``(N, 256)`` u_hat of the polynomial; ``s``: ``(256,)`` s_hat (the true key, or
    the guesses recovered so far); ``g``: ``(G,)`` candidates for ``s[j]``, or None to use
    ``s`` as it is. Operations are ``(a, b)`` operand pairs broadcast to ``(N, G)``
    (``(N, 1)`` when they do not depend on the guess); ``None`` stands for "no such operation"
    (before the first words of a BMUL).
    """

    def __init__(self, u: np.ndarray, s: np.ndarray, w: int, g: np.ndarray | None = None, j: int | None = None):
        self.w = w
        self.u = np.asarray(u, dtype=np.int64)
        self.s = np.asarray(s, dtype=np.int64)
        self.g = None if g is None else np.asarray(g, dtype=np.int64)[None, :]
        self.j = j

    def _sv(self, word: int):
        """``(s0, s1)`` of ``word``; the guessed one is the candidate row vector."""
        s0, s1 = int(self.s[2 * word]), int(self.s[2 * word + 1])
        if word == self.w and self.g is not None:
            return (s0, self.g) if self.j & 1 else (self.g, s1)
        return s0, s1

    def _uv(self, word: int):
        return self.u[:, 2 * word][:, None], self.u[:, 2 * word + 1][:, None]

    def a0(self, word: int):
        s0, _ = self._sv(word)
        return s0, self._uv(word)[0]

    def kar(self, word: int):
        s0, s1 = self._sv(word)
        u0, u1 = self._uv(word)
        return (s0 + s1) % Q, (u0 + u1) % Q

    def a1(self, word: int):
        _, s1 = self._sv(word)
        return s1, self._uv(word)[1]

    def gam(self, word: int):
        """The gamma operation of ``word``: ``(a1*b1 mod q, gamma)``."""
        a, b = self.a1(word)
        return (a * b) % Q, np.int64(R.GAMMAS[word])

    # -- the issue streams around word w --------------------------------------------
    def mul0(self, which: str):
        """``prev`` (K of word w-1), ``cur`` (A0 of w), ``next`` (K of w), ``after`` (A0 of w+1)."""
        w = self.w
        return {"prev": lambda: self.kar(w - 1) if w >= 1 else None, "cur": lambda: self.a0(w),
                "next": lambda: self.kar(w), "after": lambda: self.a0(w + 1) if w + 1 < 128 else None}[which]()

    def mul1(self, which: str):
        """``prev`` (G of w-4), ``cur`` (A1 of w), ``next`` (G of w-3), ``after`` (A1 of w+1),
        and around the gamma operation of w itself: ``gin`` (A1 of w+3), ``gam`` (G of w), ``gout`` (A1 of w+4)."""
        w = self.w
        return {"prev": lambda: self.gam(w - 4) if w >= 4 else None, "cur": lambda: self.a1(w),
                "next": lambda: self.gam(w - 3) if w >= 3 else self.a1(w),   # before pv[10]: operands repeat A1
                "after": lambda: self.a1(w + 1) if w + 1 < 128 else None,
                "gin": lambda: self.a1(w + 3) if w + 3 < 128 else None, "gam": lambda: self.gam(w),
                "gout": lambda: self.a1(w + 4) if w + 4 < 128 else None}[which]()

    def op(self, m: int, which: str):
        return self.mul0(which) if m == 0 else self.mul1(which)


def transition_hd(reg: tuple[int, str, int], x: tuple | None, y: tuple | None) -> np.ndarray | int:
    """Hamming distance of one register field between operations ``x`` -> ``y`` ((a, b) pairs)."""
    if x is None or y is None:
        return 0
    field = reg[1]
    return popcount(_State(*x).get(field) ^ _State(*y).get(field)).astype(np.int16)


# --- hypotheses -----------------------------------------------------------------------
#: ``(multiplier, first op, second op, base row, needs)``: the transitions that involve word
#: ``w``'s operations. A register at ``delay`` cycles toggles in row ``base + delay`` after ``T``.
#: ``needs``: what else the transition reads besides the guess and earlier words:
#: ``"partner"`` the other coefficient of the pair, ``"later"`` s_hat of later words.
_TERMS = (
    (0, "prev", "cur", 3, ()),                      # K(w-1) -> A0(w)
    (0, "cur", "next", 4, ("partner",)),            # A0(w)  -> K(w)
    (0, "next", "after", 5, ("partner", "later")),  # K(w)   -> A0(w+1)
    (1, "prev", "cur", 3, ()),                      # G(w-4) -> A1(w)
    (1, "cur", "next", 4, ()),                      # A1(w)  -> G(w-3)
    (1, "gin", "gam", 10, ("later",)),              # A1(w+3) -> G(w)
    (1, "gam", "gout", 11, ("later",)),             # G(w)   -> A1(w+4)
)

#: Transitions that change with the guessed coefficient (``j`` even guesses s0, odd s1).
_DEPENDS = {0: {(0, "prev", "cur"), (0, "cur", "next"), (0, "next", "after")},
            1: {(0, "cur", "next"), (0, "next", "after"), (1, "prev", "cur"), (1, "cur", "next"),
                (1, "gin", "gam"), (1, "gam", "gout")}}


def terms_for(j: int, causal: bool) -> list[tuple[int, str, str, int]]:
    """The ``(multiplier, first, second, base row)`` transitions a hypothesis for coefficient ``j`` uses.

    Only transitions that change with the guess: the others add the same number to every
    candidate and only dilute the correlation. With ``causal`` the transitions that read a
    value a blind attack does not have yet are dropped: the partner of an even coefficient
    (it is guessed second) and all later words (coefficients are recovered in ascending
    order). The odd coefficient of a pair always has its partner.
    """
    e = j & 1
    out = []
    for m, first, second, base, needs in _TERMS:
        if (m, first, second) not in _DEPENDS[e]:
            continue
        if causal and ("later" in needs or ("partner" in needs and e == 0)):
            continue
        out.append((m, first, second, base))
    return out


def rows_for(j: int, causal: bool, regs: dict | None = None) -> list[int]:
    """The rows (cycles after the read of word ``j // 2``) that carry a term of ``row_hypotheses``."""
    regs = registers() if regs is None else regs
    rows = set()
    for m, _f, _s, base in terms_for(j, causal):
        rows.update(base + d for parts in regs.values() for mm, _fld, d in parts if mm == m)
    return sorted(rows)


def row_hypotheses(u_poly: np.ndarray, s_poly: np.ndarray, j: int, candidates: np.ndarray,
                   regs: dict[str, list[tuple[int, str, int]]] | None = None,
                   causal: bool = False) -> dict[int, np.ndarray]:
    """Hypothetical toggle count of the twin's registers in each row after ``T`` of word ``j // 2``.

    ``u_poly``: ``(N, 256)``; ``s_poly``: ``(256,)`` s_hat of this polynomial (the true key, or
    the guesses recovered so far -- only the partner coefficient and neighbouring words are read
    from it); ``candidates``: ``(G,)`` values for ``s_poly[j]``. Returns ``{row: (N, G) int16}``.
    With ``causal=True`` only what a blind attack that recovers coefficients in ascending order
    has is read (see :func:`terms_for`).
    """
    regs = registers() if regs is None else regs
    ops = WordOps(u_poly, s_poly, j >> 1, candidates, j)
    cache: dict[tuple[int, str], _State | None] = {}

    def state(m: int, which: str):
        key = (m, which)
        if key not in cache:
            o = ops.op(m, which)
            cache[key] = None if o is None else _State(*o)
        return cache[key]

    out: dict[int, np.ndarray] = {}
    for m, first, second, base in terms_for(j, causal):
        x, y = state(m, first), state(m, second)
        if x is None or y is None:
            continue
        by_field: dict[str, np.ndarray] = {}
        for parts in regs.values():
            for mm, field, d in parts:
                if mm != m:
                    continue
                hd = by_field.get(field)
                if hd is None:
                    hd = by_field[field] = popcount(x.get(field) ^ y.get(field))
                k = base + d
                if k in out:
                    out[k] = out[k] + hd
                else:
                    out[k] = hd.astype(np.int16)
    shape = (np.shape(u_poly)[0], len(candidates))
    return {k: np.broadcast_to(v, shape) for k, v in out.items()}       # terms without the guess are (N, 1)
