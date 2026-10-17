"""PIPE all: every flop of the two Barrett multipliers and of the registers that copy their output."""

from .base import PipelineModel


class PipeAll(PipelineModel):
    title = "PIPE all multiplier flops -- schedule-exact HD, row-aligned (needs cycle features)"
    family = "PIPE"
    group = "all"
    equation = ("sum over rows k of HD( stage register before, stage register after ), for the operation stream "
                "A0, K, A0', K' ... (mul0) and A1, G ... (mul1) of mlkem_poly_alu.sv")
    rtl_register = ("mul0/mul1: a0 b0 p1 p2 sa4 sb4 pl4 qh5 pl5 qq6 pl6 r; alu: p00 p11 p11d p00d[0..5] mr mr2")
    pipeline_stage = "T+4 .. T+20 (T = the cycle the word's RAM read address is issued); one hypothesis per cycle"
    required_inputs = ("u_hat (public); s_hat of the pair (the guess + its partner) and of the neighbouring words "
                       "(true key when scored, the coefficients recovered so far when blind -- an even coefficient "
                       "is attacked on the transitions that do not need its partner). Per-cycle trace features and "
                       "the core clock of the build.")
    limitations = ("Equal weight per flop: a probe over one part of the die sees some groups more than others (compare "
                   "pipe_dsp / pipe_barrett / pipe_output). Covers the multiplier pipeline only -- not the Karatsuba "
                   "subtractions, the accumulator read-back (poly 1, 2) or the write-back, which carry the same data a "
                   "few cycles later. Needs the cycle alignment of the trace (cpa_attack searches +-6 cycles).")


MODEL = PipeAll()
