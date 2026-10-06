# 仿真级 TVLA（pre-silicon 侧信道泄漏评估）

`scripts/tvla_sim.py` 在**没有示波器、没有真实功耗 trace** 的情况下，用 RTL 仿真波形对 ML-KEM-768 核心做 TVLA（Test Vector Leakage Assessment）。它把泄漏定位到具体的时钟周期、模块和微码指令，并对 Decaps 解密中的三个中间运算式 ŝᵀ∘û、(ŝᵀ∘û) mod q、v − (sᵀu mod q) 逐一比较泄漏强弱（第 6 节），生成图和报告。

```bash
uv run python scripts/tvla_sim.py          # Decaps，200 条 trace + 流程自检，约 20 秒
```

结果写到 `sim_result/tvla/<日期_时间>_<运算>/`，打开其中的 `report.md` 即可，图都已嵌在里面。

> 这个核心**没有做掩码（masking）**，是用来做对比的未防护基线。检测到大量泄漏是预期结果。仿真级 TVLA 在这里的作用是：说明未防护实现在哪些阶段、哪些模块泄漏，作为以后加防护或做实测时的对照；同时验证评估流程本身是对的。它不能证明实现是安全的。

---

## 1. 原理

### 1.1 用翻转次数代替功耗
CMOS 电路的动态功耗主要来自信号翻转：一个比特从 0 变 1 或从 1 变 0，都要对负载电容充放电。所以默认使用**汉明距离（HD）模型**：

> 第 c 个周期的"功耗" = 这个周期内所有信号翻转的比特数之和。一个寄存器从旧值 x 变成新值 x′ 时贡献 HD(x, x′) = HW(x ⊕ x′)。

也可以用 `--model hw` 换成**汉明重量（HW）模型**：第 c 个周期的"功耗" = 周期末所有信号中为 1 的比特数。HW 模型常用于软件实现（总线预充电为 0），硬件寄存器一般用 HD。

Verilator 把核心内部所有信号的变化写进 VCD，再逐周期统计翻转比特数，就得到一条"仿真功耗 trace"：每个时钟周期一个采样点。当前 Decaps 一共约 26 300 个点，对应 206 MHz 下约 128 µs。

### 1.2 fixed-vs-random Welch t 检验
把 trace 分成两组：
- **fixed 组**：每次都用同一个输入；
- **random 组**：每次换一个随机输入。

两组各占一半，顺序随机打乱。在每个采样点上做 Welch t 检验，|t| > 4.5 说明该周期的功耗与输入有关，即存在一阶泄漏（单点约 99.999% 置信度）。

### 1.3 为什么要加噪声
仿真是完全确定的：fixed 组的 trace 全都一模一样，方差为 0，t 会变成 0 或无穷大，这不像真实测量。所以每个采样点都加上高斯噪声 N(0, σ²)（`--noise`，单位是"次翻转"，默认 σ = 1）。σ 越大越接近真实示波器的信噪比，要看出同样的泄漏也就需要更多 trace。

噪声很小时要注意：fixed 组的方差只有 σ²，而 random 组的方差几乎全部来自数据，于是 t ≈ √(N/2)·z，z = (x_fixed − μ_random)/σ_data 只取决于这一个固定输入在随机分布中的位置。也就是说，**|t| 能可靠地回答"有没有泄漏"，但不能用来比较"谁泄漏得多"**：任何与数据相关的信号组，平均 |t| 都约为 √(N/2)·0.8（N = 200 时约 8），max|t| 的先后会随固定输入变化。比较强弱要用第 6 节的 σ_data。

### 1.4 多重检验与 4.5 阈值
一条 trace 有约 2.6 万个采样点，每个点都做一次检验。即使完全没有泄漏，纯噪声在所有点上的最大 |t| 也约等于 √(2 ln N) ≈ 4.5，偶尔会超过 4.5。所以判断时要看：
- |t| 是否明显高于这个噪声底（例如 > 7）；
- 或者是否有大段连续周期都超过 4.5，而不是零星几个点。

