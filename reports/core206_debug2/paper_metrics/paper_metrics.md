# ML-KEM-768 第二轮：论文参数汇总

提取日期：2026-10-03。所有资源与时序来自第二轮 `implemented.dcp`，RTL/约束 SHA-256 与该轮记录一致。此次只读取检查点、生成报告和计算指标，没有修改 RTL 或重新布局布线，没有修改 Docker image。

资源统计范围为 **u_mlkem 核心**。周期由该版本 Verilator 仿真中的 CYCLE_COUNT 取得；延迟和吞吐率统一按已经通过实现验证的 **206 MHz** 换算。

| Metric | 数值 | 口径 / 依据 |
| --- | --- | --- |
| FPGA | Artix-7 XC7A100TCSG324-1 | Vivado 2024.1，第二轮已布线检查点 |
| Standard | FIPS 203 ML-KEM-768 | 算法实现；无 masking / shuffling 等功耗侧信道防护 |
| LUT | 7248 | u_mlkem；7110 logic + 44 LUTRAM + 94 SRL |
| FF | 4623 | u_mlkem |
| Slices | 2387 | 1632 SLICEL + 755 SLICEM |
| CLBs | 1307 | 核心占用的唯一物理 CLB tile 数，不是 Slice 数除以 2 |
| BRAM | 3 × RAMB36E1 | 108 Kibit；无 RAMB18 |
| DSP | 2 × DSP48E1 | u_mlkem |
| Fclk（已验证） | 206 MHz | UART 保持 100 MHz |
| Fmax（post-route STA 推算） | ≈210.04 MHz | 1000/(4.854−0.093)；不是 210 MHz 新实现或板上频率扫描的结果 |
| WNS / WHS / WPWS | +0.093 / +0.036 / +1.177 ns | 建立、保持、脉宽失败端点均为 0 |
| KeyGen cycles | 16,653.08 | ACVP 25 个向量均值；实测范围 16,631–16,703 cycles |
| Encaps cycles | 19,245.48 | ACVP 25 个向量均值；实测范围 19,205–19,321 cycles |
| Decaps cycles | 26,304.30 | ACVP 10 个向量均值；实测范围 26,270–26,363 cycles |
| KeyGen latency | 80.840 μs | 平均核心周期 / 206 MHz，不含 UART/主机传输 |
| Encaps latency | 93.425 μs | 平均核心周期 / 206 MHz，不含 UART/主机传输 |
| Decaps latency | 127.691 μs | 平均核心周期 / 206 MHz，不含 UART/主机传输 |
| KeyGen throughput | 12,370.08 ops/s | 206 MHz / 平均核心周期；理论核心处理率，不是串口端到端吞吐率 |
| Encaps throughput | 10,703.81 ops/s | 206 MHz / 平均核心周期；理论核心处理率，不是串口端到端吞吐率 |
| Decaps throughput | 7,831.42 ops/s | 206 MHz / 平均核心周期；理论核心处理率，不是串口端到端吞吐率 |
| Power：核心动态 | ≈0.289 W | Vivado 无向量活动估算；归入 u_mlkem 的动态功耗 |
| Power：整片总功耗 | ≈0.526 W | 包含 UART/CDC/MMCM；dynamic 0.428 W、static 0.099 W（分别取整） |
| KeyGen energy/op | ≈23.36 μJ（核心动态）；≈42.52 μJ（整片） | 粗估：统一的无向量功耗 × 操作平均耗时，非逐操作活动仿真或实测 |
| Encaps energy/op | ≈27.00 μJ（核心动态）；≈49.14 μJ（整片） | 粗估：统一的无向量功耗 × 操作平均耗时，非逐操作活动仿真或实测 |
| Decaps energy/op | ≈36.90 μJ（核心动态）；≈67.17 μJ（整片） | 粗估：统一的无向量功耗 × 操作平均耗时，非逐操作活动仿真或实测 |
| Correctness | ACVP 60/60 | KeyGen 25/25、Encaps 25/25、Decaps 10/10；另有 20 项检查及隐式拒绝边界 16/16 通过 |

## 论文使用说明

- **频率**：可写“post-route STA estimated Fmax ≈ 210.0 MHz; validated implementation clock = 206 MHz”。公式 `Fmax_est = 1000 / (Tclk − WNS)` 使用报告的 4.854 ns 与 0.093 ns；数字受报告精度限制。它是对已布线设计的时序推算，没有更改 MMCM、重新以 210 MHz 实现或在板上扫频。因此本表没有使用 210.04 MHz 计算运行延迟。
- **周期**：表内小数是不同 ACVP 向量整数周期的均值。SampleNTT 拒绝采样使周期随数据变化；范围仅覆盖该批向量，不是全输入空间的最坏情况保证。KeyGen/Encaps/Decaps 分别有 25/25/10 个向量。
- **Slice / CLB**：按核心原语所在的物理 site/tile 去重计数。CLB tile 可能与外围逻辑共享，不代表全部独占；不得将 2387 个已占用 Slice 简单除以 2 来声称占用 CLB 数。论文跨器件比较时优先同时报告 LUT/FF/Slice。
- **吞吐率**：使用 `Fclk / mean_cycles`（ops/s），不含 UART 传输、主机命令和任务之间的额外间隔；没有假设多个 ML-KEM 操作重叠执行。
- **功耗**：`report_power` 对已布线网表进行 vectorless 活动传播，置信度 Medium，无 SAIF/VCD。环境使用工具报告的 typical process、25°C ambient、250 LFM、medium heatsink / board、12–15 层板等默认条件，并非对实际开发板散热条件的测量。核心 0.289 W 是层级归属的动态功耗，不含全芯片静态及归属顶层的 MMCM 等；0.526 W 为整片总估算，不能标成仅 ML-KEM 核心功耗。
- **能耗**：`Energy/op = Power × cycles/Fclk`。当前两组值分别用 0.289 W 和 0.526 W 计算，只是同一 vectorless 功耗假设下的粗估，不能当作 KeyGen/Encaps/Decaps 各自的实测能耗。若论文需要比较算法能效，需进一步用各操作活动文件估算或板上采集计算；本次未进行该测量。
- **正确性**：使用本地 ACVP 向量验证，不等同于官方认证。代码保留隐式拒绝与必要输入检查。

## 可追溯数据

- [核心资源报告](../utilization_u_mlkem.rpt)
- [最终时序汇总](../timing_summary.rpt)
- [关键路径报告](core_timing.rpt)
- [物理 Slice/CLB 和 Fmax 提取值](physical_metrics.txt)
- [Slice site 清单](core_slice_sites.txt) / [CLB tile 清单](core_clb_tiles.txt)
- [功耗原始报告](power_vectorless.rpt)
- [逐操作周期统计 JSON](../rtl_validation/profile.json)
- [功能验证日志](../rtl_validation/validation.txt)
- [提取 Tcl](extract.tcl) / [提取日志](extract.log)

本表 CSV 同名保存为 `paper_metrics.csv`，机器可读数值保存为 `paper_metrics.json`。
