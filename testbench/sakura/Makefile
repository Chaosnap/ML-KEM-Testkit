# SAKURA-X local-bus bridge against AIST's own Spartan-6 controller source.
#
#   make -C testbench/sakura

ROOT := $(abspath ../..)
OUT  := $(ROOT)/build/sakura
CTRL := $(ROOT)/sasebo_giii/sasebo_giii_ctrl

IVERILOG ?= iverilog
VVP      ?= vvp

.PHONY: all clean
all: | $(OUT)
	$(IVERILOG) -g2012 -Wno-timescale -s tb_lbus_bridge -o $(OUT)/tb_lbus_bridge.vvp \
	    tb_lbus_bridge.sv $(ROOT)/hdl/xilinx/sakura_x/sakura_lbus_bridge.sv \
	    $(ROOT)/hdl/core/uart_tx.sv $(ROOT)/hdl/core/uart_rx.sv \
	    $(CTRL)/ctrl_lbus.v $(CTRL)/syncfifo_8x31.v
	cd $(OUT) && $(VVP) -n tb_lbus_bridge.vvp | tee tb_lbus_bridge.log
	@grep -q "TB_LBUS_BRIDGE PASS" $(OUT)/tb_lbus_bridge.log

$(OUT):
	mkdir -p $(OUT)

clean:
	rm -rf $(OUT)