这也是为什么脚本会先跑一次 fixed-vs-fixed 自检（见 8.1 节）。

---

## 2. 准备

| 依赖 | 用途 | 检查 |
| ---- | ---- | ---- |
| Verilator ≥ 5.0 | 编译仿真模型（需要 `--timing`） | `verilator --version` |
| Go ≥ 1.25 | 计算引擎 `pqc-testkit sca sim`：生成向量、运行仿真、解析 VCD、累加 t 检验 | `go version` |
| uv + Python ≥ 3.9 | 运行脚本；matplotlib / numpy 已在 `pyproject.toml` 中 | `uv sync` |
| GTKWave / Surfer（可选） | 查看保留下来的 VCD 波形 | — |

脚本每次运行都会自动编译 `build/pqc-testkit`，并执行 `make -C testbench/tvla_sim`；RTL 改动后会自动重新编译模型。

---

## 3. 快速开始

```bash
# 正式评估：Decaps，固定合法密文 vs 随机合法密文，200 条 trace，附带自检
uv run python scripts/tvla_sim.py

# 更多 trace、更大噪声（更接近实测条件）
uv run python scripts/tvla_sim.py -n 2000 --noise 4

# random 组用随机字节当密文：走隐式拒绝路径
uv run python scripts/tvla_sim.py --invalid-random

# KeyGen：固定 d‖z vs 随机 d‖z
uv run python scripts/tvla_sim.py --op keygen

# 复现某次结果：固定 fixed 组的密钥和密文（seed 在 report.md 里有记录）
uv run python scripts/tvla_sim.py --seed <128 个十六进制字符>

# 汉明重量模型（默认是汉明距离）
uv run python scripts/tvla_sim.py --model hw

# 只用已有数据重新画图、重写报告，不重新仿真
uv run python scripts/tvla_sim.py --replot sim_result/tvla/20261006_132420_decaps
```

在 14 核的 Mac 上，200 条 Decaps trace 约 7 秒，加上自检和画图一共约 20 秒。耗时与 trace 数成正比，内存占用与 trace 数无关。

---

## 4. 参数

| 参数 | 默认 | 说明 |
| ---- | ---- | ---- |
| `--op` | `decaps` | `decaps` 或 `keygen` |
| `-n, --traces` | 200 | 每次运行的 trace 数，两组各一半；自检也用同样数量 |
| `--noise` | 1.0 | 每个采样点所加高斯噪声的标准差（次翻转） |
| `--noise-seed` | 1 | 噪声随机数种子（自检使用 seed + 1） |
| `--seed` | 随机 | fixed 组的 64 字节种子（十六进制）。Decaps：密钥对和固定密文；KeyGen：d‖z |
| `--invalid-random` | 关 | Decaps：random 组用随机字节当密文（隐式拒绝路径） |
| `--model` | `hd` | 功耗模型：`hd` 汉明距离（每周期翻转的比特数），`hw` 汉明重量（周期末为 1 的比特数） |
| `--keep-vcd` | 1 | 正式评估中每组保留的 VCD 个数（每个约 65 MB），0 = 不保留 |
| `-j, --jobs` | CPU 核数 | 并行仿真数 |
| `--no-sanity` | 关 | 不做 fixed-vs-fixed 自检 |
| `--out` | `sim_result/tvla` | 结果目录 |
| `--replot DIR` | — | 只对已有结果重新分析和画图 |

---

## 5. 流程

```
              ┌────────── 正式评估：fixed vs random ──────────┐
编译模型 ───> │ 生成向量 → Verilator 仿真 → VCD → 每周期翻转数 │ ──┐
              │          (每条 trace 一次)        + 噪声 → t   │   │
              └───────────────────────────────────────────────┘   ├──> 统计、画图、report.md
              ┌────────── 流程自检：fixed vs fixed ────────────┐   │
              │                  同上                          │ ──┘
              └───────────────────────────────────────────────┘
```

