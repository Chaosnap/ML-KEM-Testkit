# 仿真级 TVLA 指南（pre-silicon leakage assessment）

本文说明如何在**没有示波器、没有采集真实功耗 trace** 的情况下，用 RTL 仿真波形（VCD）对 ML-KEM-768 核心做 TVLA（Test Vector Leakage Assessment），并把泄漏定位到具体的时钟周期、具体模块和具体的微码指令。

整个流程只需要一条命令，200 条 Decaps trace 在笔记本上大约 10 秒跑完。

---

## 1. 原理

### 1.1 用翻转次数代替功耗
CMOS 电路的动态功耗主要来自信号翻转：一个比特从 0 变 1 或从 1 变 0，就要对负载电容充放电。所以最常用的功耗模型是**汉明距离（Hamming distance, HD）模型**：

> 第 c 个周期的"功耗" = 这个周期内所有信号翻转的比特数之和

仿真时让 Verilator 把核心内部所有信号的变化写进 VCD 文件，然后逐周期统计翻转比特数，就得到一条"合成功耗 trace"：一个周期一个采样点，Decaps 一共约 110 564 个点。

### 1.2 fixed-vs-random t 检验
TVLA 的做法是把输入分成两组：
- **fixed 组**：每次都用同一个输入；
- **random 组**：每次换一个随机输入。

在每个采样点上，对两组 trace 做 Welch t 检验。如果某个点 |t| > 4.5，就说明这个周期的功耗和输入有关，也就是存在一阶泄漏。4.5 对应约 99.999% 的置信度。

### 1.3 为什么要加噪声
仿真是完全确定的：fixed 组 100 条 trace 一模一样，方差为 0。
- 如果 random 组在某个点也恰好恒定且和 fixed 组不同，t 就是无穷大；
- 如果两组都恒定且相同，t 就是 0。

这两种情况都不像真实测量。所以每个采样点要加一个高斯噪声 N(0, σ²)（`--noise`，单位是"翻转次数"，默认 σ = 1）。σ 越大越接近真实示波器的信噪比，要检测出同样的泄漏也就需要更多的 trace。

### 1.4 结果该怎么看
**核心没有做掩码（masking），所以检测到大量泄漏是预期的。** 仿真级 TVLA 在这里的意义是：
- 说明"未防护的基线实现在哪些阶段、哪些模块泄漏"，作为以后加防护的对照；
- 验证评估流程本身是正确的（见第 5 节的 sanity check）。

它**不能**证明实现是安全的。HD 模型也忽略了布线电容、毛刺（glitch）和耦合等物理效应，真实芯片上的泄漏只会更多，不会更少。

---

## 2. 准备

| 依赖 | 用途 | 检查命令 |
|---|---|---|
| Verilator ≥ 5.0 | 编译仿真模型（需要 `--timing` 支持） | `verilator --version` |
| Go 1.25 | `pqc-testkit` | `go version` |
| matplotlib（可选） | 画 t 曲线 | `python3 -c "import matplotlib"` |
| GTKWave 或 Surfer（可选） | 查看 VCD 波形 | — |

```bash
cd ML-KEM-Testkit
go build -o pqc-testkit ./cmd/pqc-testkit
make -C testbench/tvla_sim          # 编译 Verilator 模型，生成 testbench/tvla_sim/obj_dir/Vtb_tvla
```

RTL 改动后需要重新执行 `make -C testbench/tvla_sim`，Makefile 会根据 `hdl/core/*.sv` 的修改时间自动重新编译。

---

## 3. 快速开始

```bash
# 1. 正式评估：Decaps，固定密文 vs 随机合法密文，200 条 trace
./pqc-testkit sca sim --op decaps -n 200 -o build/tvla_decaps

# 2. 流程自检：两组输入完全相同，不应报告泄漏
./pqc-testkit sca sim --op decaps -n 200 --fixed-vs-fixed --keep-vcd 0 -o build/tvla_sanity

# 3. 画图（可选）
python3 scripts/plot_tvla.py build/tvla_decaps --groups total u_alu u_sponge u_ctrl
```

---

## 4. 流程细节

`pqc-testkit sca sim` 对每条 trace 依次做下面几步，多条 trace 并行（`-j`，默认等于 CPU 核数）：

