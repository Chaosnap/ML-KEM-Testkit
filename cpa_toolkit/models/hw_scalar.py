"""HW scalar product: Hamming weight of one Barrett product, no R_prev."""

from .base import LeakageModel, hw


class HWScalar(LeakageModel):
    title = "HW scalar multiplication -- Hamming weight, no R_prev needed"
    family = "HW"
    register = "p_self"
    rprevs = ()
    equation = "HW( u_hat[j] * s_guess mod q )"
    rtl_register = "p00 / p11 (one Barrett multiplier's output latch)"
    pipeline_stage = "T+11 of the BMUL pipeline (mlkem_poly_alu.sv lines 269-270)"
    required_inputs = ("u_hat[j] (public, from the known ciphertext). No R_prev: HD(0, X) = HW(X), "
                       "so the previous register content is taken as 0.")
    limitations = ("The register holds this value for 1 clock cycle only -- needs enough probe bandwidth "
                   "and sample rate to resolve it. R_prev = 0 ignores what the register held the cycle "
                   "before, which a real CMOS register never is (HD is the physically motivated default "
                   "for RTL registers).")

    def leak(self, x, r_prev):
        return hw(x, 12)


MODEL = HWScalar()
