# Kintex sakura_x_bridge against the template's real Spartan-6 decoder
# (third_party/sakura_x_shell/sakura-x-shell-ctrl/command_control.v + fifo.v).
#
#   make -C testbench/sakura_x                 # board wire delay 3 ns each way
#   make -C testbench/sakura_x WIRE_NS=8.0

ROOT    := $(abspath ../..)
OUT     := $(ROOT)/build/sakura_x
CTRL    := $(ROOT)/third_party/sakura_x_shell/sakura-x-shell-ctrl
WIRE_NS ?= 3.0

IVERILOG ?= iverilog
VVP      ?= vvp

.PHONY: all clean
all: | $(OUT)
	$(IVERILOG) -g2012 -Wno-timescale -I $(CTRL) -s tb_sakura_x_bridge -Ptb_sakura_x_bridge.WIRE_NS=$(WIRE_NS) \
	    -o $(OUT)/tb_sakura_x_bridge.vvp tb_sakura_x_bridge.sv $(ROOT)/hdl/xilinx/sakura_x/sakura_x_bridge.sv \
	    $(CTRL)/command_control.v $(CTRL)/fifo.v
	cd $(OUT) && $(VVP) -n tb_sakura_x_bridge.vvp | tee tb_sakura_x_bridge.log
	@grep -q "TB_SHELL_BRIDGE PASS" $(OUT)/tb_sakura_x_bridge.log

$(OUT):
	mkdir -p $(OUT)

clean:
	rm -rf $(OUT)