```
生成向量 ──> 写输入文件 ──> Verilator 仿真 ──> VCD ──> 逐周期统计翻转 ──> 加噪声 ──> 累加到 t 检验
(sca gen 同一套)  in_XXXX.mem     Vtb_tvla          (约 43 MB)  总体 + 各模块        σ = --noise   逐点 Welch t
```

### 4.1 输入向量
与 `pqc-testkit sca gen` 使用同一个生成器（`pkg/sca/mlkem.go`）：

| `--op` | fixed 组 | random 组 |
|---|---|---|
| `decaps`（默认） | dk 固定 + 一个固定的合法密文 | dk 固定 + 每次新做一次 Encaps 得到的合法密文 |
| `decaps --invalid-random` | 同上 | dk 固定 + 随机字节当密文（走隐式拒绝路径） |
| `keygen` | 固定的 d‖z | 每次随机的 d‖z |

- 两组各占一半，顺序用 crypto/rand 随机打乱。
- `--seed <128 个十六进制字符>` 可以固定整个 fixed 组（密钥和固定密文），便于复现；报告开头会打印本次用的 seed。

### 4.2 testbench：`testbench/tvla_sim/tb_tvla.sv`
每条 trace 单独运行一次仿真：
1. 通过 AXI-Lite 把输入写进 data buffer，然后写 OP_MODE；SEC_LEVEL 复位值就是 768。
2. 在写 `CTRL.start` 之前的那个下降沿，执行 `$dumpvars(0, dut)` 开始记录。
3. 等待 `o_done` 或 `o_error`，然后 `$dumpoff` 停止记录。

这样设计有两个目的：
- VCD 里第一个时钟上升沿就是这条 trace 的第 0 个周期，所有 trace 天然对齐；
- 运算期间主机接口完全空闲。testbench 不通过 AXI 轮询 STATUS，而是直接看 `o_done`，所以波形里的翻转全部来自核心自己。

也可以不经过 `pqc-testkit`，直接手动运行一次：
```bash
testbench/tvla_sim/obj_dir/Vtb_tvla +in=in.mem +len=3488 +op=2 +vcd=one.vcd
# TVLA_RESULT status=2 cycles=110564
```
`in.mem` 每行一个十六进制字节。Decaps 输入是 dk‖c，共 2400 + 1088 = 3488 字节。

### 4.3 VCD → 翻转 trace：`pkg/sca/vcd.go`
- 只统计 `tb_tvla.dut` 作用域下的信号。
- 以 `clk` 的上升沿划分周期。和上升沿处在同一时刻的变化（寄存器更新）算入新周期；下降沿的变化算入当前周期。
- 每次值变化，按比特计算新旧值的汉明距离。
- VCD 开头的初始值块只用来设定初值，不计入翻转。
- 同时记录每个周期 `u_ctrl.pc` 的值，用来把周期映射到微码指令。

翻转次数按下面几组分别统计：

| 分组 | 含义 |
|---|---|
| `total` | 核心内所有信号，每根线只算一次 |
| `top` | `pqc_mlkem_top` 自身这一层的信号（模块之间的连线等） |
| `u_alu` | 多项式 ALU：NTT/INTT、basemul、加减 |
| `u_sponge` | Keccak sponge，含 `u_keccak` 置换 |
| `u_unpack` | SampleNTT、CBD、Decode、Decompress |
| `u_pack` | Compress、Encode |
| `u_polyram` | 多项式 RAM 的端口 |
| `u_dbuf` | 8 KB data buffer 的端口 |
| `u_ctrl` | 微码控制器 |
| `u_csr` | AXI 寄存器 |

同一根线如果连接两个模块，会同时计入这两个模块的分组。所以各模块的数加起来不等于 `total`。

注意：Verilator 默认不记录元素个数超过 32 的数组，所以 RAM 的存储单元本身不在 VCD 里。但 RAM 的地址、数据、写使能端口都在，这正是真实 BRAM 功耗的主要来源。

### 4.4 t 检验：`pkg/sca/accum.go`
t 检验用 Welford 算法逐条累加：每条 trace 解析完就更新两组的均值和方差，然后丢掉。所以内存占用与 trace 数量无关，可以放心跑几千条。它和 `WelchTTest` 的结果完全一致，单元测试 `TestTTestAccumulatorMatchesWelch` 做了验证。

