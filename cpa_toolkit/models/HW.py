"""Hamming-weight models of the BaseMul multiplier registers.

``T`` = the cycle the RAM read address of the word holding the coefficient is issued
(``T = BMUL_READ_CYCLES[poly] + 2 * (coeff // 2)``); the operands reach the multiplier at
``T + 3`` (``hdl/core/mlkem_poly_alu.sv``, ``mlkem_modmul.sv``).
"""

from __future__ import annotations

from models.base import HW12, Q, Model, hw24

MODELS = [
    Model("hw_product_mod", "HW of the reduced product", "HW( u[j] * g mod q )",
          "r (Barrett output) -> p00 / p11", 12, (10, 11),
          "anshupriyan Source_code.py, Chaosnap CPA.py (model HW)",
          lambda x: HW12[x % Q],
          "Two multipliers finish a pair in the same cycle, so the partner coefficient adds noise of the "
          "same size as the signal; needs many traces (thousands). u[j] = 0 carries no information."),
    Model("hw_product_raw", "HW of the raw product", "HW( u[j] * g )",
          "DSP product register P (24 bit)", 24, (5, 6),
          "same recipe, one stage earlier (mlkem_modmul.sv p1 / p2)",
          hw24,
          "Raw 24-bit product before the Barrett reduction: more bits toggle, so a stronger signal than the "
          "reduced product when the DSP register is what the probe sees. The same pair noise applies."),
]
