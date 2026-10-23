# 简单 CPA 速查：做什么、两个假设、结果怎么读

对应代码：`cpa_toolkit/cpa.py`（引擎 + 命令行）、`cpa_toolkit/web_gui.py` / `web_static/`（Analysis 页）、`tests/test_cpa.py`。

> **2026-10-20 变更**：旧的泄漏模型目录（`cpa_toolkit/models/` 的 hw / hd / pipe 全部模型）、逐周期对齐的分析（`aligned_cpa.py`、`cycle_features.py`）和对应测试已经移除，换成下面的最简配方。旧代码在 git 历史里（`git log -- cpa_toolkit/models`）。`MODEL.md` 第 5 节和本文件旧版（流水线模型）描述的是被移除的模型，只作为记录保留；`mlkem_pipeline.py`（流水线的软件孪生）和 `scripts/sim/pipeline_check.py` 仍然保留，因为它们是对 RTL 的独立核对。

---

## 1. 做了什么（四步）

对每一个要分析的秘密系数 `ŝ[poly][j]`，**每个系数单独**做：

1. 从已知密文算出公开系数 `u = û[poly][j]`（每条 trace 一个）；
2. 对 3329 个候选 `g`（0…3328）算假设值，例如 `HW(u·g mod q)` —— 得到一个 `trace 数 × 3329` 的矩阵；
3. 和 BMUL 窗口里**每一个采样点**算 Pearson 相关系数 ρ（一次矩阵乘法），得到 `3329 × 采样点数`；
4. 每个候选取它在整个窗口里最大的 |ρ|；最大的那个候选就是猜测，峰值所在的采样点就是 POI。

然后做两件“人能看懂”的事：

- **top-10 + 稳定性投票**：在 POI 周围 3、5、7、9 个采样点的窗口里，各自累加 ρ² 取前 10，数每个候选出现在几个窗口里（4/4 最稳）。一个正确的密钥不应该只靠一个偶然的采样点；
- **和 RTL 预测的位置比较**：硬件里这个寄存器在哪个核心周期持有这个乘积是已知的（第 2 节），所以每个峰都能和“应该在的位置”比较（*offset*）。

真实密钥（`--dk-ref` / “Rank the campaign key”）**只用于事后给猜测排名**，从不参与构造假设：假设只由公开的 `u` 和候选 `g` 算出，所以分析是盲的。

---

## 2. 两个假设

乘法器的操作数在 `T + 3` 到达（`T` = 该系数所在字的 RAM 读地址周期，`T = BMUL_READ_CYCLES[poly] + 2·(j // 2)`，每个字晚 2 个周期；见 `mlkem_poly_alu.sv`）。

| 假设（`--model`） | 公式 | 寄存器 | 周期 |
| ---- | ---- | ---- | ---- |
| `hw_product_mod`（默认） | `HW(u·g mod q)`，12 位 | Barrett 输出 `r`，再被 `p00` / `p11` 复制一拍 | T + 10 / T + 11 |
| `hw_product_raw` | `HW(u·g)`，24 位 | DSP 乘积寄存器（`mlkem_modmul.sv` 的 `p1` / `p2`） | T + 5 / T + 6 |

- 一对系数（2w，2w+1）在**同一个周期**由两个乘法器算完，所以搭档系数对另一个来说是算法噪声（和信号一样大），需要上千条 trace；
- `u[j] = 0` 的 trace 对该系数没有信息；整个 campaign 里 `u[j]` 都不变时这个系数没有信息，报告会标出来；
- 没有 R_prev、没有部分和：为了保持最简单，不建模“寄存器之前是什么”（旧的 `hd_*` 模型在周期精确仿真里和真实翻转的相关性只有约 0.01）。想要更强的假设，在 `cpa.py` 的 `MODELS` 里加一个 `Model(...)`。

---

## 3. 怎么跑

GUI：Analysis → *New analysis*：1 选假设；2 选多项式（`0-2`）和每个多项式的前几个系数（`8`，`0` = 全部 256，`0-15` / `3,7` = 指定）、在哪里找（**整个 BMUL 窗口**，或**预测周期 ± N**，可加 shift）；3 盲分析还是用 campaign 的密钥给猜测排名；4 运行。

命令行：