### 5.1 输入向量

| `--op` | fixed 组 | random 组 |
| ------ | -------- | --------- |
| `decaps` | 固定 dk + 一个固定的合法密文 | 固定 dk + 每次新做一次 Encaps 得到的合法密文 |
| `decaps --invalid-random` | 同上 | 固定 dk + 随机字节当密文 |
| `keygen` | 固定 d‖z | 每次随机的 d‖z |

两组的私钥相同，只有密文（或 KeyGen 的种子）不同。所以测到的是"运算是否泄漏与输入相关的中间值"。

### 5.2 一次仿真：`testbench/tvla_sim/tb_tvla.sv`
1. 通过 AXI-Lite 把输入写进数据缓冲，再写 OP_MODE。
2. 在写 CTRL.start 前的下降沿开始记录 VCD（`$dumpvars(0, dut)`）。
3. 等 `o_done` 或 `o_error` 后停止记录。

VCD 中的第一个上升沿就是这条 trace 的第 0 个周期，所有 trace 天然对齐。运算期间主机接口空闲（testbench 直接看 `o_done`，不通过 AXI 轮询），所以翻转全部来自核心自身。

### 5.3 VCD → 功耗 trace（Go 引擎，`pkg/sca/vcd.go`）
- 只统计 `tb_tvla.dut` 下的信号；以 `clk` 上升沿划分周期；每次值变化，按新旧值的汉明距离计数。
- 同时记录每个周期微码控制器的 `pc`，用来把周期对应到微码指令（`hdl/core/mlkem_ucode_rom.sv` 中的注释）。
- 按模块分别统计：

| 分组 | 含义 |
| ---- | ---- |
| `total` | 核心内全部信号 |
| `u_alu` | 多项式 ALU：NTT / INTT、BMUL（basemul）、加减 |
| `u_sponge` | Keccak sponge（1600 位状态和轮函数） |
| `u_unpack` | SampleNTT、CBD、ByteDecode / Decompress |
| `u_pack` | Compress、ByteEncode |
| `u_polyram` | 多项式 RAM 的端口（地址、数据、写使能） |
| `u_dbuf` | 8 KB 数据缓冲的端口 |
| `u_ctrl` | 微码控制器 |
| `top` | 顶层模块之间的连线 |
| `u_csr` | AXI 寄存器（运算期间应当没有泄漏，可以作为对照） |

同一根线如果连接两个模块，会同时计入这两个模块，所以各模块之和不等于 `total`。RAM 存储阵列本身不在 VCD 里（Verilator 不记录大数组），但 RAM 端口都在，这正是 BRAM 功耗的主要来源。

除了按模块，还可以按**任意一组信号**统计（引擎参数 `--group 名字=信号,信号,...`，路径相对 `tb_tvla.dut`，末尾 `*` 匹配前缀，例如 `u_alu.p00d*` 匹配 `p00d[0]`…`p00d[5]`）。Decaps 时脚本用它为第 6 节的三个运算式和它们的各组寄存器各建一组。

### 5.4 t 检验（`pkg/sca/accum.go`）
用 Welford 算法逐条累加两组的均值和方差，每条 trace 处理完就丢掉。因此内存与 trace 数无关，可以跑几千条。

---

## 6. 三个运算式的泄漏对比（P1 / P2 / P3，Decaps）

Decaps 的解密是 m′ = Compress₁(v − INTT(ŝᵀ∘û))。我们要回答的是下面三个中间运算式中**哪个泄漏最多**：

| 点 | 运算式 | 含义 | 计算它的微码指令（窗口） |
| -- | ------ | ---- | ------------------------ |
| P1 | ŝᵀ∘û | NTT 域乘积，模 q 约减之前 | 解密的 3 条 `BMUL s7 * s6 -> s3`（i = 0, 1, 2） |
| P2 | (ŝᵀ∘û) mod q | Barrett 约减之后的乘积、基乘组合和累加结果 | 同上 |
| P3 | v − (sᵀu mod q) | 解密的减法（sᵀu 是 INTT 的输出） | `SUB s6 - s3 -> s3`（s6 = v） |

