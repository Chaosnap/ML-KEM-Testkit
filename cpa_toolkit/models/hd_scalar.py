"""HD scalar product: the same Barrett product as hw_scalar, with R_prev modelled."""

from .base import LeakageModel, hd


class HDScalar(LeakageModel):
    title = "HD scalar multiplication -- same register as HW, with R_prev modelled"
    family = "HD"
    register = "p_self"
    rprevs = ("prev-word", "zero")
    equation = "HD( R_prev, u_hat[j] * s_guess mod q )"
    rtl_register = "p00 / p11 (one Barrett multiplier's output latch)"
    pipeline_stage = "T+11 of the BMUL pipeline (mlkem_poly_alu.sv lines 269-270)"
    required_inputs = ("u_hat[j] (public) + R_prev = the same physical register's content from the previous "
                       "BMUL pair (the multiplier alternates between a0*b0 / a1*b1 every other cycle).")
    limitations = ("Same 1-cycle window as hw_scalar. R_prev assumes the register is a pipeline latch "
                   "overwritten pair after pair -- true for p00/p11, but not derived from a gate-level model.")

    def leak(self, x, r_prev):
        return hd(r_prev, x, 12)


MODEL = HDScalar()
