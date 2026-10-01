# TVLA fixed/random 测试向量：生成与使用指南

本文说明 ML-KEM-768 核心的 TVLA 测试向量：怎么生成 fixed 组和 random 组的输入、文件格式是什么，以及怎样用于三种评估。

| 用途 | 命令 | 是否需要硬件 |
|---|---|---|
| 生成向量文件（给示波器采集，或存档复现） | `pqc-testkit sca gen` | 否 |
| 板上时序 TVLA（CYCLE_COUNT 当 trace） | `pqc-testkit sca timing` | Arty 板 |
| 仿真级 TVLA（RTL 波形算功耗） | `pqc-testkit sca sim` | 否，需要 Verilator |

三条命令用的是**同一个向量生成器**（`pkg/sca/mlkem.go`），所以向量的定义完全一样。仿真级 TVLA 的细节见 [SIM_TVLA_GUIDE.md](SIM_TVLA_GUIDE.md)。

---

## 1. 向量是怎么定义的

TVLA 的 fixed-vs-random 检验需要两组输入：
- **fixed 组（class 0）**：每一条都是**同一个**输入；
- **random 组（class 1）**：每一条都是**新的随机**输入。

逐点比较两组 trace 的均值。如果某点差异显著（|t| > 4.5），就说明功耗或时间与输入有关。

### 1.1 三种向量集

| 命令参数 | 被测运算 | fixed 组 | random 组 | 每条输入长度 |
|---|---|---|---|---|
| `--op decaps`（默认） | Decaps | 固定 dk + 固定合法密文 c₀ | 同一个 dk + 每条新做一次 Encaps 得到的合法密文 | 3488 字节 |
| `--op decaps --invalid-random` | Decaps | 同上 | 同一个 dk + 随机字节当密文（重加密比对失败，走隐式拒绝） | 3488 字节 |
| `--op keygen` | KeyGen | 固定的 d‖z | 每条随机的 d‖z | 64 字节 |

几点说明：
- **Decaps 的两组 dk 相同**，只有密文不同，所以检验的是"功耗是否依赖密文，以及密文与私钥结合后的中间值"。
- `--invalid-random` 用来比较"正常解封装路径"和"隐式拒绝路径"。FIPS 203 要求这两条路径不可区分。
- KeyGen 的 random 组，d 和 z 都是随机的。

### 1.2 输入的字节布局

输入就是写进 FPGA data buffer（`DATA_IN_ADDR`，默认偏移 0x0000）的字节串，与 FIPS 203 的 `*_internal` 函数一致：

```
KeyGen  (64 B)   : d (32) ‖ z (32)
Decaps  (3488 B) : dk (2400) ‖ c (1088)
                   dk = dk_PKE (1152) ‖ ek (1184) ‖ H(ek) (32) ‖ z (32)
```

### 1.3 顺序已经随机打乱
两组严格各占一半，然后用 crypto/rand 做 Fisher–Yates 打乱，**不是** fixed、random 交替排列。这样做是为了避免温度漂移、电源波动这类随时间缓慢变化的因素与组别相关，造成假泄漏。

**使用时必须按文件顺序逐行运行，不要按 class 重新排序。**

### 1.4 可复现性：seed
- fixed 组完全由 **seed**（64 字节）决定：
  - KeyGen：d‖z 就是 seed；
  - Decaps：密钥对由 seed 生成，固定消息 m₀ = SHA3-256("pqc-testkit tvla fixed m" ‖ seed)，c₀ = Encaps(ek, m₀)。
- 不指定 `--seed` 时会随机选一个，并在输出里打印出来。以后用 `--seed <该值>` 就能得到完全相同的 fixed 组。
- random 组**每次运行都重新随机**。这是 TVLA 的要求，不需要复现。需要存档时，直接保存生成的 CSV 文件。

---

## 2. 生成向量文件

### 2.1 先编译工具
```bash
cd ML-KEM-Testkit
go build -o pqc-testkit ./cmd/pqc-testkit
```

