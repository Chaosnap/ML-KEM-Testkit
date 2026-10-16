# 主机 ↔ FPGA 通信协议（UART）

本文描述 PC 与板上 ML-KEM-768 核心之间的通信协议：电脑通过它**发送数据、选择运算、启动执行、读取结果**，从而自动采集几千到几万条功耗 trace。

这个协议不是 ML-KEM 算法的一部分，也不控制 NTT、BaseMul 等运算单元。算法由核心内部的状态机（`mlkem_ctrl` + 微码）自动执行，协议只负责把输入放进去、启动运算、把结果取出来。

| 部分 | 文件 |
| ---- | ---- |
| FPGA 端（协议桥） | `hdl/core/uart_axi_bridge.sv` |
| 寄存器与数据缓冲 | `hdl/core/pqc_axi_csr.sv` |
| 主机端（Python） | `cpa_toolkit/uart_target.py`（`Link` 收发帧，`Target` 提供分步操作） |
| 测试 | `testbench/iverilog/tb_uart_link.sv`、`testbench/cdc/tb_bridge.sv`、`tests/test_uart_target.py` |

---

## 1. 系统结构

```
PC (Python, cpa_toolkit)
  │  USB 串口，8N1，默认 115200
  ▼
uart_rx ─► uart_axi_bridge ─► AXI4-Lite ─► axil_cdc ─► pqc_mlkem_top（206 MHz）
uart_tx ◄─┘   (100/125 MHz)                              ├─ CSR 寄存器 + 8 KB 数据缓冲
              ▲                                          ├─ 内部 FSM 自动执行 KeyGen / Encaps / Decaps
              └──────── core_busy（STATUS.busy）──────────┤
                                                         └─ trig_out = STATUS.busy ──► 示波器触发
```

- **Arty A7**：板载 USB 串口（FT2232H 的 `…1` 接口）直接连到 PL 的 `uart_rx/uart_tx`。
- **Arty Z7（纯 PL，`hdl/xilinx/arty_z7`）**：外接 3.3 V USB-UART 到 Pmod JA。
- **Arty Z7（PS 版，`hdl/xilinx/arty_z7_ps`）**：板载 USB 串口只连到 PS 的 MIO，所以由 PS 上的 `sw/arty_z7_uart_fwd` 把字节在 PS UART0 和 PS UART1（经 EMIO 到 PL）之间原样转发。协议与 A7 完全相同，主机代码不区分。
- **触发**：`trig_out = STATUS.busy`，从 START 到运算结束为高电平。A7 引脚是 Pmod JA1（G13），Z7 是 Pmod JA3（Y16）。示波器设为上升沿触发即可，设计细节和验证方法见第 5.1 节。
- 图中从串口到核心、触发和板上 LED 的全部逻辑，在三块板上是同一个模块 `hdl/core/mlkem_uart_system.sv`。各板的顶层只负责时钟（MMCM）、复位和引脚，Z7 PS 版另外加上 PS。

协议桥是**半双工、一问一答**的：主机发一帧，等到应答后再发下一帧。协议桥在处理命令、发送应答、等待运算（0x05）时不接收数据，这期间收到的字节会被丢弃。

---

## 2. 物理层

| 参数 | 值 |
| ---- | -- |
| 格式 | 8 数据位，无校验，1 停止位（8N1），LSB 先发 |
| 波特率 | 默认 115200；板级顶层参数 `BAUD_RATE` |
| 帧内超时 | 一帧内相邻两个字节间隔超过 100 ms（`TIMEOUT_CYCLES = CLK_FREQ/10`），半帧被丢弃，协议桥回到等待命令状态 |

**提高波特率**（吞吐量主要受此限制，见第 7 节）需要同时修改三处并重新生成 bitstream：

