# System test of axil_cdc: 100 MHz AXI-Lite master -> axil_cdc ->
# pqc_mlkem_top on a 206.25 MHz clock (registers, byte strobes, ACVP KeyGen
# and Decaps through the crossing). Verilator: the whole core is too slow
# for Icarus.
#
#   make -C testbench/cdc

ROOT  := $(abspath ../..)
RTL   := $(ROOT)/hdl/core
OUT   := $(ROOT)/build/cdc
TB    := $(abspath .)

VERILATOR ?= verilator
CORE      := $(filter-out $(RTL)/uart_axi_bridge.sv $(RTL)/uart_rx.sv $(RTL)/uart_tx.sv, $(wildcard $(RTL)/*.sv))

.PHONY: all clean

all: $(OUT)/obj_dir/Vtb_cdc
	python3 $(TB)/gen_vectors.py $(OUT)
	cd $(OUT) && ./obj_dir/Vtb_cdc +dir=$(OUT) | tee tb_cdc.log
	@grep -q "TB_CDC PASS" $(OUT)/tb_cdc.log

$(OUT)/obj_dir/Vtb_cdc: $(TB)/tb_cdc.sv $(CORE) $(wildcard $(RTL)/*.hex)
	mkdir -p $(OUT)
	cd $(OUT) && $(VERILATOR) --binary --timing -O3 -j 0 -Wno-fatal -Wno-lint -Wno-style \
		--top-module tb_cdc -o Vtb_cdc $(TB)/tb_cdc.sv $(CORE) > verilator.log

clean:
	rm -rf $(OUT)
