# hostlab — 实测侧信道采集框架

上位机侧的测量框架：把 fixed-vs-random 测试向量经 UART 发给 Arty A7 上的 ML-KEM-768 核心，逐次采集功耗 trace，存成标准 numpy 数组，并做 Welch t 检验。

这是 `scripts/tvla_sim.py`（仿真级 TVLA）的**实测对应物**：同样的统计量，输入换成示波器采到的真实波形，而不是从 VCD 算出来的汉明距离。做防护（countermeasure）研究时，两者配合使用 —— 仿真定位泄漏在哪条指令、哪组寄存器，实测确认防护在真实硅片上是否有效、需要多少条 trace 才能攻破。

```bash
uv run python hostlab/capture.py ports            # 找板子
uv run python hostlab/capture.py check -p PORT    # 链路自检 + 测一次运算耗时
uv run python hostlab/capture.py dry-run -p PORT  # 不接示波器，先跑通整条流程
uv run python hostlab/capture.py run -p PORT -r VISA_RESOURCE -n 5000
uv run python hostlab/capture.py analyse DIR
```

---

## 1. 测量链路

```
   ┌──────────┐   USB/UART 115200    ┌───────────────────────────┐
   │          │─────────────────────>│  Arty A7-100T             │
   │  上位机   │   向量 / 寄存器       │  uart_axi_bridge          │
   │  (PC)    │<─────────────────────│    └─ pqc_mlkem_top        │
   │          │   K / CYCLE_COUNT     │         206 MHz           │
   └──────────┘                      └───────────┬───────┬───────┘
        │  ▲                                     │       │
        │  │ pyvisa (USB / LAN)            触发   │       │ 功耗
        │  │                                     ▼       ▼
        │  └──────────────────────┬────────[外触发]  [探头 CH1]
        └─────────────────────────┤        示波器
                                  └───────── trace ──────┘
```

一次迭代：上位机把输入写进核心的数据缓冲 → 示波器 arm → 写 `CTRL.start` → 核心运算并触发示波器 → 上位机读回结果和波形 → 存盘。

### 1.1 触发信号（重要）

**没有稳定的触发就没有可用的 trace。** trace 之间对不齐时，同一个采样点在不同次运行中对应的不是同一个时钟周期，t 检验和 CPA 都会被抹平。

当前 RTL **没有专用触发引脚**，可用的方案按推荐顺序：

| 方案 | 信号 | 引脚 | 抖动 | 说明 |
| ---- | ---- | ---- | ---- | ---- |
| **A（推荐）** | LD4 = `status_busy` | H5 | ~1 个核心周期 | 运算全程为高（Decaps 约 128 µs），正好是运算包络 |
| B | UART RX 最后一字节 | A9 | 桥接帧处理时间，固定帧下较小 | 不用碰板上任何焊点，接 USB-UART 的 TX 线即可 |
| C | 功耗信号自身 | — | 大 | 用运算开始时电流跳变做边沿触发，只在信噪比很好时可行 |

方案 A 的 `status_busy` 已经由 `arty_a7_top.sv` 接到 LD4，不需要改 RTL。实际操作时探 LD4 的焊盘或其限流电阻。

> **关于专用触发引脚**：把 `status_busy`（或一个按微码 PC 可编程的窗口）单独引到 Pmod JA 的一个引脚上，探头接起来会干净得多，也能把采样窗口压到只覆盖解密的三条 BMUL，从而在同样的存储深度下大幅提高采样率。这需要改 RTL（顶层加一个输出端口 + 一条 XDC 约束），目前没有做。

### 1.2 示波器设置

这些是测量决策，框架不替你设定，只把设置记进 `meta.json`：

