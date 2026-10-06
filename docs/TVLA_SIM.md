# 仿真级 TVLA（pre-silicon 侧信道泄漏评估）

`scripts/tvla_sim.py` 在**没有示波器、没有真实功耗 trace** 的情况下，用 RTL 仿真波形对 ML-KEM-768 核心做 TVLA（Test Vector Leakage Assessment）。它把泄漏定位到具体的时钟周期、模块和微码指令，生成图和报告。

```bash
uv run python scripts/tvla_sim.py          # Decaps，200 条 trace + 流程自检，约 20 秒
```

结果写到 `sim_result/tvla/<日期_时间>_<运算>/`，打开其中的 `report.md` 即可，图都已嵌在里面。

> 这个核心**没有做掩码（masking）**，是用来做对比的未防护基线。检测到大量泄漏是预期结果。仿真级 TVLA 在这里的作用是：说明未防护实现在哪些阶段、哪些模块泄漏，作为以后加防护或做实测时的对照；同时验证评估流程本身是对的。它不能证明实现是安全的。

---

## 1. 原理

### 1.1 用翻转次数代替功耗
CMOS 电路的动态功耗主要来自信号翻转：一个比特从 0 变 1 或从 1 变 0，都要对负载电容充放电。所以常用**汉明距离（HD）模型**：

> 第 c 个周期的"功耗" = 这个周期内所有信号翻转的比特数之和

Verilator 把核心内部所有信号的变化写进 VCD，再逐周期统计翻转比特数，就得到一条"仿真功耗 trace"：每个时钟周期一个采样点。当前 Decaps 一共约 26 300 个点，对应 206 MHz 下约 128 µs。

### 1.2 fixed-vs-random Welch t 检验
把 trace 分成两组：
- **fixed 组**：每次都用同一个输入；
- **random 组**：每次换一个随机输入。

两组各占一半，顺序随机打乱。在每个采样点上做 Welch t 检验，|t| > 4.5 说明该周期的功耗与输入有关，即存在一阶泄漏（单点约 99.999% 置信度）。

### 1.3 为什么要加噪声
仿真是完全确定的：fixed 组的 trace 全都一模一样，方差为 0，t 会变成 0 或无穷大，这不像真实测量。所以每个采样点都加上高斯噪声 N(0, σ²)（`--noise`，单位是"次翻转"，默认 σ = 1）。σ 越大越接近真实示波器的信噪比，要看出同样的泄漏也就需要更多 trace。

### 1.4 多重检验与 4.5 阈值
一条 trace 有约 2.6 万个采样点，每个点都做一次检验。即使完全没有泄漏，纯噪声在所有点上的最大 |t| 也约等于 √(2 ln N) ≈ 4.5，偶尔会超过 4.5。所以判断时要看：
- |t| 是否明显高于这个噪声底（例如 > 7）；
- 或者是否有大段连续周期都超过 4.5，而不是零星几个点。

这也是为什么脚本会先跑一次 fixed-vs-fixed 自检（第 5 节）。

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

### 5.4 t 检验（`pkg/sca/accum.go`）
用 Welford 算法逐条累加两组的均值和方差，每条 trace 处理完就丢掉。因此内存与 trace 数无关，可以跑几千条。

---

## 6. 输出

`sim_result/tvla/<YYYYmmdd_HHMMSS>_<op>[_invalid]/`

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
raw/main/                  引擎原始输出（正式评估）
  tvla_t.csv               每周期一行：cycle, pc, instruction, t_total, t_<模块>…
  tvla_mean.csv            每周期两组的平均翻转数
  vectors.csv              全部输入：index, class(0 fixed / 1 random), input_hex
  summary.txt, engine.log  引擎的文字报告
  vcd/                     保留的 VCD 波形（.gitignore 已忽略）
