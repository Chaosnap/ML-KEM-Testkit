# System tests on the real core (Verilator: the whole core is too slow for
# Icarus).
#
#   make -C testbench/cdc          tb_cdc: 100 MHz AXI-Lite master -> axil_cdc ->
#                                  pqc_mlkem_top on a 206 MHz clock (registers,
#                                  byte strobes, ACVP KeyGen and Decaps)
#   make -C testbench/cdc bridge   tb_bridge: host protocol -> uart_axi_bridge ->
#                                  axil_cdc -> core; 0x05 Run with a real Decaps

ROOT  := $(abspath ../..)
RTL   := $(ROOT)/hdl/core
OUT   := $(ROOT)/build/cdc
TB    := $(abspath .)

VERILATOR ?= verilator
CORE      := $(filter-out $(RTL)/uart_axi_bridge.sv $(RTL)/uart_rx.sv $(RTL)/uart_tx.sv, $(wildcard $(RTL)/*.sv))

.PHONY: all bridge clean

all: $(OUT)/obj_dir/Vtb_cdc
	python3 $(TB)/gen_vectors.py $(OUT)
	cd $(OUT) && ./obj_dir/Vtb_cdc +dir=$(OUT) | tee tb_cdc.log
	@grep -q "TB_CDC PASS" $(OUT)/tb_cdc.log

$(OUT)/obj_dir/Vtb_cdc: $(TB)/tb_cdc.sv $(CORE) $(wildcard $(RTL)/*.hex)
	mkdir -p $(OUT)
	cd $(OUT) && $(VERILATOR) --binary --timing -O3 -j 0 -Wno-fatal -Wno-lint -Wno-style \
		--top-module tb_cdc -o Vtb_cdc $(TB)/tb_cdc.sv $(CORE) > verilator.log

bridge: $(OUT)/obj_bridge/Vtb_bridge
	python3 $(TB)/gen_vectors.py $(OUT)
	cd $(OUT) && ./obj_bridge/Vtb_bridge +dir=$(OUT) | tee tb_bridge.log
	@grep -q "TB_BRIDGE PASS" $(OUT)/tb_bridge.log

$(OUT)/obj_bridge/Vtb_bridge: $(TB)/tb_bridge.sv $(CORE) $(RTL)/uart_axi_bridge.sv $(wildcard $(RTL)/*.hex)
	mkdir -p $(OUT)
	cd $(OUT) && $(VERILATOR) --binary --timing -O3 -j 0 -Wno-fatal -Wno-lint -Wno-style \
		--Mdir obj_bridge --top-module tb_bridge -o Vtb_bridge $(TB)/tb_bridge.sv $(CORE) $(RTL)/uart_axi_bridge.sv > verilator_bridge.log

clean:
	rm -rf $(OUT)