### 2.2 生成命令
```bash
# Decaps：固定密文 vs 随机合法密文（最主要的一组）
./pqc-testkit sca gen --op decaps -n 10000 -o vectors_decaps.csv

# Decaps：固定合法密文 vs 随机密文（隐式拒绝）
./pqc-testkit sca gen --op decaps --invalid-random -n 10000 -o vectors_reject.csv

# KeyGen
./pqc-testkit sca gen --op keygen -n 10000 -o vectors_keygen.csv
```

输出示例：
```
wrote 10000 ML-KEM-768 decaps TVLA vectors (5000 fixed, 5000 random, shuffled) to vectors_decaps.csv
seed: f1798cc395b324803d941e249b75bf2387ca76a064f7461f229b2b11c233b8def50457f3c22574d9f4cb93e31e92cdb590cf39e8cd18d07503546e0ac00909db
(pass --seed with this value to regenerate the same fixed class)
```
**请把 seed 记下来**，写进实验记录或组会 PPT。两组实验如果要用同一个密钥（例如 `vectors_decaps` 和 `vectors_reject`），生成时传同一个 `--seed`：
```bash
SEED=f1798cc3...09db     # 128 个十六进制字符
./pqc-testkit sca gen --op decaps                  -n 10000 --seed $SEED -o vectors_decaps.csv
./pqc-testkit sca gen --op decaps --invalid-random -n 10000 --seed $SEED -o vectors_reject.csv
```

### 2.3 参数

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--op` | `decaps` | `keygen` 或 `decaps` |
| `-n, --traces` | 1000 | 总条数，两组各一半，必须是偶数且 ≥ 4 |
| `--seed` | 随机 | 64 字节 seed 的十六进制（128 个字符） |
| `--invalid-random` | 关 | 只对 decaps 有效：random 组改用随机密文 |
| `-o, --output` | `tvla_vectors.csv` | 输出文件 |

### 2.4 条数怎么选
- 板上时序 TVLA：1000–2000 条已经足够。周期数要么完全恒定，要么差别很明显。
- 仿真 TVLA：200 条就能看到未掩码设计的泄漏；噪声调大时按 σ² 增加。
- 示波器功耗采集：未防护的实现通常 1 000–10 000 条可以检测到一阶泄漏；做了防护的实现需要 10⁵–10⁶ 条。

文件大小：Decaps 每条约 7 KB，1 万条约 70 MB；KeyGen 1 万条约 1.3 MB。

### 2.5 检查生成的文件
```bash
f=vectors_decaps.csv
echo "总条数:        $(($(wc -l < $f) - 1))"
echo "fixed 条数:    $(awk -F, 'NR>1 && $2==0' $f | wc -l)"
echo "random 条数:   $(awk -F, 'NR>1 && $2==1' $f | wc -l)"
echo "fixed 输入种类: $(awk -F, 'NR>1 && $2==0 {print $3}' $f | sort -u | wc -l)   # 应为 1"
echo "每条字节数:    $(awk -F, 'NR==2 {print length($3)/2}' $f)                  # decaps 3488, keygen 64"
echo "前 20 条类别:  $(awk -F, 'NR>1 && NR<=21 {printf "%s ", $2}' $f)           # 应该是打乱的"
```

---

## 3. 文件格式

CSV 文件，第一行是表头：
```
index,class,input_hex
0,0,f3aca30fe78d27b4aa31198d...     ← 3488 字节 = 6976 个十六进制字符
1,1,f3aca30fe78d27b4aa31198d...
...
```

| 列 | 含义 |
|---|---|
| `index` | 运行顺序，从 0 开始 |
| `class` | 0 = fixed，1 = random |
| `input_hex` | 写入 data buffer 的完整输入，十六进制 |

Python 读取：
```python
import csv
with open("vectors_decaps.csv") as f:
    for row in csv.DictReader(f):
        idx, cls, inp = int(row["index"]), int(row["class"]), bytes.fromhex(row["input_hex"])