1. 板级顶层的 `BAUD_RATE` 参数（`arty_a7_top.sv`、`arty_z7_top.sv`、`arty_z7_ps_top.sv`）。
2. 仅 Z7 PS 版：`sw/arty_z7_uart_fwd/main.c` 中的 `BAUDGEN_*`/`BAUDDIV_*` 分频值。
3. 主机：GUI 的 Baud 输入框，或命令行 `--baud`。

`uart_rx/uart_tx` 每位的时钟数是 `CLK_FREQ / BAUD_RATE`，取整数。推荐 **1 000 000 baud**，因为以下三处都能整除，没有频率误差：A7 100 MHz / 1 M = 100；Z7 125 MHz / 1 M = 125；PS UART 100 MHz / (25 × 4)。

---

## 3. 帧格式

所有多字节字段均为**小端**。CRC 为 CRC-32/IEEE（与 Python `zlib.crc32` 相同）。

**主机 → FPGA（命令帧）**

```
[CMD:1][ADDR:4][LEN:4][DATA:LEN][CRC:4]      CRC 覆盖 CMD..DATA
```

**FPGA → 主机（应答帧）**

```
[STATUS:1][LEN:4][DATA:LEN][CRC:4]           CRC 覆盖 STATUS..DATA
```

- `STATUS = 0x00` 表示成功。
- `STATUS = 0x01` 表示错误，此时 `LEN = 0`。可能的原因：CRC 错、未知命令、长度不合法、缓冲区越界、AXI 错误。出错的命令**不会执行**，既不写也不启动。

主机检查应答 CRC。如果 `LEN > 16384`，视为失步，先等待 200 ms（超过帧内超时），再清空接收缓冲，然后读 `ALG_ID` 确认链路（`Link.resync()`）。

---

## 4. 命令

| CMD | 名称 | ADDR | LEN / DATA | 应答 DATA |
| --- | ---- | ---- | ---------- | --------- |
| `0x01` | 读寄存器 | 总线地址（CSR，见第 6 节） | 0 | 4 字节寄存器值 |
| `0x02` | 写寄存器 | 总线地址 | 4，u32 值 | 无 |
| `0x03` | 写数据缓冲 | 缓冲字节偏移 | 1..256，数据 | 无 |
| `0x04` | 读数据缓冲 | 缓冲字节偏移 | 4，u32 字节数（1..16384） | 指定字节数 |
| `0x05` | **运行并等待完成（Run）** | 要写的寄存器（通常是 CTRL `0x00`） | 12：u32 写入值，u32 输出偏移，u32 输出字节数（0..16384） | 12 字节状态块 + 输出 |

缓冲区的偏移和长度满足 `偏移 < 16384` 且 `偏移 + 长度 ≤ 16384`。实际缓冲是 8 KB，高 8 KB 是镜像（`pqc_axi_csr.sv`）。

### 4.1 `0x05` Run：FPGA 主动报告“运算完成”

`0x05` 是为采集设计的命令。主机发出一帧后**只需等待**，FPGA 在运算结束后才回复，回复里同时带回状态和结果：

```
主机  ──[05][CTRL][12][START, OUT_BASE, 32][CRC]──►
FPGA        写 CTRL.start ─► 核心 busy=1 …… 运算中 …… busy=0
            │◄────────── UART 两个方向都完全静默 ──────────►│
主机  ◄──[00][44][STATUS][CYCLE_COUNT][ERROR_CODE][K:32][CRC]──
```

协议桥内部的执行过程：

1. 先校验整帧（CRC、`LEN = 12`、输出区间不越界），失败则回 `STATUS 0x01`，不写不启动。
2. 把 u32 写入值写到 `ADDR`（`CTRL = 1` 即 START）。
3. 等待 `RUN_GUARD`（16 个桥时钟），让 `core_busy` 经两级同步器到达本时钟域。
4. 等到 `core_busy = 0`。如果超过 `RUN_TIMEOUT_CYCLES`（板级设为 1 s）还没结束，也停止等待。
5. 经 AXI 读回 `STATUS`、`CYCLE_COUNT`、`ERROR_CODE`。
6. 回复 `LEN = 12 + n`：

   | 偏移 | 内容 |
   | ---- | ---- |
   | 0 | STATUS（u32） |
   | 4 | CYCLE_COUNT（u32） |
   | 8 | ERROR_CODE（u32） |
   | 12 | 输出：**仅当** STATUS = done、无 error、不 busy 时，为数据缓冲中从“输出偏移”起的“输出字节数”个字节；否则为空（n = 0） |