### 6.1 每个点对应哪些寄存器

一个点 = 硬件中**保存该运算式值的全部寄存器**（`scripts/tvla_sim.py` 中的 `POINTS`）。BMUL 用两个 Barrett 乘法器（`mlkem_modmul`，7 级流水，u_mul0 / u_mul1）算 Karatsuba 基乘：

| 点 | 寄存器组 | 信号（相对 `dut`） | 比特 | 保存的值 |
| -- | -------- | ------------------ | ---: | -------- |
| P1 | p1 | `u_alu.u_mul0.p1`, `u_alu.u_mul1.p1` | 48 | DSP 的 M 寄存器，未约减乘积 a·b（< q²，24 bit）。mul0 交替算 â0·b̂0 和 (â0+â1)(b̂0+b̂1)，mul1 交替算 â1·b̂1 和 (â1b̂1 mod q)·γ |
| P1 | p2 | `u_alu.u_mul0.p2`, `u_alu.u_mul1.p2` | 48 | DSP 的 P 寄存器，同一乘积晚一拍 |
| P2 | r | `u_alu.u_mul0.r`, `u_alu.u_mul1.r` | 24 | Barrett 输出 a·b mod q |
| P2 | basemul | `u_alu.p00`, `p11`, `p11d`, `p00d*`, `s1`, `h1`, `h1d*`, `h0` | 204 | 基乘组合（都已模 q）：a0b0、a1b1 及其延迟链，h1 = a0b1 + a1b0，h0 = a0b0 + a1b1γ |
| P2 | acc | `u_alu.wb`, `wbd`, `rb_din` | 72 | 累加结果 Σᵢ ŝᵢ∘ûᵢ mod q 和 RAM 写回数据 |
| P3 | ls | `u_alu.ls` | 24 | 减法结果 v − w（mod q） |
| P3 | rb_din | `u_alu.rb_din` | 24 | RAM 写回数据（ls 的下一拍） |

Barrett 约减的内部寄存器（sa4、sb4、qh5、qq6…）既不是乘积也不是约减结果，不计入任何一个点。要改分组，直接编辑 `POINTS`。

### 6.2 拼接方法

分三个层次：

1. **比特拼接。** 每个多项式 RAM 字是两个 12 bit 系数拼成的 24 bit 字 {f[2w+1], f[2w]}；一个点里的所有寄存器（两个乘法器的同名寄存器、流水线中的延迟副本）也当作一个长比特向量 X_P。HD 对比特可加：HD(X_P) = Σ_寄存器 HD(寄存器)。
2. **周期样本。** 点 P 在第 c 个时钟周期的样本是 L_P(c) = Σ_{r∈P} HD(r(c−1), r(c)) + N(0, σ²)（HW 模型下是 Σ HW(r(c))）。每个周期一个样本，相当于一个只对准这些寄存器的理想探头。
3. **时间拼接。** 同一组寄存器在其他指令里存的是别的值（例如 NTT 时乘法器算 ζ·b），所以只截取真正计算该运算式的周期：P1、P2 取 3 条 BMUL 的周期（每条 281 个），按时间顺序首尾相接成 843 个采样点；P3 取 SUB 的 265 个周期。窗口用每个周期的微码 `pc` 和 ROM 注释自动找（正则 `^BMUL s7 \* s6`、`^SUB `）。核心是常数时间的（每条 Decaps trace 周期数相同），所以窗口在每条 trace 里的位置都一样，不需要对齐；所有统计量都在拼接后的 trace 上逐点计算。