```

---

## 4. 用法一：板上时序 TVLA（`sca timing`）

最简单的用法，只需要烧好 768 bitstream 的 Arty 板，不需要示波器。每次运算读回的 `CYCLE_COUNT` 当作只有 1 个采样点的 trace。

```bash
PORT=/dev/cu.usbserial-XXXXXXXX1      # macOS；Linux 一般是 /dev/ttyUSB1

./pqc-testkit sca timing -T uart -d $PORT --op decaps -n 1000 -o timing_decaps.csv
./pqc-testkit sca timing -T uart -d $PORT --op decaps --invalid-random -n 1000 -o timing_reject.csv
./pqc-testkit sca timing -T uart -d $PORT --op keygen -n 1000 -o timing_keygen.csv
```

- `sca timing` 自己在内存里生成向量，参数与 `sca gen` 相同（`--op`、`-n`、`--seed`、`--invalid-random`），不需要先生成文件。
- `-o` 保存每条的 `index, class, cycles`。
- 为了加速，dk 只写入一次，之后每条只发送和上一条不同的字节。115200 波特下 Decaps 每条约 0.1 秒，1000 条约 2 分钟。
- 没有板子时，可以用 `-T sim` 走一遍流程，但软件模拟器的周期数固定为 42000，没有评估意义。

输出示例和判读：
```
class         n        min        max         mean        std
fixed       500     110551     110551     110551.0       0.00
random      500     110551     110551     110551.0       0.00

Welch t = 0.000  (threshold |t| = 4.5)
RESULT: PASS - constant time, all 1000 runs took 110551 cycles
```

| 输出 | 含义 |
|---|---|
| `PASS - constant time` | 所有运行周期数完全相同，是最理想的结果 |
| `PASS - no timing leakage detected` | 周期数有波动，但与组别无关 |
| `TIMING LEAKAGE` | 周期数与输入组别有关，命令以非 0 退出 |

RTL 仿真中，Decaps-768 在三种密文下都是固定的 110551 周期（具体数值取决于密钥的 ρ）。**KeyGen 预期会报泄漏**：SampleNTT 的拒绝采样次数取决于 ρ，而 ρ 是公开的，包含在 ek 中，所以不泄露秘密。时序安全的结论以 Decaps 为准。

---

## 5. 用法二：仿真级 TVLA（`sca sim`）

没有硬件时，用 RTL 波形构造功耗 trace：
```bash
make -C testbench/tvla_sim
./pqc-testkit sca sim --op decaps -n 200 -o build/tvla_decaps
```
本次使用的向量会自动保存为 `build/tvla_decaps/vectors.csv`，格式同第 3 节，报告开头也会打印 seed。完整说明见 [SIM_TVLA_GUIDE.md](SIM_TVLA_GUIDE.md)。

---

## 6. 用法三：示波器 / ChipWhisperer 采集真实功耗

有测量设备后，用 `sca gen` 生成的文件驱动板子，每运行一条采集一条 trace。下面是基于 `scripts/uart_test.py` 中 `Link` 类的采集骨架，其中 `arm_scope()` 和 `read_trace()` 需要换成你所用示波器的 API：

```python
import csv, sys
sys.path.insert(0, "scripts")
from uart_test import Link

OP = {"keygen": 0, "decaps": 2}["decaps"]
link = Link("/dev/cu.usbserial-XXXXXXXX1", 115200, timeout=2.0)

traces, classes = [], []
with open("vectors_decaps.csv") as f:
    for row in csv.DictReader(f):              # 必须按文件顺序，不要重排
        inp = bytes.fromhex(row["input_hex"])
        arm_scope()                            # TODO：示波器进入等待触发状态
        out, cycles = link.run(768, OP, inp)   # 写输入 → start → 等待 done
        traces.append(read_trace())            # TODO：读回这一条功耗波形
        classes.append(int(row["class"]))
