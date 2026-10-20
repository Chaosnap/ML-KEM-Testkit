# SAKURA-X portable SRAM images

> 2026-10-20：RTL 已修正本地总线桥与 ML-KEM UART 的波特率不匹配，以及 SW4 复位按钮极性。本次未重新生成此目录中的 `sakura_x_top.bit`；使用修复需要在 Vivado 重新综合、实现并生成 Kintex bitstream。Spartan 原厂 `.bit` 无需修改。

These small binary files are intentionally tracked in normal Git (no Git LFS).
They are outside the ignored `build/` tree so a clone contains both images.

| Image | Header | Device | Purpose |
| --- | --- | --- | --- |
| `sasebo_giii_ctrl.bit` | CN10 | XC6SLX45 FGG484 | Original AIST USB/local-bus controller |
| `sakura_x_top.bit` | CN5 | XC7K160T FBG676 | ML-KEM with local-bus bridge |

See [Linux instructions](../../docs/SAKURA_X_LINUX.md).
`manifest.json` records provenance and SHA-256 values; `timing_impl.rpt` is the
existing Kintex build's report. This is a snapshot, not a live alias of build
outputs. Packaging did not rebuild or hardware-validate these images.
The AIST image retains [AIST-LICENSE.txt](AIST-LICENSE.txt).

From this directory, verify downloads with `sha256sum -c SHA256SUMS`.
When replacing either image after a new build, regenerate `SHA256SUMS`, update
`manifest.json` and the timing report, and commit those changes together.
Explicit bitstream paths can be passed to the programmer to test a fresh build
without replacing these distributed snapshots.