---

## 5. 输出文件

`-o` 指定的目录（默认 `build/tvla_sim/`）下会生成：

| 文件 | 内容 |
|---|---|
| `summary.txt` | 运行结束时打印的报告 |
| `tvla_t.csv` | 每个周期一行：`cycle, pc, instruction, t_total, t_top, t_u_alu, …` |
| `tvla_mean.csv` | 每个周期 fixed 组和 random 组 `total` 翻转数的均值，可以看出功耗曲线的形状 |
| `vectors.csv` | 本次使用的输入：`index, class, input_hex` |
| `vcd/trace_XXXX_fixed.vcd`、`vcd/trace_XXXX_random.vcd` | 每组保留的前 `--keep-vcd` 个 VCD（默认各 1 个，每个约 43 MB），用于查看波形 |

其余 trace 的 VCD 解析完就删除，不占磁盘。

---

## 6. 如何解读结果

### 6.1 第一步：先跑 sanity check
```bash
./pqc-testkit sca sim --op decaps -n 200 --fixed-vs-fixed --keep-vcd 0 -o build/tvla_sanity
```
两组输入完全相同，理论上不存在任何泄漏。实测输出如下：
```
instance      max|t|    cycle     leaky     first  instruction at max
total            4.9    28231     0.00%     28231  ENCODE d=1 s4 -> 0x3060
u_alu            4.3    19730     0.00%        -1  DECODE d=12 IN+768 -> s8
u_unpack         5.2    88920     0.00%     88920  CBD2 -> s6
...
```
这里 max|t| 在 4.3–5.2 之间，而不是 0，原因是**多重检验**。一条 trace 有 11 万个采样点，每个点都在做一次检验。即使完全没有泄漏，纯噪声的最大 |t| 也大约是 √(2 ln 110000) ≈ 4.8，偶尔会超过 4.5。

所以在本流程里判断"泄漏"要看两点：
- |t| 明显高于这个噪声底（比如 > 7）；
- 或者大段连续的周期都超过 4.5，而不是零星一两个点。

真实测量也是同样的道理。规范做法是用两组独立的数据各做一次，只有两次都超过阈值的点才算泄漏。

### 6.2 第二步：正式评估
```bash
./pqc-testkit sca sim --op decaps -n 200 -o build/tvla_decaps
```
实测输出：
```
ML-KEM-768 decaps simulation TVLA
traces: 200 (100 fixed, 100 random), cycles per trace: 110564..110564, noise sigma: 1.00 toggles

instance      max|t|    cycle     leaky     first  instruction at max
total           54.9    22921    52.23%         8  INTT s4
top             44.1   105465    19.24%         8  DECODE d=1 0x3060 -> s6
u_alu           54.3    22921    42.38%       974  INTT s4
u_csr            5.1    59275     0.00%     59275  ENCODE d=10 s4 -> 0x3100
u_ctrl          33.9      377     6.57%         8  DECODE d=10 IN+2400 -> s7
u_dbuf          32.6    28997     4.49%         8  HABS 0x3060 len=32
u_pack          46.5    28748     9.23%       974  ENCODE d=1 s4 -> 0x3060
u_polyram       44.7   105465    13.28%        13  DECODE d=1 0x3060 -> s6
u_sponge        30.9    30799     3.48%        11  HABS IN+2400 len=1088
u_unpack        35.6    37333     4.75%        11  CBD2 -> s1

Top 10 microcode instructions by max|t| (total):
   max|t|          cycles    pc  instruction
     54.9   22406-27018     247  INTT s4
     54.3   53436-58048     297  INTT s4
     51.1     970-4558      236  NTT s7
     ...
RESULT: LEAKAGE DETECTED (expected for an unmasked core)
```

各列的含义：
- **max|t| / cycle**：这个分组 |t| 最大的周期，以及该周期正在执行的微码指令。
- **leaky**：|t| > 4.5 的周期占全部周期的比例。
- **first**：第一个超过阈值的周期，-1 表示没有。
- **Top 10 microcode instructions**：把连续执行同一条微码指令的周期合成一段，按段内最大 |t| 排序。这是定位泄漏最直接的视图。

例子中 `u_csr` 只有 5.1，比例是 0.00%，和 sanity check 的噪声底一样，属于误报。其他模块都在 30–55，而且大段连续，是真实的泄漏。

