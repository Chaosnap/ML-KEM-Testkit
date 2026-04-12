# Cocotb Makefile for Barrett reduction testbench

TOPLEVEL_LANG = verilog
VERILOG_SOURCES = ../../hdl/core/mod_reduce.sv
TOPLEVEL = barrett_reduce_3329
MODULE = test_ntt

ifeq ($(SIM),verilator)
EXTRA_ARGS += --timing
endif

include $(shell cocotb-config --makefiles)/Makefile.sim
