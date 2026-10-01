# Cocotb Makefile for Keccak-f[1600] testbench
#
# Run with:
#   make SIM=verilator    (open-source, fast)
#   make SIM=icarus       (open-source, Verilog-only)
#   make SIM=questa       (commercial, SystemVerilog)

TOPLEVEL_LANG = verilog
VERILOG_SOURCES = ../../hdl/core/keccak_f1600.sv ../../hdl/core/keccak_round.sv
TOPLEVEL = keccak_f1600
MODULE = test_keccak

# Verilator requires --timing for cocotb.
ifeq ($(SIM),verilator)
EXTRA_ARGS += --timing
endif

include $(shell cocotb-config --makefiles)/Makefile.sim
