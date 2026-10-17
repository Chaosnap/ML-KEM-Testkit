"""PIPE output: the reduced products and the registers that copy them down the pipeline."""

from .base import PipelineModel


class PipeOutput(PipelineModel):
    title = "PIPE reduced products -- r of both multipliers and the registers that copy them (p00, p11, mr, p00d ...)"
    family = "PIPE"
    group = "output"
    equation = "sum over rows k of HD between consecutive reduced products A0, K, A0', K' ... / A1, G ... (12 bit each)"
    rtl_register = "mul0/mul1: r; alu: p00 p11 p11d p00d[0..5] mr mr2"
    pipeline_stage = "T+10 .. T+20 (the reduced product and its delayed copies)"
    required_inputs = "Same as pipe_all."
    limitations = ("Narrow values (12 bit) repeated across many copies: strong when the probe sees the copy chain, "
                   "blind to the multiplier internals. Replaces hd_scalar / hd_basemul_*, which assume the previous "
                   "word's product was the previous value of these registers (it was the Karatsuba product).")


MODEL = PipeOutput()