流水线里同一周期的不同寄存器存的是不同系数字的值（BMUL 每 2 个周期读一个字，乘积在读后第 5 拍进入 M 寄存器，结果在第 21 拍写回），所以一个样本是几个字的叠加，这和真实功耗是一致的。

### 6.3 两个指标

- **TVLA（检测）**：拼接 trace 上的 fixed-vs-random Welch t，|t| > 4.5 即判定该点泄漏。
- **σ_data（强度，用于排序）**：random 组每个采样点的标准差扣掉所加噪声，σ_data(c) = √(Var_random(L_P(c)) − σ²)。仿真本身没有噪声，所以 random 组的方差全部来自数据（随机密文的 û、v），也就是 SNR = Var(信号)/Var(噪声) 的分子。在同样的测量噪声下，σ_data 越大，TVLA 越容易检出、CPA 越容易成功（相关系数 ρ = √(SNR/(1+SNR))）。它只用 random 组，与 fixed 组选了哪个输入无关。报告给出窗口内的 RMS 值（√平均方差）和最大值。

为什么不用 max|t| 排序，见 1.3 节：噪声小时，t 只反映这一个固定输入偏离随机均值多远。只有当噪声远大于 σ_data 时，t ≈ √(N/4)·z·σ_data/σ 才与 σ_data 成正比，所以**按 σ_data 排序，就相当于在真实测量噪声下按 TVLA 排序**。

报告还给出 **σ²/bit**（窗口内平均方差 ÷ 比特数）：比特以 1/2 概率独立翻转时约为 0.25。它用来区分两种情况：某个运算式的泄漏强，是因为每个比特更依赖数据，还是因为硬件里存它的寄存器比特更多。

### 6.4 当前结果（HD 模型，200 条 trace）

| 点 | 比特 | σ_data RMS | σ²/bit | TVLA max\|t\| |
| -- | ---: | ---------: | -----: | ------------: |
| P2 (ŝᵀ∘û) mod q | 300 | **8.5** | 0.24 | 30–40 |
| P1 ŝᵀ∘û | 96 | 4.6 | 0.22 | 28–39 |
| P3 v − (sᵀu mod q) | 48 | 3.5 | 0.26 | 24–34 |

- 泄漏最强的是 **P2**，σ_data 约是 P1 的 1.8 倍、P3 的 2.4 倍。用 4 个不同的 fixed 输入各跑一次，σ_data 每次都是 8.5 / 4.6 / 3.5；改用 HW 模型，排序也不变（9.2 / 4.6 / 3.5）。而 max|t| 的先后在 P1、P2 之间来回变（表中给的是范围），这正是不能用它排序的原因。
- 三者的 σ²/bit 都在 0.22–0.26 之间：每个寄存器比特泄漏得差不多，**P2 最强主要是因为它在硬件里占的寄存器最多**（Karatsuba 基乘的中间项和延迟链，以及累加器）。如果只比单个寄存器，P1 的 p1（48 bit）σ_data 为 3.2，P2 的 r、P3 的 ls（都是 24 bit）都为 2.4。
- 三个点都远远超过 4.5：未防护核心在这三处都有一阶泄漏。

### 6.5 图

- **08_points_windows**：解密阶段（约前 6500 个周期）每个点那组寄存器的 t 曲线，阴影是截取的窗口，底部是微码指令色带。阴影以外也有很大的 t（例如 NTT 时乘法器也在工作），这就是要按窗口截取的原因。
- **09_points_t**：拼接后的 t 曲线，点划线是拼接处，× 是 max|t|。
- **10_points_compare**：(a) 各点的 σ_data（排序用）；(b) 各点的 TVLA max|t| 和平均 |t|（检测用，三者的平均 |t| 都约为 8）；(c) 各寄存器组的 σ_data。
- **11_points_power**：拼接 trace 上各点的功耗：黑线是 fixed 组，彩色线和阴影是 random 组的均值 ± σ_data。阴影越宽，泄漏越强。

---

## 7. 输出