要点：

- **运算期间链路静默**：从 START 到运算结束，FPGA 不发送任何字节，主机也不发送，所以 UART 的活动不会叠加到功耗 trace 上。旧流程不同：写 CTRL 的应答（9 字节，115200 下约 0.78 ms）正好在约 128 µs 的 Decaps 期间发出。这一点已由仿真检查（见第 9 节）。
- **没有 STATUS 轮询**：运算只能用 `0x05` 启动，完成、出错和超时都体现在状态块里。早先“写 CTRL.start，再反复读 STATUS 直到完成”的流程已从主机端全部移除（GUI、采集、ACVP 测试都只用 `0x05`）。超时时状态块中 `STATUS.busy = 1`，主机应先 CTRL.reset 再继续。
- **能力探测**：对只读寄存器 `ALG_ID`（`0x08`）发 `0x05`、写入值 0、输出字节数 0，不会启动任何运算，会立即收到 12 字节状态块。不支持 `0x05` 的旧 bitstream 会回 `STATUS 0x01`，帧被完整消费，链路不失步。`Target.probe_run()` 在连接时就是这样判断的。旧 bitstream 不能运行任何运算：主机会拒绝（`BitstreamError`），并提示重新生成；寄存器和数据缓冲的读写不受影响。
- 等待期间主机发来的字节会被丢弃，所以主机必须等到应答（或超时）后再发下一帧。

---

## 5. 一次 Decaps 的完整流程

数据缓冲的布局（`cpa_toolkit/config.py`）：

| 区域 | 偏移 | 长度 |
| ---- | ---- | ---- |
| dk（解封装私钥） | `IN_BASE = 0x0000` | 2400 |
| c（密文） | `IN_BASE + 2400 = 0x0960` | 1088 |
| K（共享密钥，输出） | `OUT_BASE = 0x0E00` | 32 |

**一次性配置**：连接后，以及每次重发 dk 时都会写一遍。这些寄存器会保持，不用每条 trace 都写：

```
0x02 SEC_LEVEL     = 768
0x02 OP_MODE       = 2        (Decaps)
0x02 DATA_IN_ADDR  = 0x0000
0x02 DATA_IN_LEN   = 3488
0x02 DATA_OUT_ADDR = 0x0E00
0x03 写 dk（10 帧 × ≤256 B）
```

**每条 trace**（只换密文）：

```
示波器 arm（等待 trig_out 上升沿）
0x03 写 c 到 0x0960（5 帧：4 × 256 + 64）
0x05 Run：CTRL ← START，输出 0x0E00 起 32 字节  →  STATUS / CYCLE_COUNT / ERROR_CODE / K
示波器读波形；主机把 K 与预先算好的期望值比较
```

采集程序（`acquire_cpa.run_campaign`）每 `resend_dk_every`（默认 50）条 trace 重发一次 dk，同时重写配置。如果某条 trace 失败或 K 不符，就丢弃缓存的配置，下一条 trace 前重写。这样即使中途按了 BTN0（寄存器复位，`OP_MODE` 变回 0），也能自动恢复。

### 5.1 示波器触发（`trig_out`）

**设计**：`trig_out` 直接由核心的 `STATUS.busy` 驱动。它是 `mlkem_ctrl` 中的一个寄存器，位于 206 MHz 核心时钟域，从寄存器到引脚之间没有任何组合逻辑。

