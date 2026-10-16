"""Template for a new leakage model (files starting with _ are not loaded).

1. Copy this file to models/<your_name>.py (lower case, no leading _).
2. Pick the register the hypothesis is built on (base.REGISTERS):
     p_self  a_j * g mod q, one Barrett product          12 bit
     c0, c1  even / odd BaseMul output, accumulated      12 bit
     word    {c1, c0}, the 24-bit RAM write-back         24 bit
3. Pick the R_prev choices it accepts (base.RPREVS: zero, prev-word, acc),
   default first, or () if the model does not use what the register held.
4. Write leak(x, r_prev): x is (N traces, G candidates) of register values,
   r_prev is (N, 1); return a number per (trace, candidate). The CPA
   correlates it with the traces.
5. Open the GUI's Analysis page (or reload it): the model is in the list.
   Run its Self-test first -- it checks that the register values the model
   sees are the ones the hardware computes and that the hypothesis depends
   on the guess.

The example below is a "single bit" model: the hypothesis is one bit of the
24-bit word, the classic DPA-style selection function.
"""

from .base import LeakageModel


class WordBit(LeakageModel):
    title = "Word bit 0 -- one bit of the RAM write-back (DPA-style example)"
    family = "Bit"
    register = "word"
    rprevs = ()
    equation = "bit 0 of {c1, c0}"
    rtl_register = "wb / wbd / rb_din"
    pipeline_stage = "T+21 (written to the polynomial RAM)"
    required_inputs = "Same as hd_basemul_word."
    limitations = "Uses 1 of 24 bits: much weaker than a Hamming-distance model on the same register."

    def leak(self, x, r_prev):
        return x & 1


MODEL = WordBit()