```bash
cd cpa_toolkit
uv run python cpa.py FILE.hdf5                              # 盲分析，每个多项式的前 8 个系数
uv run python cpa.py FILE.hdf5 --dk-ref dk.bin              # + 真实密钥的排名
uv run python cpa.py FILE.hdf5 --search gate --margin 10 --delay 3
uv run python cpa.py FILE.hdf5 --coeffs 0 --out report.json --csv r.csv   # 全部 768 个
```

建议顺序：先 **整个窗口 + 8 个系数**（几秒），看 *offset* 列；如果峰堆在同一个偏移上，把它填进 *shift*，改用**预测周期 ± 10**，这时可以放心跑全部 256 个系数（快得多，假峰也少）。

---

## 4. 结果怎么读

**Overview** 的表，一行一个系数：

| 列 | 含义 | 好的样子 |
| ---- | ---- | ---- |
| best | \|ρ\| 最大的候选 | 盲分析里这就是猜测 |
| peak \|ρ\| | 它的最大 \|ρ\| | 明显高于噪声底 |
| ×floor | 峰值 ÷ 噪声底（纯噪声在 3329 个候选 × 窗口内至少能碰到一次的 \|ρ\| ≈ √(2 ln(试验数)/trace 数)） | > 1，越大越好；≤ 1 没有意义 |
| lead over #2 | 比第二名高多少 | 0.02 以上才像“赢家” |
| peak time | 峰在触发之后多少 µs | 在预测位置附近 |
| offset | 峰位置 − 预测周期，核心周期 | **所有系数差不多同一个值** |
| votes | 在 4 个窗口里有几个把它排进前 10 | 4/4 |
| true / true rank / ✓ | 打分模式：真实值、它在 3329 个候选里排第几 | rank 1 |

两张图：**“峰值是真的吗”**（每个系数的最佳峰 vs 虚线噪声底；打分模式下绿 = 恢复、红 = 没恢复，白菱形 = 真实密钥自己的峰）和 **“峰值落在哪”**（offset 直方图：真实泄漏堆在一个值上，噪声散开）。页面顶部的一句话结论会用同样的标准说明：没有信号 / 有信号但没对齐 / 对齐且恢复了多少。

**Coefficient**（点表里的一行，← → ↑ ↓ 切换）：

1. **相关系数随时间**：最佳候选（蓝）和真实密钥（绿，若不同）的 ρ 曲线；黄色带 = RTL 预测的周期，虚线 = 噪声能到的 ±ρ。**一个对的结果**：在黄色带里或附近有一个尖锐的峰，远高于虚线；
2. **哪个候选赢了**：3329 个候选各自的峰值（灰点），赢家（蓝）和真实密钥（绿菱形）。赢家应该远离灰色的云；
3. **随 trace 数的收敛**：真实密钥（盲分析为最佳候选）的峰 vs 最强的错误候选 vs 噪声底。对的候选会爬到灰线（最强的错误候选）之上并留在那里；
4. **top-10 与稳定性**：表；
5. **候选 × 时间热图**：前 24 个候选（和真实密钥）每个一行，亮 = \|ρ\| 高。对的密钥是某一时刻的一条亮线。

---

## 5. 判断标准、常见问题

| 现象 | 说明 / 处理 |
| ---- | ---- |
| “No signal”：没有峰高过噪声底 | 加 trace（噪声底按 1/√N 下降）、换另一个假设、用 TVLA 看泄漏是不是落在 BMUL 窗口里、用 *Check trigger* 看记录是否从触发开始 |
| 峰很多但 offset 散得很开 | 是噪声里挑出来的最大值：整个窗口 × 3329 个候选，总会有一个“最高”。看 ×floor 和 votes，不要只看排名 |
| offset 全是同一个非零值 | 探头 / 放大器延迟（或预测的周期有 1–2 个周期的出入）：填进 *shift*，用预测周期搜索 |
| 打分模式 rank-1 很低但盲分析的峰高于噪声底 | 峰是真的但属于别的东西（例如搭档系数）：看 Coefficient 的第 2 张图，赢家是不是明显离开云 |
| offset 恰好是一个固定的大数（如 +206） | 旧 campaign 没有记录 record start，被当成“采样 0 = 触发”：重新采集，或在示波器上把触发位置拉到最左边 |
| 一个都恢复不了，但合成数据测试通过 | 这不是工具的错，说明这块板子、这个探头位置在这个 trace 数下泄漏太弱 —— **不等于安全**（`cpa_toolkit/README.md` 第 7 节） |

