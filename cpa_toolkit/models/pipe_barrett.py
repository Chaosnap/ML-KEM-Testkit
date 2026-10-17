"""PIPE Barrett: the shift-add reduction stages of the two multipliers (LUT fabric)."""

from .base import PipelineModel


class PipeBarrett(PipelineModel):
    title = "PIPE Barrett reduction stages -- sa4 sb4 pl4 qh5 pl5 qq6 pl6 of both multipliers"
    family = "PIPE"
    group = "barrett"
    equation = "sum over rows k of HD of the 37-bit shifted products, the quotient estimate and its product with q"
    rtl_register = "mul0/mul1: sa4 sb4 (37 bit), pl4 qh5 pl5 qq6 pl6 (13 bit)"
    pipeline_stage = "T+7 .. T+10 (stages 4 to 6 of the 7-stage multiplier)"
    required_inputs = "Same as pipe_all."
    limitations = ("The widest block of toggling flops (about 280): the best single group when the probe sees the "
                   "fabric around the DSPs, blind to the DSP slices and to the output copies.")


MODEL = PipeBarrett()