- **上升沿**：核心接受 START 的那个时钟沿，也就是运算的第 0 个周期。`config.BMUL_WINDOWS` 中的周期数就是从这里开始计的。
- **高电平宽度**：正好 CYCLE_COUNT 个核心周期（Decaps 约 26 300 个，≈ 128 µs），之后回到低电平。
- **只在真正开始运算时出现**：寄存器读写、数据缓冲上传、`0x05` 探测都不会产生触发；参数错误时核心直接置 error、从不 busy，也不会触发。采集中每个触发都对应一次 Decaps。
- **对齐精度**：触发沿和运算锁定在同一个时钟上，两者之间只有固定的引脚延迟，与 UART、协议桥、CDC 的时序都无关。条与条之间剩下的错位只来自示波器自身的采样量化（≤ 1 个采样点；412.5 MSa/s 时为 2.4 ns，比 1 个核心周期 4.85 ns 还短）。
- **不干扰测量**：引脚为 LVCMOS33、慢速摆率。它只在开始和结束各翻转一次，而第一个 BMUL 窗口在触发后约 6.25 µs（第 1289 周期）才开始，所以翻转不会落在攻击窗口里。

**RTL 验证**（`make sim-bridge`，真实核心）：

- 每次 Decaps 恰好 1 个上升沿；
- 脉宽 = CYCLE_COUNT（26 363 个核心周期）；
- 探测、寄存器写、缓冲上传、参数错误时都是 0 个上升沿。

**上板检查**：GUI → Acquisition → *Check trigger*（`/api/acquire/trigger_check`）依次做三项检查，每项给出 PASS / FAIL 和处理建议：

1. **示波器已 arm，只有寄存器和缓冲读写、没有 START**：示波器**不能**触发。若触发了，说明示波器处于 AUTO 模式、触发源选错，或触发电平被噪声越过。
2. **示波器已 arm，执行一次 Decaps（`0x05`）**：示波器**必须**触发。若没有，检查探头是否接在 JA1 / JA3（地接 Pmod 的 GND 引脚）、触发源是否是这个通道、是否为上升沿、电平是否约 1.6 V。
3. **采到的波形覆盖全部三个 BMUL 窗口**：触发后至少约 23 µs。

示波器设置：NORMAL / SINGLE 模式（不要用 AUTO），上升沿，电平约 1.6 V。也可以用脉宽触发（> 100 µs）来排除其他所有信号。

---

## 6. 寄存器表（`pqc_axi_csr.sv`）

| 地址 | 名称 | 访问 | 说明 |
| ---- | ---- | ---- | ---- |
| `0x00` | CTRL | WO | bit0 START 脉冲，bit1 RESET 脉冲（读为 0） |
| `0x04` | STATUS | RO | bit0 busy，bit1 done（保持到下次 START/RESET），bit2 error |
| `0x08` | ALG_ID | RO | 1 = ML-KEM |
| `0x0C` | SEC_LEVEL | RW | 复位值 768（核心只支持 768） |
| `0x10` | OP_MODE | RW | 0 KeyGen，1 Encaps，2 Decaps（复位值 0） |
| `0x14` | CYCLE_COUNT | RO | 上一次运算的核心时钟周期数 |
| `0x18` | VERSION | RO | `主.次.补丁` 各 8 位 |
| `0x1C` | ERROR_CODE | RO | 0 无；1 SEC_LEVEL/OP_MODE 不支持；2 ek 模数检查失败；3 非法微码 |
| `0x20` | DATA_IN_ADDR | RW | 输入在缓冲中的字节偏移（4 的倍数） |
| `0x24` | DATA_IN_LEN | RW | 输入长度（仅记录） |
| `0x28` | DATA_OUT_ADDR | RW | 输出偏移（复位值 0x1800；采集时设为 0x0E00） |
| `0x2C` | DATA_OUT_LEN | RO | 输出长度 |
| `0x4000–0x7FFF` | 数据缓冲 | RW | 由 `0x03/0x04` 以偏移访问，不直接用总线地址 |