`sim_result/tvla/<YYYYmmdd_HHMMSS>_<op>[_invalid][_hw]/`

```
report.md                  报告（结论、参数、表格、图）
analysis.json              报告用到的全部统计数字
figures/
  01_t_total.png           整个核心的 t 曲线 + 微码指令色带
  02_t_per_module.png      各模块的 t 曲线
  03_power_mean.png        两组的平均功耗曲线及其差
  04_leakage_by_instruction.png   按微码指令类别：max|t| 和超阈值周期占比
  05_leakage_by_module.png        按模块：max|t| 和超阈值周期占比
  06_sanity_fixed_vs_fixed.png    自检的 t 曲线
  07_t_distribution.png           t 值分布：自检 vs 正式评估
  08_points_windows.png           Decaps：P1/P2/P3 的寄存器在解密阶段的 t 曲线和截取窗口
  09_points_t.png                 P1/P2/P3 拼接后的 t 曲线
  10_points_compare.png           P1/P2/P3 对比：σ_data、TVLA、各寄存器组
  11_points_power.png             P1/P2/P3 拼接后的功耗（fixed、random 均值 ± σ_data）
raw/main/                  引擎原始输出（正式评估）
  tvla_t.csv               每周期一行：cycle, pc, instruction, t_total, t_<模块>…, t_P1…（Decaps）
  tvla_mean.csv            每周期两组的均值和标准差：total，以及每个 P1… 信号组
  vectors.csv              全部输入：index, class(0 fixed / 1 random), input_hex
  summary.txt, engine.log  引擎的文字报告
  vcd/                     保留的 VCD 波形（.gitignore 已忽略）
raw/sanity/                自检的同样文件
```

### 7.1 各图怎么看
- **01_t_total**：横轴是时钟周期，纵轴是 t。红色虚线是 ±4.5。下方色带标出每个周期正在执行的微码指令类别（NTT、INTT、BMUL、SAMPLE、CBD、DECODE、ENCODE、HABS…），图中还标注了 t 最大的 5 条指令。看哪些阶段大面积超出红线。
- **02_t_per_module**：同样的曲线，按模块拆开。可以看出泄漏是在 ALU、sponge 还是 RAM 端口上；右上角是该模块的 max|t| 和超阈值周期占比。
- **03_power_mean**：上图是两组的平均翻转数，即"功耗形状"，可以清楚地看到 NTT、Keccak 等各阶段；下图是两组之差，差不为零的地方就是 t 大的地方。
- **04_leakage_by_instruction**：按指令类别汇总。左图是 max|t|，右图是该类指令的周期中超过 4.5 的比例。
- **05_leakage_by_module**：按模块汇总。
- **06_sanity_fixed_vs_fixed**：自检的 t 曲线。应当全部落在噪声范围内，灰色虚线是纯噪声的预期最大值。
- **07_t_distribution**：所有周期 t 值的直方图（纵轴取对数）。自检（绿色）应接近标准正态分布；正式评估（蓝色）有很宽的尾巴，这就是泄漏。

---

## 8. 如何解读结果

### 8.1 第一步：看自检
`report.md` 开头给出自检结论，判定为通过的条件是：每个模块的 max|t| < 7，且超阈值周期占比 < 0.1%。
- **通过**：评估流程本身没有问题。fixed-vs-fixed 只会因为噪声产生约 4.5 量级的最大值。
- **不通过**：两组输入完全相同，翻转却不同，说明仿真结果不确定（例如有寄存器没有初始化，不同运行中取值不同），或者 trace 没有对齐（开始记录的时刻不一致）。这时正式评估的结果不可信，要先排查。

