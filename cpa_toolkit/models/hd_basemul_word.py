"""HD BaseMul word: the full 24-bit RAM write-back {c1, c0} (the default model)."""

from .base import LeakageModel, hd


class HDBaseMulWord(LeakageModel):
    title = "HD BaseMul word -- full 24-bit RAM write-back (default, closest to a real probe)"
    family = "HD"
    register = "word"
    rprevs = ("prev-word", "zero", "acc")
    equation = "HD( R_prev, {c1, c0} )  (c1 in bits 23:12, c0 in bits 11:0)"
    rtl_register = "wb / wbd / rb_din"
    pipeline_stage = ("T+18-20 internally, written to the polynomial RAM at T+21 "
                      "(mlkem_poly_alu.sv lines 290-291, 301)")
    required_inputs = "Everything hd_basemul_c0 and hd_basemul_c1 each need, combined."
    limitations = ("Both pair halves in one 24-bit hypothesis, so a guess for a single coefficient explains "
                   "only half the register's bits -- the strongest real-probe match of these models per the "
                   "positive control (docs/ATTACK_MODEL.md section 8), but not a single-coefficient model.")

    def leak(self, x, r_prev):
        return hd(r_prev, x, 24)


MODEL = HDBaseMulWord()
