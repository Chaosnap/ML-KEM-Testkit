# Verilator model of the full ML-KEM core for batch runs (ACVP, profiling).
#
#   make            build obj_dir/Vtb_core
#   make clean
#
# Normally driven by scripts/sim_acvp.py and scripts/profile_ucode.py,
# which rebuild it as needed.

VERILATOR ?= verilator
SRCS := tb_core.sv $(wildcard ../../hdl/core/*.sv)

obj_dir/Vtb_core: $(SRCS) $(wildcard ../../hdl/core/*.hex)
	$(VERILATOR) --binary --timing -O3 -j 0 \
		-Wno-fatal -Wno-lint -Wno-style \
		--top-module tb_core -o Vtb_core $(SRCS)

.PHONY: clean
clean:
	rm -rf obj_dir