各运算的输入输出（输入放在 `DATA_IN_ADDR`，输出在 `DATA_OUT_ADDR`）：

| OP_MODE | 输入 | 输出 |
| ------- | ---- | ---- |
| 0 KeyGen | d ‖ z（64 B） | ek ‖ dk（1184 + 2400 B） |
| 1 Encaps | ek ‖ m（1184 + 32 B） | c ‖ K（1088 + 32 B） |
| 2 Decaps | dk ‖ c（2400 + 1088 B） | K（32 B） |

---

## 7. 吞吐量

下表是每条 trace 的 UART 线上时间（只换密文、配置已缓存），按“TX + RX 字节数 × 10 bit / 波特率”计算：

| | 往返次数 | 线上字节 | 115200 | 1 Mbaud |
| - | -------: | -------: | -----: | ------: |
| 已移除的旧流程（每条 trace 写 6 个寄存器、轮询 STATUS、读 3 个寄存器、再读 K），仅作对比 | 16 | ≈ 1516 | ≈ 132 ms | ≈ 15 ms |
| 新流程（5 帧密文 + 1 帧 `0x05`） | **6** | ≈ 1276 | ≈ 111 ms | ≈ 13 ms |

- 往返次数从 16 降到 6（`tests/test_uart_target.py` 中有断言）。每次往返还要加上 USB 延迟：FT2232H 的 latency timer 最长 16 ms，Linux 下可以通过 `/sys/bus/usb-serial/devices/ttyUSB*/latency_timer` 调到 1 ms。所以在实际系统里，往返次数往往比线上字节数更影响速度。
- 每 50 条 trace 重发一次 dk 和配置，约 2.8 KB，在 115200 下摊到每条约 5 ms。
- 在 115200 下，1088 字节的密文本身就要约 94 ms，这是上限。要采几万条，建议把波特率提高到 1 Mbaud（第 2 节）。之后瓶颈一般是示波器读波形。
- 核心本身的 Decaps 约 26 300 周期 ÷ 206.25 MHz ≈ 128 µs，可以忽略。密钥固定时周期数恒定；不同密钥因 ρ 的拒绝采样不同而略有差异。

---

## 8. 与论文中 SimpleSerial 命令的对应关系

Kamucheka 等人在 *A Masked Pure-Hardware Implementation of Kyber Cryptographic Algorithm*（IACR ePrint）的 Table II 中使用了 ChipWhisperer SimpleSerial 1.1 风格的命令。他们的结构是 PC 通过 UART 发命令给 MicroBlaze，MicroBlaze 解释命令并配置 Kyber IP，IP 内部 FSM 执行算法，示波器由独立的 FPGA trigger 触发。本项目的结构相同，只是解释命令的是硬件协议桥，没有用软核 CPU。

| 论文命令 | 作用 | 本协议 | Python（`uart_target.Target`） |
| -------- | ---- | ------ | ------------------------------ |
| `x` Reset | 复位设备/控制逻辑 | `0x02` CTRL ← 2 | `reset()` |
| `e` Enable | 使能设备 | 没有一一对应的命令（见下） | — |
| `m` Set Mode | 选择运行模式 | `0x02` SEC_LEVEL、OP_MODE、DATA_* | `configure(op)` |
| `s` Send Secret Key | 发送私钥 | `0x03` 写 dk 到 `IN_BASE` | `load_secret_key(dk)` |
| `c` Send Ciphertext | 发送密文 | `0x03` 写 c 到 `IN_BASE + 2400` | `load_ciphertext(ct)` |
| — | 启动运算并等待完成 | **`0x05` Run**（CTRL ← START，完成后回复） | `run()` |
| `a` Get Shared Secret | 读取共享密钥 | `0x05` 应答中直接带回 K；也可单独 `0x04` | `run()` 的返回值 / `read_shared_secret()` |
| — | 读状态 | `0x01` STATUS（`0x05` 状态块中已包含） | — |

