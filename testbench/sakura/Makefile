# SAKURA-X local-bus bridge against AIST's own Spartan-6 controller source.
#
#   make -C testbench/sakura                        # AIST package in <repo>/third_party/aist_sasebo_giii
#   make -C testbench/sakura SASEBO_DIR=/path/to/sasebo_giii
#
# The Spartan-6 side of the bench is AIST's own controller source (ctrl_lbus.v,
# syncfifo_8x31.v from the bundled AIST subset). A custom missing reference
# directory still skips the bench (not failed).

ROOT       := $(abspath ../..)
OUT        := $(ROOT)/build/sakura
SASEBO_DIR ?= $(ROOT)/third_party/aist_sasebo_giii
CTRL       := $(SASEBO_DIR)/sasebo_giii_ctrl

IVERILOG ?= iverilog
VVP      ?= vvp

.PHONY: all clean
all: | $(OUT)
	@if [ ! -f $(CTRL)/ctrl_lbus.v ]; then \
	    echo "SKIP tb_lbus_bridge: AIST's controller source not found at $(CTRL)"; \
	    echo "     (pass SASEBO_DIR=/path/to/sasebo_giii to run it)"; exit 0; fi; \
	$(IVERILOG) -g2012 -Wno-timescale -s tb_lbus_bridge -o $(OUT)/tb_lbus_bridge.vvp \
	    tb_lbus_bridge.sv $(ROOT)/hdl/xilinx/sakura_x/sakura_lbus_bridge.sv \
	    $(ROOT)/hdl/core/uart_tx.sv $(ROOT)/hdl/core/uart_rx.sv \
	    $(CTRL)/ctrl_lbus.v $(CTRL)/syncfifo_8x31.v && \
	cd $(OUT) && $(VVP) -n tb_lbus_bridge.vvp | tee tb_lbus_bridge.log && \
	grep -q "TB_LBUS_BRIDGE PASS" $(OUT)/tb_lbus_bridge.log

$(OUT):
	mkdir -p $(OUT)

clean:
	rm -rf $(OUT)

# Real board top + ML-KEM UART system, with functional vendor clock models.
# Covers both directions and response CRC; USB hardware is not simulated.
.PHONY: integration
integration: | $(OUT)
	$(IVERILOG) -g2012 -Wno-timescale -s tb_sakura_top_link -o $(OUT)/tb_sakura_top_link.vvp \
	    tb_sakura_top_link.sv $(ROOT)/hdl/xilinx/sakura_x/*.sv $(ROOT)/hdl/core/*.sv \
	    $(CTRL)/ctrl_lbus.v $(CTRL)/syncfifo_8x31.v
	cd $(ROOT) && $(VVP) -n $(OUT)/tb_sakura_top_link.vvp > $(OUT)/tb_sakura_top_link.log
	cat $(OUT)/tb_sakura_top_link.log
	grep -q "TB_SAKURA_TOP_LINK PASS" $(OUT)/tb_sakura_top_link.log