link.close()
```

采集时要注意：
- **触发**：最好用 FPGA 引出的触发信号，例如把 `o_busy` 接到 Pmod 引脚，busy 上升沿触发，这样每条 trace 都对齐到运算开始。现在的 bitstream 只把 busy 接到了 LED，需要在 `arty_a7_top.sv` 和 XDC 里加一个引脚。
- **测量点**：Arty A7 没有专门的分流电阻，需要在 FPGA 核心电源（VCCINT）上串电阻或用电流探头。专业评估一般用 ChipWhisperer CW305 等专用板。
- **速度**：`link.run` 每条都会写完整输入，Decaps 每条约 0.3 秒。可以参考 `sca timing` 的做法，只重发变化的字节。
- **分析**：把 `traces` 按 `classes` 分成两组，逐点做 Welch t 检验。可以用 `pkg/sca` 的 `WelchTTest` 或 `TTestAccumulator`，也可以用 numpy：
  ```python
  import numpy as np
  X, c = np.array(traces, dtype=float), np.array(classes)
  a, b = X[c == 0], X[c == 1]
  t = (a.mean(0) - b.mean(0)) / np.sqrt(a.var(0, ddof=1)/len(a) + b.var(0, ddof=1)/len(b))
  print("max |t| =", np.abs(t).max(), "at sample", np.abs(t).argmax())
  ```

---

## 7. 推荐的实验组合

| 实验 | 命令 | 回答的问题 | 预期结果 |
|---|---|---|---|
| ① Decaps 时序 | `sca timing --op decaps` | Decaps 是否常数时间 | constant time |
| ② 隐式拒绝时序 | `sca timing --op decaps --invalid-random` | 正常路径与拒绝路径能否被时间区分 | constant time |
| ③ 仿真自检 | `sca sim --fixed-vs-fixed` | 评估流程是否会误报 | max\|t\| ≈ 4.5–5（噪声底） |
| ④ Decaps 仿真功耗 | `sca sim --op decaps` | 未掩码设计泄漏在哪些阶段、哪些模块 | 明显泄漏，max\|t\| 约 55 |
| ⑤ KeyGen 时序 | `sca timing --op keygen` | 对照实验 | 报泄漏，但来自公开的 ρ |

组会或论文中报告时，写清楚以下几项：运算、向量集（是否 invalid-random）、条数、seed、噪声 σ（仿真时）、max|t| 及其位置、结论。

---

## 8. 常见问题

**random 组能不能也复现？**
不能，也不需要，每次运行都重新随机。需要完全相同的数据时，保存 `sca gen` 生成的 CSV，或者 `sca sim` 输出目录里的 `vectors.csv`。

**`--invalid-random` 用在 keygen 上报错？**
这个参数只对 decaps 有意义，keygen 不接受。

**`-n` 为奇数时报错？**
两组必须严格各占一半，所以条数必须是偶数，且 ≥ 4。

**`--seed` 报错？**
seed 必须正好是 64 字节，也就是 128 个十六进制字符。

**旧版本生成的文件还能用吗？**
能用。旧版本的 fixed 密文是随机选取的，而不是由 seed 派生，所以用同一个 seed 重新生成时 fixed 组会不一样，但作为 TVLA 输入完全有效。

---

## 9. 相关文件

| 文件 | 作用 |
|---|---|
| `pkg/sca/mlkem.go` | 向量生成器：`GenerateMLKEMTVLA` |
| `pkg/sca/tvla.go` | `WelchTTest`，一次性输入全部 trace |
| `pkg/sca/accum.go` | `TTestAccumulator`，逐条累加的 t 检验 |
| `cmd/pqc-testkit/cmd/sca.go` | `sca gen`、`sca timing` 命令 |
| `cmd/pqc-testkit/cmd/scasim.go` | `sca sim` 命令 |
| `scripts/uart_test.py` | `Link` 类，Python 驱动板子 |
| [SIM_TVLA_GUIDE.md](SIM_TVLA_GUIDE.md) | 仿真级 TVLA 指南 |
| [UART_TEST_GUIDE.md](UART_TEST_GUIDE.md) | 板子连接与 UART 测试 |