### 8.2 第二步：看正式评估
- `total` 的 max|t| 远大于 4.5，超阈值周期占比很高：未防护核心几乎在所有处理秘密数据的阶段都泄漏，这是预期结果。
- 看**哪些指令和模块泄漏最强**（报告中“按微码指令”“各模块”两节的表格，图 04 / 05）。例如 Decaps 中处理 s（DECODE d=12）、NTT / INTT / BMUL、重加密的 SAMPLE / CBD，都与秘密相关。
- `u_csr` 应当基本没有泄漏，可以作为对照组。
- `--invalid-random` 下 random 组走隐式拒绝路径，可以与默认结果对比，看 CMP / CSEL 阶段的差异。
- Decaps 报告的第 1 节回答"三个运算式哪个泄漏最多"：按 σ_data 排序（第 6 节），TVLA 的 |t| 只用来确认每个点都泄漏。

### 8.3 trace 数与噪声
信号强度不变时，t 大约随 √N 增长。噪声 σ 加倍，需要的 trace 数约为 4 倍。想模拟更接近实测的条件，可以把 `--noise` 调大，同时增加 `-n`。

---

## 9. 局限性
- 汉明距离模型只统计逻辑翻转，不包含布线电容的差异、毛刺（glitch）、串扰和电源网络效应。真实芯片上的泄漏只会更多、更复杂。
- 只评估一阶泄漏（均值差）。高阶泄漏需要预处理（例如中心化后相乘），本流程不做。
- 仿真里 fixed 组完全确定，噪声是人为加的。t 值大小不能直接换算成实测需要的 trace 数；噪声小时 |t| 也不适合比较强弱（见 1.3 节），比较用 σ_data。
- 运算式级分析是"只对准这些寄存器的理想探头"：真实测量看到的是整个芯片的功耗，其余电路的翻转对它来说都是噪声。σ_data 也随寄存器比特数增长，比较单个寄存器时看分项表和 σ²/bit。
- 微码指令的对应依据 `pc`。取指和译码的周期也计入当前 pc 对应的指令。

---

## 10. 实测对照

本文是**仿真级**评估。实测（真实示波器 + 板子）的对应流程见 [`hostlab/README.md`](../hostlab/README.md)：同样的 fixed-vs-random 方法和 Welch t 检验，trace 来自示波器而不是 VCD。两者的分工：

| | 仿真（本文） | 实测（hostlab） |
| -- | ------------ | --------------- |
| 功耗来源 | VCD 的汉明距离 | 示波器采到的真实波形 |
| 定位精度 | 精确到时钟周期、模块、微码指令、单个寄存器 | 受采样率和探头限制 |
| 噪声 | 人为加入，可控 | 真实的测量噪声、布线、毛刺、耦合 |
| 速度 | 200 条 trace 约 20 秒 | 受 UART 限制，约 0.4 秒/条 |
| 用途 | 定位泄漏来自哪个运算、哪组寄存器 | 确认防护在真实硬件上有效，量化 traces-to-disclosure |

做防护时先用仿真定位（例如本文第 6 节指出 P2 泄漏最强，因为保存它的寄存器最多），改完 RTL 后用实测确认。

---

## 11. 常见问题

| 现象 | 处理 |
| ---- | ---- |
| `go build failed` | 确认 Go ≥ 1.25（`go version`）；在仓库根目录运行 |
| `Verilator build failed` | 确认 Verilator ≥ 5.0 并支持 `--timing`；看报错里的 RTL 语法问题 |
| `matplotlib / numpy missing` | 用 `uv run python scripts/tvla_sim.py`，或先 `uv sync` |
| 磁盘占用大 | 每个保留的 VCD 约 65 MB。用 `--keep-vcd 0` 不保留；VCD 已被 `.gitignore` 忽略 |
| 报告里指令名为空 | 微码 ROM 的格式变了，引擎按 `hdl/core/mlkem_ucode_rom.sv` 中 `ADDR_W'(n): rom_q <= ...; // 注释` 解析 |
| 想直接调用引擎 | `build/pqc-testkit sca sim --help`；也可以手动跑一次仿真：`testbench/tvla_sim/obj_dir/Vtb_tvla +in=in.mem +len=3488 +op=2 +vcd=one.vcd` |