| 参数 | 建议起点 | 理由 |
| ---- | -------- | ---- |
| 采样窗口 | 覆盖 `check` 报出的运算时长（Decaps 约 128 µs） | 先采全程，窗口后期再缩 |
| 采样率 | ≥ 5× 核心时钟 = ≥ 1 GSa/s | 206 MHz 核心，低于 2× 会混叠 |
| 存储深度 | 由上面两项决定（128 µs × 1 GSa/s = 128 kpts） | `-s` 要和实际传回的点数一致 |
| 垂直档位 | 让信号占满 ±4 格，**不要削顶** | 削顶会把泄漏信息直接丢掉 |
| 耦合 | AC，或 DC + 偏置抵掉直流 | 关心的是波动不是绝对值 |
| 带宽限制 | 先开 20 MHz 看整体，分析时再放开 | 降噪，但会滤掉高频泄漏 |
| 探头位置 | 核心电源去耦电容两端，或串一个 1 Ω 采样电阻 | Arty A7 没有预留 SMA，需要改板 |

采样率和深度的权衡：窗口缩到只覆盖感兴趣的运算（例如解密的三条 BMUL，约 1.4 µs）后，同样 128 kpts 的深度可以支撑 90 GSa/s，信噪比会好很多。

---

## 2. 安装

| 依赖 | 用途 | 检查 |
| ---- | ---- | ---- |
| Python ≥ 3.9 + uv | 运行框架 | `uv sync` |
| pyserial | 串口 | 已在 `pyproject.toml` |
| numpy | trace 存储和统计 | 已在 `pyproject.toml` |
| matplotlib | 出图 | 已在 `pyproject.toml` |
| **pyvisa** | 控制示波器 | `uv add pyvisa pyvisa-py`（USB 还需要 `pyusb`） |
| Go ≥ 1.25 | 生成向量（`pqc-testkit sca gen`） | `go version` |

示波器连接方式：

- **LAN**：`TCPIP0::192.168.1.50::INSTR`，只需 `pyvisa-py`，最省事。
- **USB**：`USB0::0x0957::0x17A6::MY1234::INSTR`，需要 `pyusb` 后端，或装厂商的 VISA runtime（Keysight IO Libraries / NI-VISA）。

`uv run python hostlab/capture.py scopes` 会列出当前能看到的仪器。

---

## 3. 实验顺序

按这个顺序做，每一步都能把故障归到正确的层面上。

### 第 0 步：功能正确性

```bash
make acvp-uart PORT=/dev/cu.usbserial-XXXXB
```

先确认核心算得对（NIST ACVP 60/60），再谈测泄漏。见 `docs/ACVP_TEST.md`。

### 第 1 步：链路自检

```bash
uv run python hostlab/capture.py check -p /dev/cu.usbserial-XXXXB
```

自底向上 7 项检查：身份、版本、复位、寄存器读写、CRC 拒绝与恢复、1 KB 批量传输、缓冲区寻址。全过才继续；它还会跑一次 Decaps 并报出运算时长，用来设示波器窗口。

### 第 2 步：空跑（不接示波器）

```bash
uv run python hostlab/capture.py dry-run -p PORT -n 200 --detection-curve
```

用合成的 `NullScope` 走完整条流程：生成向量 → 发板 → 跑运算 → 存盘 → 分析。此时 trace 是纯高斯噪声，所以**分析必须报告"未检测到泄漏"**；如果报了，是分析本身有问题。

再用 `--synthetic-leak 5` 跑一次，这次**必须**检测出来 —— 确认分析确实有检出能力，不是永远说"没有"。

### 第 3 步：对照组（接上示波器后的第一件事）

```bash
uv run python hostlab/capture.py run -p PORT -r RESOURCE -n 2000 --fixed-vs-fixed
```

两组用完全相同的输入，因此**真实泄漏在构造上不可能存在**。这时 t 检验报出来的任何东西，都是测量装置本身的假阳性水平：触发抖动、温度漂移、两组没有真正交错、示波器自动量程在中途变了档，都会在这里暴露。

对照组的 max|t| 必须落在噪声底（图上灰色虚线）附近。**对照组不干净，正式结果就不能解释。**

