"""HD BaseMul c0: the even output coefficient of the pair, accumulated."""

from .base import LeakageModel, hd


class HDBaseMulC0(LeakageModel):
    title = "HD BaseMul c0 -- even output coefficient of the pair, accumulated"
    family = "HD"
    register = "c0"
    rprevs = ("prev-word", "zero", "acc")
    equation = "HD( R_prev, (a0*b0 + gamma*a1*b1 + C_old) mod q )"
    rtl_register = "h0"
    pipeline_stage = "T+17 (mlkem_poly_alu.sv line 286)"
    required_inputs = ("u_hat[2w], u_hat[2w+1] (public); s_hat[2w] or s_hat[2w+1] (the guess); the other "
                       "half of the pair and C_old (earlier polynomials' accumulated sum) come from the "
                       "guesses recovered so far (blind) or the real key (oracle-assisted).")
    limitations = ("The Karatsuba cross term and C_old fold in coefficients other than the one guessed, "
                   "adding noise and dependence beyond the target coefficient.")

    def leak(self, x, r_prev):
        return hd(r_prev, x, 12)


MODEL = HDBaseMulC0()