raw/sanity/                自检的同样文件
```

### 6.1 各图怎么看
- **01_t_total**：横轴是时钟周期，纵轴是 t。红色虚线是 ±4.5。下方色带标出每个周期正在执行的微码指令类别（NTT、INTT、BMUL、SAMPLE、CBD、DECODE、ENCODE、HABS…），图中还标注了 t 最大的 5 条指令。看哪些阶段大面积超出红线。
- **02_t_per_module**：同样的曲线，按模块拆开。可以看出泄漏是在 ALU、sponge 还是 RAM 端口上；右上角是该模块的 max|t| 和超阈值周期占比。
- **03_power_mean**：上图是两组的平均翻转数，即"功耗形状"，可以清楚地看到 NTT、Keccak 等各阶段；下图是两组之差，差不为零的地方就是 t 大的地方。
- **04_leakage_by_instruction**：按指令类别汇总。左图是 max|t|，右图是该类指令的周期中超过 4.5 的比例。
- **05_leakage_by_module**：按模块汇总。
- **06_sanity_fixed_vs_fixed**：自检的 t 曲线。应当全部落在噪声范围内，灰色虚线是纯噪声的预期最大值。
- **07_t_distribution**：所有周期 t 值的直方图（纵轴取对数）。自检（绿色）应接近标准正态分布；正式评估（蓝色）有很宽的尾巴，这就是泄漏。

---

## 7. 如何解读结果

### 7.1 第一步：看自检
`report.md` 开头给出自检结论，判定为通过的条件是：每个模块的 max|t| < 7，且超阈值周期占比 < 0.1%。
- **通过**：评估流程本身没有问题。fixed-vs-fixed 只会因为噪声产生约 4.5 量级的最大值。
- **不通过**：两组输入完全相同，翻转却不同，说明仿真结果不确定（例如有寄存器没有初始化，不同运行中取值不同），或者 trace 没有对齐（开始记录的时刻不一致）。这时正式评估的结果不可信，要先排查。

### 7.2 第二步：看正式评估
- `total` 的 max|t| 远大于 4.5，超阈值周期占比很高：未防护核心几乎在所有处理秘密数据的阶段都泄漏，这是预期结果。
- 看**哪些指令和模块泄漏最强**（第 3 节表格和图 04 / 05）。例如 Decaps 中处理 s（DECODE d=12）、NTT / INTT / BMUL、重加密的 SAMPLE / CBD，都与秘密相关。
- `u_csr` 应当基本没有泄漏，可以作为对照组。
- `--invalid-random` 下 random 组走隐式拒绝路径，可以与默认结果对比，看 CMP / CSEL 阶段的差异。

### 7.3 trace 数与噪声
信号强度不变时，t 大约随 √N 增长。噪声 σ 加倍，需要的 trace 数约为 4 倍。想模拟更接近实测的条件，可以把 `--noise` 调大，同时增加 `-n`。

---

## 8. 局限性
- 汉明距离模型只统计逻辑翻转，不包含布线电容的差异、毛刺（glitch）、串扰和电源网络效应。真实芯片上的泄漏只会更多、更复杂。
- 只评估一阶泄漏（均值差）。高阶泄漏需要预处理（例如中心化后相乘），本流程不做。
- 仿真里 fixed 组完全确定，噪声是人为加的。这里的 t 值大小用于相对比较（不同模块、不同指令、不同版本之间），不能直接换算成实测需要的 trace 数。
- 微码指令的对应依据 `pc`。取指和译码的周期也计入当前 pc 对应的指令。

---

## 9. 常见问题

| 现象 | 处理 |
| ---- | ---- |
| `go build failed` | 确认 Go ≥ 1.25（`go version`）；在仓库根目录运行 |
| `Verilator build failed` | 确认 Verilator ≥ 5.0 并支持 `--timing`；看报错里的 RTL 语法问题 |
| `matplotlib / numpy missing` | 用 `uv run python scripts/tvla_sim.py`，或先 `uv sync` |
| 磁盘占用大 | 每个保留的 VCD 约 65 MB。用 `--keep-vcd 0` 不保留；VCD 已被 `.gitignore` 忽略 |
| 报告里指令名为空 | 微码 ROM 的格式变了，引擎按 `hdl/core/mlkem_ucode_rom.sv` 中 `ADDR_W'(n): rom_q <= ...; // 注释` 解析 |
| 想直接调用引擎 | `build/pqc-testkit sca sim --help`；也可以手动跑一次仿真：`testbench/tvla_sim/obj_dir/Vtb_tvla +in=in.mem +len=3488 +op=2 +vcd=one.vcd` |