### 第 4 步：正式采集

```bash
uv run python hostlab/capture.py run -p PORT -r RESOURCE -n 20000 \
    --seed <128位十六进制> --detection-curve
```

记下 `--seed`：它固定了密钥对和 fixed 组的那一条密文，整个 campaign 可以精确复现。不给就随机生成，程序会把实际用的 seed 记进 `meta.json`。

吞吐受 UART 限制：Decaps 的输入是 3488 字节，115200 baud 下光传输就约 0.3 s，所以 **1 条 trace 约 0.4 s，20000 条约 2.2 小时**。中途 Ctrl-C 会干净地收尾，已采的部分可以直接分析。

### 第 5 步：加防护后对比

防护做完后，用**同样的 seed、同样的示波器设置**再采一次，比较的指标不是峰值 `|t|`（它随 √N 增长，trace 数一变就没有可比性），而是 **traces-to-disclosure**：`|t|` 从第几条 trace 开始稳定越过 4.5。`--detection-curve` 就是算这个的。

---

## 4. 完整性检查

长时间采集最大的风险是：采了几小时，结果数据是废的。每一次运行都做三项检查：

1. **核心必须报 DONE 且无错误码**，否则这条 trace 丢弃并计数。
2. **`CYCLE_COUNT` 必须恒定**。有变化会立即告警 —— 这本身就是一个时序泄漏的发现，值得跟进。
3. **fixed 组每条的输出必须和第一条一致**。密钥和密文都固定时共享密钥 K 也固定，所以这是一个几乎每隔一条就做一次的免费已知答案测试。一旦不符立即中止：说明板子复位了、密钥变了或链路在静默地损坏数据，继续采没有意义。

链路出错时会自动 resync 并重试一次；连续失败超过 `--max-errors`（默认 5）才放弃。

---

## 5. 数据格式

一个 campaign 目录就是几个标准 `.npy` 文件加一个 JSON：

```
traces.npy    (N, S) 采样点，dtype 按采集设置（int8/int16/float32）
inputs.npy    (N, L) uint8，每条 trace 对应的输入（Decaps 是 dk‖c，3488 字节）
classes.npy   (N,)   uint8，0 = fixed，1 = random
cycles.npy    (N,)   uint32，核心报告的 CYCLE_COUNT
outputs.npy   (N, 32) uint8，核心返回的结果
meta.json     参数、仪器设置、来源、进度
tvla.png      分析图（analyse 生成）
```

数组是一次性按最终大小建好、逐行通过内存映射写入的，所以**采集中途崩溃或 Ctrl-C，文件依然可读**，`meta.json` 里的 `completed` 记录实际写了多少条。内存占用与 trace 数无关。

全部是标准 numpy，不依赖本框架也能读：

```python
import numpy as np, json
traces = np.load("traces.npy", mmap_mode="r")
meta   = json.load(open("meta.json"))
n      = meta["completed"]                  # 实际完成的条数
cls    = np.load("classes.npy")[:n]
fixed, random = traces[:n][cls == 0], traces[:n][cls == 1]
```

---

## 6. 作为库使用

```python
import sys; sys.path.insert(0, "hostlab")
from tracelab import Target, TraceSet, assess, summary

# 直接驱动板子（不采 trace）
with Target.open("/dev/cu.usbserial-XXXXB") as t:
    print(t.info())
    out = t.decaps(dk, ct)
    print(out.describe(), out.output.hex())

# 分析已有的 campaign
ts = TraceSet.load("sim_result/traces/20261006_decaps")
print(ts.summary())
print(summary(assess(ts, detection_curve=True)))
```

分层结构（`tracelab/`）：

