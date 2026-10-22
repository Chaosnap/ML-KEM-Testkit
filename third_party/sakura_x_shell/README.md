# Pinned sakura-x-shell dependency

Upstream: https://github.com/hal-lab-u-tokyo/sakura-x-shell

Commit: `4be342788ee0babe1e666302cce864c7d9fc7f8e`.
This is a selected **unchanged** source / firmware snapshot, not a submodule.
`manifest.json` and `SHA256SUMS` record the exact included upstream files.
The original local clone is not required to build or use this port.

- `sakura-x-shell-ctrl/sakura-x-shell-ctrl.bit`: prebuilt Spartan-6 shell,
  updated upstream in `d82befc` after adding Channel-B RTS reset. SRAM load;
  no ISE installation is needed to use this supplied bitstream.
- `.mcs`: upstream PROM image retained for reference, not used by our SRAM procedure.
- Controller RTL and ISE project: reference / optional ISE rebuild.
- `ft2232h_config`: Channel A 245 FIFO, Channel B UART, product string configuration.
- `vivado/sakura-x-k7.xdc`: original parallel pin map (reference only).
  Our K7 build uses `hdl/xilinx/sakura_shell/constraints.xdc`.

The upstream README says 20 MHz, but its checked-in `pll.v` / `.xco` specify
24 MHz × 25 / 12 = **50 MHz**; `ctrl_top.v` defaults to a 20 ns period.
Our bridge uses 50 MHz. The actual prebuilt image / board clock has not been
measured here. Verify it on Linux / hardware before drawing timing conclusions.
The upstream K7 XDC's 10 ns bus constraint is not reused.

License: upstream MIT (`LICENSE`); AIST notices on pin constraints and Xilinx
notices on generated clock IP remain applicable. See `AIST_NOTICE.txt` and
original per-file notices. The root project's MIT license does not replace them.

See [the project guide](../../docs/SAKURA_X_SHELL.md) for build and programming.
