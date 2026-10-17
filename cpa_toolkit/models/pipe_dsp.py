"""PIPE DSP48: the operand and product registers of the two multipliers (A/B, M, P of the DSP slices)."""

from .base import PipelineModel


class PipeDsp(PipelineModel):
    title = "PIPE DSP48 registers -- operands a0 b0 and the 24-bit product p1 p2 of both multipliers"
    family = "PIPE"
    group = "dsp"
    equation = "sum over rows k of HD of (a, b, a*b, a*b) between consecutive operations of a multiplier"
    rtl_register = "mul0/mul1: a0 b0 (A/B registers), p1 (M register), p2 (P register)"
    pipeline_stage = "T+4 .. T+8 (operands one cycle after they are presented, the product two and three)"
    required_inputs = "Same as pipe_all."
    limitations = ("Only the DSP slices' own registers: right when the probe sits over the DSP column, blind to "
                   "everything else. The unreduced 24-bit product is the widest data-dependent value in the datapath.")


MODEL = PipeDsp()
