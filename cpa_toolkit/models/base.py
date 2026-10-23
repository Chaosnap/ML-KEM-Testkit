"""The base class of a CPA leakage model and the bit-count helpers the models share."""

from __future__ import annotations

import numpy as np

import mlkem_ref as R

Q = R.Q

#: Hamming weight of every 12-bit value.
HW12 = np.array([bin(i).count("1") for i in range(4096)], dtype=np.float32)


def hw12(x: np.ndarray) -> np.ndarray:
    """Hamming weight of the low 12 bits."""
    return HW12[np.asarray(x, dtype=np.int64) & 0xFFF]


def hw24(x: np.ndarray) -> np.ndarray:
    """Hamming weight of the low 24 bits."""
    x = np.asarray(x, dtype=np.int64)
    return HW12[x & 0xFFF] + HW12[(x >> 12) & 0xFFF]


def hd(a: np.ndarray, b: np.ndarray, bits: int = 24) -> np.ndarray:
    """Hamming distance of the low ``bits`` bits of ``a`` and ``b`` (for HD models)."""
    x = (np.asarray(a, dtype=np.int64) ^ np.asarray(b, dtype=np.int64)) & ((1 << bits) - 1)
    return hw24(x) if bits > 12 else hw12(x)


class Model:
    """One hypothesis about one register of the BaseMul datapath.

    The default ``hypotheses`` applies ``leak`` to the product ``u * g`` of the public
    coefficient ``u`` and every candidate ``g``. A model that needs something else (for
    example an HD model that also uses the previous register value) overrides
    ``hypotheses``. It must return an ``(N, G)`` float32 array: one row per trace, one
    column per candidate in ``guesses``.

    ``cycles``: the core cycles after ``T`` (the read of the word that holds the
    coefficient) in which the register holds the value; cpa.py predicts the peak from them.
    """

    def __init__(self, name, title, equation, register, bits, cycles, source, leak=None, limitations=""):
        self.name, self.title, self.equation, self.register = name, title, equation, register
        self.bits, self.cycles, self.source, self.leak, self.limitations = bits, tuple(cycles), source, leak, limitations

    def hypotheses(self, u: np.ndarray, guesses: np.ndarray) -> np.ndarray:
        """``(N, G)`` float32 leakage values for ``u`` ``(N,)`` and candidates ``guesses`` ``(G,)``."""
        prod = np.asarray(u, dtype=np.int64)[:, None] * np.asarray(guesses, dtype=np.int64)[None, :]
        return np.asarray(self.leak(prod), dtype=np.float32)

    def describe(self) -> dict:
        return {"name": self.name, "title": self.title, "equation": self.equation, "register": self.register,
                "bits": self.bits, "cycles_after_read": list(self.cycles), "source": self.source,
                "limitations": self.limitations}
