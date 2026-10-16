"""HD BaseMul c1: the odd output coefficient of the pair, accumulated."""

from .base import LeakageModel, hd


class HDBaseMulC1(LeakageModel):
    title = "HD BaseMul c1 -- odd output coefficient of the pair, accumulated"
    family = "HD"
    register = "c1"
    rprevs = ("prev-word", "zero", "acc")
    equation = "HD( R_prev, (a0*b1 + a1*b0 + C_old) mod q )"
    rtl_register = "h1d[4]"
    pipeline_stage = "T+18 (mlkem_poly_alu.sv lines 283-285)"
    required_inputs = "Same as hd_basemul_c0, for the odd half of the pair."
    limitations = "Same as hd_basemul_c0."

    def leak(self, x, r_prev):
        return hd(r_prev, x, 12)


MODEL = HDBaseMulC1()