`decaps(dk, ct, resend_dk)` 就是按顺序调用 `configure → load_secret_key → load_ciphertext → run`。

两点区别：

- **`e` 不等于 START**。论文只把 `e` 定义为 Device Enable，仅凭 Table II 不能说明它就是每次运算的启动脉冲。本协议不设“使能”状态，每次运算由 `0x05` 显式启动，并在完成时回复。
- **帧格式**。SimpleSerial 1.1 用 ASCII 十六进制（每字节占 2 个字符，以换行结尾），没有 CRC。本协议用二进制帧加 CRC-32，同样的密文只需一半的线上时间，传输错误也能被发现并自动重同步。

### 关于 “Zynq PS 通过 AXI 直接控制 PL”

对 Arty Z7，另一种做法是让 PS（Cortex-A9）解释命令，经 M_AXI_GP0 直接写 PL 中核心的寄存器和缓冲，从而省掉 PL 里的 UART 桥。这需要：

- 在 block design 中打开 M_AXI_GP0 和 FCLK；
- 加 AXI 协议转换，并经 `axil_cdc` 接到核心（核心在 206 MHz 时钟域）；
- 把核心的 `irq` 接到 IRQ_F2P；
- 重写 PS 程序。

如果 PS 程序实现**同一套帧格式**，主机代码可以完全不变。它的好处是：运算期间 CPU 可以 WFI 等中断，比现在一直在轮询的字节转发程序更安静。代价是需要修改并重新验证 Z7 的 block design。

目前的方案是三块板共用同一个 PL 协议桥：Z7 PS 版只做字节转发，`0x05` 在三块板上的行为完全一致。如果要做最低噪声的测量，仍然推荐使用纯 PL 版（外接 USB-UART），这时板上没有任何 CPU 在运行。

---

## 9. 验证

| 测试 | 命令 | 覆盖内容 |
| ---- | ---- | -------- |
| 串口链路（Icarus） | `make -C testbench/iverilog uart-link`（加 `BAUD=1000000` 更快） | 8N1 收发；`0x01–0x04`；CRC 错误；`0x05`：探测立即返回、运算结束后才回复、非对齐输出、参数错误、越界参数、核心挂死时超时回复、**核心 busy 期间 FPGA TX 线无跳变** |
| 协议桥 + CDC + 真实核心（Verilator） | `make -C testbench/cdc bridge` | 完整采集流程：探测、配置、上传 dk ‖ c、`0x05` 跑 ACVP Decaps 向量，K 与 NIST 一致；第二次只传 c 得到相同周期数；运算期间无字节发出；OP_MODE=3 报错；触发（`trig_out` = busy）每次 Decaps 恰好 1 个上升沿、脉宽 = CYCLE_COUNT，其他操作不触发 |
| 主机驱动（Python） | `uv run python -m unittest tests.test_uart_target -v` | 在软件板模型上：运算只走 `0x05`，从不读 STATUS；旧 bitstream 被拒绝且不上传任何数据；每条 trace 6 次往返；BTN0 复位后自动恢复配置；参数错误；ACVP 脚本的 UART 目标同样只用 `0x05` |
| 上板 | GUI → Connection → Preflight | 最后一项 “completion notice (0x05 Run)” 显示当前 bitstream 是否支持完成通知 |
| 上板，逐步执行 | GUI → Advanced → Protocol steps | 按 x / m / s / c / e / a 逐条发命令，查看每一步的应答 |
| 上板，触发 | GUI → Acquisition → *Check trigger* | 见第 5.1 节的三项检查 |

修改 `uart_axi_bridge.sv` 后需要重新生成 bitstream，板子才会有 `0x05`。在此之前，板子上的旧 bitstream 只能做寄存器和缓冲读写，所有运算都会被主机拒绝并提示重新生成。