**时间轴**：示波器报告的“第一个采样点相对触发的时间”（LeCroy 的 `HORIZ_OFFSET`）现在随每条 campaign 一起存（`record_start_s`），BMUL 窗口按**时间**放在 trace 上，所以触发位置在屏幕中间（有预触发数据）也不会错位。窗口两侧的余量 `TRIGGER_SLACK_CYCLES` 的单位是**核心周期**（旧版把它当成采样点，2.5 GS/s 下只有预期的 1/12）；窗口落在记录之外时，剩下的窗口保持自己的编号（旧版会把 BMUL 1 标成 “BMUL 0”）。

---

## 6. 和参考仓库的对照（哪些文件用到了、哪些没有）

| 仓库 | 文件 | 内容 | 在这个工具里 |
| ---- | ---- | ---- | ---- |
| `anshupriyan/My-CPA-code-analysis`（MIT） | `Source_code.py` | 对系数遍历 0…3328，`HW((k·r) mod q)`，对每个采样点求 Pearson；找 POI；围绕 POI 取 4 个窗口，每个窗口累加 ρ²，取前 10，数“k/4 个窗口”的稳定性 | **采用**：假设、逐采样点相关、POI、top-10、4 窗口稳定性投票（用向量化的矩阵乘法重写，原代码对每个 (候选, 采样点) 调一次 `pearsonr`） |
| | `trace_window_for_all.py` | 向量化矩阵 CPA（`t_centered.T @ h_centered`），用 FFT 对齐 trace，按 500 条 trace 的块滑动扫描并保留相关系数最高的块 | **部分采用**：矩阵形式的相关系数。**没有采用**：FFT 对齐（trace 由 `trig_out` 触发，已对齐）；“取相关系数最高的 trace 块”（在很多块里挑最大值会把噪声峰当成结果） |
| | `Output_log.txt` | 输出格式：`Peak t / corr`、`Key N: k/4 windows` | **采用**为 Overview 表的列（peak、votes） |
| | `README.md`（`R_INV = 2704`） | 目标实现是 Montgomery 域 | 不用：本核心是 Barrett，因子为 1 |
| `Chaosnap/CorrelationPowerAnalysisCorrelationPowerAnalysis`（你自己的） | `CPA.py` | `Correlate`（向量化 Pearson）、POI 曲线、top-N 候选、窗口半径 1…4 的稳定性、HW / HD 两个模型 | **采用**：同样的流程（HW；HD 需要“前一个寄存器值”，这颗核心上没有可靠的对应物，没有采用） |
| | `ToolBox.py` | HW / HD 查表、FFT 对齐 | 采用 HW 查表；不用 FFT 对齐 |
| | `Main.py` | 命令行 + 画 POI 曲线 / 候选相关系数 | **采用**为 `cpa.py` 的命令行和 GUI 的 Coefficient 页（相关系数随时间、候选排名） |
| `PRASANNA-RAVI/Generic-SCA-CCA-Lattice-Schemes` | Kyber512 的 MATLAB 脚本 | 选择密文方法：用 t 检验在 m=0 / m=1 两类 trace 之间找兴趣点，再用聚类 / 简化模板判断该位；trace 是 ARM 板的 `.mat` | **不能直接用**（MATLAB、ARM 板的 trace、不是 CPA）。思路已有对应物：t 检验 = 本工具的 TVLA；“用密文让一个系数决定一位”= `pc_oracle.py`（明文检查预言机，`testvectors --strategy pc-oracle`） |
| | `Get_ciphertext_scripts/` | 生成选择密文的 C 代码 | 不用（`pc_oracle.py` 已经是 Python 的对应实现） |
| `Chaman-veteran/KyberCPA` | `statistical_analysis.py`、`get_traces.py`、`interface.py` | pqm4 Cortex-M4 固件、ChipWhisperer 采集；假设是 ARM SIMD 指令（`smultt` 等）的结果，一次猜 16 位密钥（2¹⁶ 个候选），先猜 k2k3 再猜 k0k1；用阈值 `max_pearson_corr` 先筛候选 | **不能直接用**（假设建立在 ARM 指令上）。采集 / 主机通信部分已被 `acquire_cpa.py` + `uart_target.py` 覆盖 |

许可：`anshupriyan` 的仓库是 MIT；`PRASANNA-RAVI` 和 `Chaman-veteran` 的仓库没有许可文件，所以没有复制其中的任何代码，只参考了公开的思路。`cpa.py` 是自己写的实现。