### 6.3 把泄漏对应到算法步骤
Decaps 的微码按 FIPS 203 Algorithm 18 的顺序执行。`tvla_t.csv` 里的 `pc` 和 `instruction` 两列可以把每个周期对应到具体步骤（pc 以 ML-KEM-768 的 ROM 为准）：

| pc | 微码 | 算法步骤 | 是否涉及秘密 |
|---|---|---|---|
| 235–246 | `DECODE IN+2400…`、`NTT s7`、`DECODE d=12 IN+0…`、`BMUL s8*s7` | 解码密文 u 并做 NTT；解码私钥 ŝ；计算 ŝ∘û | **是**（ŝ 与密文相乘） |
| 247 | `INTT s4` | INTT(ŝᵀû) | **是** |
| 248–250 | `DECODE d=4…`、`SUB`、`ENCODE d=1` | w = v − ŝᵀu，解码出 m′ | **是**（m′ 是被封装的秘密消息） |
| 251–255 | `HINIT SHA3-512 … HSQZ` | (K′, r) = G(m′‖h) | **是** |
| 256–260 | `HINIT SHAKE256 … HABS IN+2400 len=1088` | K̄ = J(z‖c) | **是**（z 是私钥的一部分） |
| 261 起 | `CBD2`、`NTT`、`SAMPLE`、`BMUL`、`INTT`、`ENCODE` | 用 r 重新加密得到 c′ | **是**（r 由 m′ 派生） |
| 最后 | `CMP`、`CSEL` | 比较 c 与 c′，选择 K′ 或 K̄ | **是** |

需要注意两点：
- **"fixed vs random 密文"是非特定（non-specific）检验。** 它检测的是"功耗是否依赖输入"，而密文本身是公开的。所以像 cycle 8 起 `DECODE d=10 IN+2400`（读密文）这类点，泄漏的只是公开数据，本身不构成攻击面。
- **真正需要关注的**是密文与秘密相结合之后的步骤：上表中 pc 238 起的 `BMUL ŝ∘û`、`INTT`、m′ 的解码，以及之后所有由 m′ 派生的运算。本例 max|t| 出现在 pc 247 `INTT s4`（ŝᵀû），正是一阶 DPA/CPA 攻击 ML-KEM 解封装时最常用的目标。

### 6.4 其他实验
```bash
# 固定合法密文 vs 随机密文：比较正常路径与隐式拒绝路径
./pqc-testkit sca sim --op decaps --invalid-random -n 200 -o build/tvla_reject

# KeyGen
./pqc-testkit sca sim --op keygen -n 200 -o build/tvla_keygen

# 提高噪声、增加 trace：更接近真实测量条件
./pqc-testkit sca sim --op decaps -n 2000 --noise 20 --keep-vcd 0 -o build/tvla_noisy
```

**KeyGen 的特殊情况**：SampleNTT 的拒绝采样次数取决于 ρ，所以每条 trace 的周期数不同（实测 65684–66005）。第一次出现数据相关的延迟之后，各条 trace 就不再按周期对齐，后面的 t 值（例如 `u_ctrl` 的 227）主要反映的是时序差异。报告会打印 `note: trace lengths differ` 作为提示。ρ 是公开的，这种时序差异本身不泄露秘密，但会让后面的逐周期比较失去意义。所以 KeyGen 只看第一次 SAMPLE 之前的周期；关于秘密的结论以 Decaps 为准。

**噪声 σ 的选择**：
- σ 小（默认 1）：接近"理想无噪声"的上界，能看到所有与数据相关的翻转。
- σ 大：t 值按 1/σ 下降，需要按 σ² 增加 trace 数量才能检测到同样的泄漏。可以用它估计"真实测量大概需要多少条 trace"。

---

## 7. 查看波形

保留下来的 VCD 可以用 GTKWave 或 Surfer 打开：
```bash
brew install --cask gtkwave            # macOS；Linux 用 apt install gtkwave
gtkwave build/tvla_decaps/vcd/trace_0000_fixed.vcd
```
推荐的对照方法：
1. 在 `tvla_t.csv` 或图中找到 |t| 大的周期 c。
2. VCD 的时间单位是 ps，时钟周期是 10 ns，所以周期 c 对应"起始时间 + c × 10000 ps"。起始时间是文件里第一个 `#` 时间戳。
3. 同时打开一个 fixed 和一个 random 的 VCD，看同一时刻哪些信号不同。