| 模块 | 职责 |
| ---- | ---- |
| `protocol.py` | UART 帧协议（CRC32）、寄存器映射、数据缓冲 |
| `target.py` | ML-KEM-768 运算、链路自检 |
| `vectors.py` | 调用 `pqc-testkit sca gen` 生成 fixed/random 向量集 |
| `scope.py` | `VisaScope`（SCPI）、`NullScope`（合成，用于空跑） |
| `campaign.py` | 采集循环 + 完整性检查 |
| `storage.py` | `TraceSet`：增量写入的 `.npy` + `meta.json` |
| `analysis.py` | Welch t 检验、噪声底、detection curve、出图 |

向量来自 Go 侧的 `pkg/sca`，和功能测试用的是同一份 ML-KEM 参考实现，上位机不需要自己实现这个方案。

**关于 NIST 向量**：`acvp/` 下的 ACVP 文件是**功能性**已知答案测试 —— 一组固定的、数量很少的向量，没有 fixed/random 结构，用来验证算得对不对（`scripts/acvp_test.py`），**不是**泄漏评估的输入集。采集 campaign 用 `sca gen`，功能验证用 ACVP。

---

## 7. 不同厂商的示波器

SCPI 在 IEEE 488.2 以外并不统一，厂商相关的命令集中在 `scope.py` 的 `DIALECTS` 里：

| dialect | 适用 |
| ------- | ---- |
| `default` | Keysight InfiniiVision、Rigol DS/MSO，以及足够接近的型号 |
| `tektronix` | Tektronix TDS/MDO 系列（`CURVe?` + `DATa:` 设置） |

```bash
uv run python hostlab/capture.py run ... --dialect tektronix --channel CH1
```

新型号请加一个 dialect 条目，不要改采集流程本身。

---

## 8. 常见问题

| 现象 | 处理 |
| ---- | ---- |
| `no serial ports found` | 板子没插、没上电，或没装 FTDI 驱动 |
| 找不到是哪个串口 | Arty 在一颗 FTDI 上枚举出两个口，**第二个**是 UART（第一个是 JTAG）；macOS 用 `/dev/cu.*` |
| `check` 第 1 项就失败 | 比特流没烧进去（LD7 心跳灯应该在闪）、波特率不是 115200、或接错了 JTAG 口 |
| `no VISA resources found` | USB 需要后端：`uv add pyusb`，或装厂商 VISA；LAN 的话直接给 `TCPIP0::<ip>::INSTR` |
| `scope did not trigger within N s` | 触发源/电平不对，或触发线没接上；先在示波器上手动单次触发确认能触发 |
| 每条 trace 点数和 `-s` 不符 | 存储深度设置和 `--samples` 不一致；以示波器实际返回的为准 |
| 对照组（fixed-vs-fixed）就有大 t | 触发抖动、量程中途变化、温漂；先解决这个再看正式结果 |
| `CYCLE_COUNT varied` 告警 | 核心对这批输入不是常数时间 —— 这是一个真实的发现，值得单独查 |
| fixed 组输出不一致导致中止 | 板子复位了、电源不稳，或链路在静默损坏数据；检查供电和 USB 线 |
| 采集太慢 | 瓶颈是 115200 baud 下传 3488 字节输入。桥接本身支持更高波特率（`make regress` 里按 1 Mbaud 测过），但需要同时改板级封装的 `BAUD_RATE` 参数并重新综合 |

---

## 9. 相关文档

- `docs/ACVP_TEST.md` — NIST ACVP 功能验证（上板第一步）
- `docs/TVLA_SIM.md` — 仿真级 TVLA，含解密三个运算式 P1/P2/P3 的泄漏对比
- `docs/papers/` — 参考文献（按类别、算法、FPGA 可查）

## 10. 参考

- Goodwill, Jun, Jaffe, Rohatgi, *A testing methodology for side-channel resistance validation*, NIST NIAT Workshop 2011 — TVLA 的原始方法学
- Schneider, Moradi, *Leakage Assessment Methodology*, CHES 2015 — t 检验在实践中的统计细节与多重检验问题
- ISO/IEC 17825 — 侧信道测试的标准化流程
- FIPS 203 — ML-KEM 规范
