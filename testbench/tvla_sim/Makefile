# Verilator model for simulation-based TVLA (see docs/SIM_TVLA_GUIDE.md).
#
#   make            build obj_dir/Vtb_tvla
#   make clean

VERILATOR ?= verilator
SRCS := tb_tvla.sv $(wildcard ../../hdl/core/*.sv)

obj_dir/Vtb_tvla: $(SRCS)
	$(VERILATOR) --binary --timing --trace -j 0 \
		-Wno-fatal -Wno-lint -Wno-style \
		--top-module tb_tvla -o Vtb_tvla $(SRCS)

.PHONY: clean
clean:
	rm -rf obj_dir