---

## 8. 画 t 曲线

```bash
python3 -m pip install matplotlib        # 或者：uv run --with matplotlib scripts/plot_tvla.py ...
python3 scripts/plot_tvla.py build/tvla_decaps                                 # 所有分组
python3 scripts/plot_tvla.py build/tvla_decaps --groups total u_alu u_sponge   # 选几组
python3 scripts/plot_tvla.py build/tvla_decaps --from 22000 --to 28000 -o zoom.png   # 放大到 INTT 这一段
```
图中红色虚线是 ±4.5。Decaps 的 `total` 曲线能明显看出分段：NTT、INTT、BMUL 阶段泄漏最强；只做 Keccak 的阶段，`u_alu` 基本回到噪声水平。

`tvla_t.csv` 和 `tvla_mean.csv` 都是普通 CSV，也可以直接用 Excel、Origin 或 MATLAB 画图。

---

## 9. 命令参数一览

```
pqc-testkit sca sim [flags]
  --op string          keygen | decaps（默认 decaps）
  -n, --traces int     trace 总数，两组各一半（默认 200）
  --invalid-random     decaps：random 组使用随机密文
  --fixed-vs-fixed     自检：两组输入相同
  --seed string        固定的 64 字节种子（十六进制）
  --noise float        每个采样点加的高斯噪声标准差，单位为翻转次数（默认 1）
  --noise-seed uint    噪声随机数种子（默认 1）
  --keep-vcd int       每组保留的 VCD 数（默认 1）
  -j, --jobs int       并行仿真数（默认 CPU 核数）
  -o, --out string     输出目录（默认 build/tvla_sim）
  --sim string         仿真模型路径（默认 testbench/tvla_sim/obj_dir/Vtb_tvla）
  --scope string       VCD 中核心的作用域（默认 tb_tvla.dut）
  --rom string         用来给指令命名的微码 ROM 源文件
```

---

## 10. 局限

| 局限 | 影响 |
|---|---|
| HD 模型只数翻转，不考虑电容、毛刺、耦合 | 真实芯片的泄漏只会比仿真结果更多 |
| RTL 级而非门级网表 | 综合后的逻辑结构不同，泄漏的位置和强度会变化。门级仿真需要用 Vivado 导出网表（`write_verilog -mode funcsim`），流程相同，但仿真慢得多 |
| 只做一阶 t 检验 | 不评估高阶泄漏，对未掩码的设计足够了 |
| 大数组不在 VCD 中 | RAM 存储单元的翻转没有计入，端口的翻转已经计入 |

---

## 11. 常见问题

**`simulator ... not found`**
先执行 `make -C testbench/tvla_sim`。

**`core finished with STATUS=… (error)`**
核心报错了。常见原因是 bitstream 或 ROM 不是 768，或者改了 RTL 之后没有重新编译模型。用手动命令（4.2 节）跑一次，看打印的 status。

**KeyGen 的结果"到处都泄漏"**
见 6.4 节：trace 长度不同导致不对齐，这是预期现象。

**磁盘空间**
每个 Decaps VCD 约 43 MB。默认只保留每组 1 个，其余解析完立即删除。`--keep-vcd 0` 则一个都不保留。

---

## 12. 相关文件

| 文件 | 作用 |
|---|---|
| `testbench/tvla_sim/tb_tvla.sv` | 单条 trace 的 testbench，负责生成 VCD |
| `testbench/tvla_sim/Makefile` | 编译 Verilator 模型 |
| `cmd/pqc-testkit/cmd/scasim.go` | `sca sim` 命令：调度仿真、汇总、输出 |
| `pkg/sca/vcd.go` | VCD → 逐周期翻转 trace |
| `pkg/sca/accum.go` | 流式 Welch t 检验 |
| `pkg/sca/mlkem.go` | fixed/random 向量生成（与 `sca gen`、`sca timing` 共用） |
| `scripts/plot_tvla.py` | 画 t 曲线 |
| [TVLA_VECTORS_GUIDE.md](TVLA_VECTORS_GUIDE.md) | fixed/random 向量的生成与使用 |
