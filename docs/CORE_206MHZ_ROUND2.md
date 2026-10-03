# 206 MHz 核心：第二轮代码交付

日期：2026-10-03。当前原项目目录中的代码就是第二轮版本；不再继续调整设计。

## 实现结果

Vivado 2024.1，在现有 `vivado-container` 内运行，器件 `xc7a100tcsg324-1`。输入时钟 100 MHz，MMCM 核心输出 206 MHz。没有改变 Docker image，没有下载或安装工具，两轮实现没有并行运行。

| 指标 | 截图对应版本 | 第一轮 | 当前第二轮 |
| --- | ---: | ---: | ---: |
| WNS / ns | −0.068 | +0.002 | **+0.093** |
| WHS / ns | +0.024 | +0.014 | **+0.036** |
| 建立时间失败端点 | 9 | 0 | **0** |
| 核心 LUT | 7365 | 7390 | **7248** |
| 核心 FF | 4540 | 4553 | **4623** |
| 核心 RAMB36 / DSP | 3 / 2 | 3 / 2 | **3 / 2** |

当前第二轮 TNS/THS/TPWS 均为 0，WPWS +1.177 ns。13942 条可路由网络全部完成布线，路由错误为 0。没有无时钟寄存器或未约束的内部端点。DRC 保留两项 DSP 输入流水化建议（DPIP-1 Warning），无 DRC Error。

相对第一轮，第二轮减少 142 LUT，增加 70 FF，WNS 增加 0.091 ns。相对截图版本，减少 117 LUT，增加 83 FF。相对最初 f206_3 基线（7657 LUT、4732 FF），最终减少 409 LUT（5.34%）、109 FF（2.30%）。资源只统计 `u_mlkem`；顶层包含 UART 和 CDC，不应混用。

这些是本次已完成布局布线的实际报告，不是仿真推算。其他工程设置、工具版本或后续 RTL 变更需要重新实现确认。

## 交付的代码变化

1. `hdl/core/mlkem_unpack.sv`：把宽字段提取拆为字节窗口选择寄存和小范围位移两级，减少原来的宽移位关键路径；同步流水有效信号。
2. `hdl/core/mlkem_ctrl.sv`：密文比较按四个字节分别寄存比较结果，再更新累计不匹配标志，切开 RAM 读出到长比较归并的路径。
3. `hdl/core/keccak_sponge.sv`：第二轮新增寄存的 one-hot 预取选择，完整 lane 消费时移位，半 lane 消费时保持，置换输出装载时重新初始化。
4. 扩展解包单元测试到 d=1…12，并增加 `scripts/sim_rejection_boundary.py`，覆盖最后一个比较字发生不匹配以及之后正常运行时的标志清零。

保留上一阶段的 Keccak 两拍轮函数、核心 FIFO 和压缩通路优化。UART bridge/RX/TX 源码与最初基线逐字节相同，仍运行在 100 MHz；未加入 masking、shuffling、blinding 或随机等待。算法要求的采样、比较、CSEL 和隐式拒绝保留。

## 验证与执行时间

`rtl_validation/validation.txt` 记录全部功能回归：ACVP 60/60、额外有效性/参数 20 项、隐式拒绝边界 16/16，另有解包 15872 个系数、压缩 34944 字节、sponge 104 向量、FIFO 12000 拍、独立 Keccak 及 100/206 MHz CDC 测试。TVLA 模型编译通过，但没有进行实际功耗 TVLA 或 CPA 采集。

平均核心周期：KeyGen 16653.08，Encaps 19245.48，Decaps 26304.30。第二轮与第一轮周期相同；相对截图版本分别增加 15、20、27 拍，来自解包流水级。按 206 MHz 换算，核心执行耗时约 80.84、93.42、127.69 μs，不含 UART 传输。

## 对应文件与使用

- `reports/core206_debug2/timing_summary.rpt`：最终时序报告。
- `reports/core206_debug2/utilization_hier.rpt`：最终层级资源报告。
- `reports/core206_debug2/implemented.dcp`：本次通过时序的已布线检查点。
- `reports/core206_debug2/source_hashes.json`：28 个 RTL/约束文件的 SHA-256。
- `reports/core206_debug2/rtl_validation/`：对应代码的功能验证与周期分析。
- `deliverables/ML-KEM-Testkit_core206_round2_source.zip`：源码快照，包含 HDL、脚本、测试源码、文档和本次文本报告；不含构建缓存、ACVP 数据集或大型 DCP。

现有原项目已更新。若使用手动维护源文件集的 GUI 工程，确认包含新增的 `hdl/core/mlkem_word_fifo.sv`；重置后重新综合/实现才会替换 GUI 中旧的运行结果。已有旧 `.bit` 不代表第二轮版本；本次交付源码和已布线 DCP，没有重新生成 bitstream。

功能验证：在项目根目录执行 `bash scripts/check_core206.sh`，使用现有 Icarus、Verilator 和本地 ACVP 向量。

实现复现：在容器内项目根目录 source Vivado settings64.sh 后，以新的 build/reports 目录运行 `scripts/vivado_reports.tcl`，最后一个参数保持 **10**。该参数是输入时钟周期；使用 5 会同时把 MMCM 派生的核心约束翻倍。具体已运行命令：

```bash
docker exec -w /home/vivadouser/project/ML_KEM_Test/ML-KEM-Testkit vivado-container bash -lc \
  'source /home/vivadouser/Vivado/2024.1/settings64.sh && R=$PWD && mkdir -p build/vivado_core206_debug2 && cd build/vivado_core206_debug2 && vivado -mode batch -nolog -nojournal -source "$R/scripts/vivado_reports.tcl" -tclargs "$R/reports/core206_debug2" 10'
```

第一轮检查点保留在 `reports/core206_debug1/implemented.dcp`，不会用第二轮报告替代其历史结果。
