# Icarus Verilog 仿真指南：用 VCD 波形证明 UART 与 NTT 核心工作正常

本文说明怎样用 iverilog 跑 UART 链路和 ML-KEM NTT 核心的 testbench，并逐项说明**在 VCD 波形里看哪些信号、在哪个时间点、应该看到什么值**，来证明功能确实实现了。

文中的时间点和数值都来自默认参数（100 MHz 时钟、115200 波特率）下的实际仿真结果，打开波形后可以直接跳到对应时刻核对。

---

## 1. 快速开始

```bash
# 需要 Icarus Verilog 12 及以上（brew install icarus-verilog）
make sim-iverilog                         # 跑 UART + NTT 两个 testbench
make -C testbench/iverilog uart           # 只跑 UART（一个字节 tx → rx，约 0.1 秒）
make -C testbench/iverilog ntt            # 只跑 NTT
```

输出都在 `build/sim/` 下：

| 文件 | 内容 |
|---|---|
| `tb_uart.vcd` | UART 波形（约 450 KB，仿真 89 µs） |
| `tb_uart.log` | 最后一行是 `TB_UART PASS` |
| `tb_ntt.vcd` | NTT 波形（约 4 MB） |
| `tb_ntt.log` | 最后一行是 `TB_NTT PASS` |

预期的终端输出（节选）：

```
  82715 ns: uart_rx valid, data = 0xa5
TB_UART PASS
...
  NTT  on slot 1 finished in 3585 cycles
  INTT on slot 5 finished in 4609 cycles
=== tb_ntt: 7 checks, 0 errors ===
TB_NTT PASS
```

编译时会出现几行 `sorry: constant selects in always_* processes are not fully supported`，这是 iverilog 对敏感列表的提示，不影响仿真结果，可以忽略。

### 1.1 查看 VCD 的工具

任选一个：
- **GTKWave**：`brew install --cask gtkwave`（Linux 用 `apt install gtkwave`），然后 `gtkwave build/sim/tb_ntt.vcd`
- **Surfer**：https://surfer-project.org ，有桌面版，也可以在浏览器里直接打开 VCD
- **VS Code 插件**：VaporView 或 WaveTrace，在编辑器里直接打开 `.vcd`

几个设置技巧：
- `test_name` 是 ASCII 字符串（`reg [8*24-1:0]`），把显示格式设成 **ASCII**（GTKWave：右键 → Data Format → ASCII），就能看到当前在跑哪个测试，例如 `T3_ntt_random`（NTT testbench 中）。
- 状态机的 `state` 是枚举，VCD 里记录的是数字，对照第 2 节的编码表看。
- 数据信号用 **Hex** 显示；`checks`、`errors`、`op_cycles`、`chk_idx` 用 **Decimal** 显示。

---

## 2. 状态机编码表

| 模块 | 信号 | 编码 |
|---|---|---|
| `uart_rx` / `uart_tx` | `state` | 0 IDLE, 1 START, 2 DATA, 3 STOP |
| `uart_axi_bridge` | `state` | 0 RX_CMD, 1 RX_ADDR, 2 RX_LEN, 3 RX_DATA, 4 RX_CRC, 5 EXEC, 6 AXI_WR, 7 AXI_WR_RESP, 8 AXI_RD, 9 AXI_RD_RESP, 10 TX_PREP, 11 TX_SEND, 12 TX_HOLD |
| `uart_axi_bridge` | `phase` | 0 PH_HDR, 1 PH_DATA, 2 PH_CRC |
| `mlkem_poly_alu` | `state` | 0 S_IDLE, 1 S_ISSUE, 2 S_LATCH, 3 S_LATCH_C, 4 S_EXEC, 5 S_WRITE |

---

## 3. UART：一次传输（uart_tx → uart_rx）

`testbench/iverilog/tb_uart.sv` 只做一件事：用板级参数（100 MHz、115200 8N1）让 `uart_tx` 发送 **1 个字节 0xA5**，串口线直接接到 `uart_rx`，收到后和发送值比较。整帧 10 位 × 8.68 µs，**仿真约 89 µs 就结束**，VCD 只有约 450 KB，方便截图作为 evidence。

```
tx_data=0xA5, tx_valid ─▶ u_tx ──line──▶ u_rx ─▶ rx_data, rx_valid
```

选 0xA5 = `1010_0101` 是因为它不对称：LSB 先发，线上依次是 **1 0 1 0 0 1 0 1**，一眼就能看出位序对不对（0x55 这种对称值看不出来）。

终端输出：

```
=== tb_uart: 100000000 Hz, 115200 baud (868 clk/bit), byte 0xa5 ===
  200 ns: uart_tx accepts 0xa5
  82715 ns: uart_rx valid, data = 0xa5
TB_UART PASS
```

### 3.1 要添加的信号

按从上到下的顺序添加，截图时最清楚：

| 信号 | 显示格式 | 作用 |
|---|---|---|
| `tb_uart.tx_valid` | 二进制 | 发送请求脉冲 |
| `tb_uart.tx_data` | Hex | 要发送的字节 |
| `tb_uart.tx_ready` | 二进制 | 发送器空闲 |
| `tb_uart.u_tx.state` | Decimal | 0 IDLE / 1 START / 2 DATA / 3 STOP |
| `tb_uart.u_tx.bit_idx` | Decimal | 正在发送第几位 |
| `tb_uart.line` | 二进制 | **串口线本身** |
| `tb_uart.u_rx.state` | Decimal | 接收状态机 |
| `tb_uart.rx_sample` | 二进制 | 接收器对数据位的采样时刻（testbench 引出的观察信号） |
| `tb_uart.u_rx.shift_reg` | Hex | 接收移位寄存器，逐位填入 |
| `tb_uart.rx_valid` | 二进制 | 接收完成脉冲 |
| `tb_uart.rx_data` | Hex | 收到的字节 |

先 Zoom Fit 看完整的 0 ~ 89 µs，再放大到 200 ns 附近看发送启动，放大到 82.7 µs 附近看接收完成。

### 3.2 在波形上应该看到什么

**① 发送启动（200 ns 附近，放大到几十 ns）**

| 时刻 | 信号 | 值 |
|---|---|---|
| 200 ns | `tx_valid` | 1（一个时钟周期），`tx_data = 0xA5` |
| 205 ns | `u_tx.state` / `tx_ready` | 1 (START) / 0 |
| 215 ns | `line` | 1 → 0，**起始位开始** |
| 245 ns | `u_rx.state` | 1 (START)：接收器检测到起始位（经过 2 级同步器，晚 3 个周期） |

**② 串口线上的 10 位（215 ns ~ 87015 ns）**

每位宽度 868 个时钟 = **8680 ns**。用两个 marker 量任意相邻两个边沿的间距，应是 8680 ns 的整数倍。

| 位 | 时间段 | `line` | `u_tx.bit_idx` |
|---|---|---|---|
| 起始位 | 215 ~ 8895 ns | 0 | — |
| bit0 | 8895 ~ 17575 ns | **1** | 0 |
| bit1 | 17575 ~ 26255 ns | **0** | 1 |
| bit2 | 26255 ~ 34935 ns | **1** | 2 |
| bit3 | 34935 ~ 43615 ns | **0** | 3 |
| bit4 | 43615 ~ 52295 ns | **0** | 4 |
| bit5 | 52295 ~ 60975 ns | **1** | 5 |
| bit6 | 60975 ~ 69655 ns | **0** | 6 |
| bit7 | 69655 ~ 78335 ns | **1** | 7 |
| 停止位 | 78335 ~ 87015 ns | 1 | — |

bit0 ~ bit7 读出来是 1 0 1 0 0 1 0 1，从 bit7 往 bit0 反过来写就是 `1010_0101` = **0xA5**，说明发送器按 LSB 先发、8N1 格式工作。

**③ 接收器在每位中点采样**

`rx_sample` 在每个数据位里出现一个窄脉冲，位置都在该位的**中点附近**（例如 bit0 的中点是 13235 ns，采样在 13265 ns），离两侧的边沿都有约 4.3 µs 的余量，这就是可靠采样的依据。

每次采样后 `u_rx.shift_reg` 从低位开始填入：

| 采样时刻 | 采到的位 | `shift_reg` |
|---|---|---|
| 13265 ns | bit0 = 1 | 0x01 |
| 21945 ns | bit1 = 0 | 0x01 |
| 30625 ns | bit2 = 1 | 0x05 |
| 39305 ns | bit3 = 0 | 0x05 |
| 47985 ns | bit4 = 0 | 0x05 |
| 56665 ns | bit5 = 1 | 0x25 |
| 65345 ns | bit6 = 0 | 0x25 |
| 74025 ns | bit7 = 1 | **0xA5** |

**④ 接收完成（82715 ns）**

| 时刻 | 信号 | 值 |
|---|---|---|
| 74035 ns | `u_rx.state` | 3 (STOP) |
| 82715 ns | `rx_valid` | 1（一个时钟周期） |
| 82715 ns | `rx_data` | **0xA5** = 发送值 ✓ |
| 87015 ns | `tx_ready` | 回到 1，发送器可以接收下一个字节 |

`rx_valid` 出现在停止位中点（78335 + 4340 ≈ 82675 ns，加上同步器延迟）。停止位确认为高之后才输出数据，所以这一个脉冲同时证明了帧格式正确。

**截图建议**：一张 0 ~ 89 µs 的全景图（能看到 `line` 上完整的 10 位、8 个 `rx_sample` 脉冲、`shift_reg` 逐步变成 A5、最后 `rx_valid` 和 `rx_data = A5`），再配一张 82.7 µs 附近的放大图，就足够作为 UART 收发功能的 evidence。

### 3.3 换一个字节试试

```bash
cd build/sim
iverilog -g2012 -P tb_uart.TX_BYTE=8\'h3C -s tb_uart -o t.vvp \
  ../../testbench/iverilog/tb_uart.sv ../../hdl/core/uart_tx.sv ../../hdl/core/uart_rx.sv && vvp -n t.vvp
```

注意这会覆盖 `build/sim/tb_uart.vcd`。重新 `make -C testbench/iverilog uart` 就恢复为 0xA5 的波形，3.2 节的时间表对应的是 0xA5。


## 4. NTT：测试结构

**被测对象是 `mlkem_poly_alu` + `mlkem_polyram`**，也就是 `pqc_mlkem_top.sv` 里真正实例化、上板运行的 NTT 数据通路（FIPS 203 Algorithm 9/10，q = 3329）。

```
TB ──端口 A（加载 / 读回，tb_own=1 时）──▶ mlkem_polyram ◀── 端口 A/B ── mlkem_poly_alu
                                                       （运算时 tb_own=0）
```

- RAM 每个 24 位字存两个系数：`word {slot, w} = {f[2w+1], f[2w]}`，高 12 位是奇数下标。
- testbench 内部实现了一个 FIPS 203 Algorithm 9（NTT）和 Algorithm 10（NTT⁻¹）的行为模型，zeta 由 testbench 自己按 17^BitRev7(i) mod 3329 计算，**不使用 RTL 的常数 ROM**，所以两边是独立实现，结果互相印证。

| 测试 | `test_name` | 内容 |
|---|---|---|
| T1 | `T1_ntt_delta` | f(X) = 1 做 NTT，结果应为 (1, 0, 1, 0, …) |
| T2 | `T2_ntt_ramp` | f[i] = i 做 NTT，和参考模型逐系数比对 |
| T3 | `T3_ntt_random` | 随机多项式放在 slot 5，做 NTT，和参考模型比对 |
| T4 | `T4_intt_roundtrip` | 对 T3 的结果做 INTT，既和参考模型比对，也要求等于原始 f |
| T5 | `T5_guard_slots` | T3 之前往 slot 4 和 slot 6 填了固定图案，确认运算没有越界写到相邻 slot |

---

## 5. NTT：在波形上怎么看

**要添加的信号**：
- 控制：`test_name`（ASCII）、`tb_own`、`start`、`op`、`slot`、`done`、`op_cycles`（Decimal）
- ALU 内部：`u_alu.state`、`u_alu.layer`、`u_alu.item`、`u_alu.jw`、`u_alu.jb`、`u_alu.zeta_idx`、`u_alu.zeta_r`、`u_alu.a0`、`u_alu.a1`、`u_alu.b0`、`u_alu.b1`、`u_alu.m0_r`、`u_alu.m1_r`、`u_alu.wa`、`u_alu.wb`、`u_alu.scale`
- RAM 端口：`u_alu.ra_addr`、`u_alu.rb_addr`、`u_alu.ra_we`
- 比对：`chk_valid`、`chk_idx`（Decimal）、`chk_got`、`chk_exp`、`chk_err`

### 5.1 全局：7 层蝶形、周期数和理论值一致

缩到能看到整个 T2（41 µs ~ 82 µs）：

| 时刻 | 事件 |
|---|---|
| 42.410 µs | `start` 脉冲，`op = 0`（NTT），`slot = 1`，`tb_own` 变 0 |
| 42.415 µs | `layer = 0` |
| 47.535 / 52.655 / 57.775 / 62.895 / 68.015 / 73.135 µs | `layer` 依次变为 1 ~ 6 |
| 78.255 µs | `done` 脉冲（一个周期） |

每层正好 5.12 µs = 512 个周期 = 64 个蝶形步 × 8 个周期。每一步是 ISSUE、LATCH、5 个 EXEC（乘法器 4 级流水）、WRITE，`u_alu.state` 按 1 → 2 → 4 ×5 → 5 循环。每步同时处理两个系数，64 步覆盖 128 对蝶形，也就是一整层。

- NTT：7 层 × 64 步 × 8 周期 = 3584 周期，加上启动周期，log 里为 **3585**。
- INTT（T4，125.74 µs 起）：7 层之后 `u_alu.scale` 变为 1，再做一遍乘 128⁻¹ = 3303 的缩放（128 字 × 8 周期 = 1024），共 **4609** 周期，`done` 在 171.825 µs。

### 5.2 单个蝶形：可以手算核对（T2 第一步，42.415 µs ~ 42.495 µs）

这一段放大到每个时钟都能看清。T2 的输入是 f[i] = i，第 0 层第 0 步处理 (f[0], f[128]) 和 (f[1], f[129]) 两对，zeta = zetas[1] = 1729。

| 时刻 | 信号 | 值 | 解释 |
|---|---|---|---|
| 42.415 µs | `state` | 1 (ISSUE) | `ra_addr = 0x080` = {slot 1, 字 0}，`rb_addr = 0x0C0` = {slot 1, 字 64}，`zeta_idx = 1` |
| 42.425 µs | `state`, `zeta_r` | 2 (LATCH), 0x6C1 | 0x6C1 = **1729** ✓ |
| 42.435 µs | `a0, a1, b0, b1` | 0x000, 0x001, 0x080, 0x081 | f[0]=0, f[1]=1, f[128]=128, f[129]=129 ✓ |
| 42.435 µs | `state` | 4 (EXEC) | 乘法器开始 |
| 42.475 µs | `m0_r`, `m1_r` | 0x63E, 0xCFF | 128×1729 mod 3329 = **1598**，129×1729 mod 3329 = **3327** ✓（正好是 4 周期延迟） |
| 42.485 µs | `state`, `ra_we` | 5 (WRITE), 1 | 写回 |
| 42.485 µs | `wa` | 0xD0063E | {f[1]′, f[0]′} = {1+3327 = **3328**, 0+1598 = **1598**} ✓ |
| 42.485 µs | `wb` | 0x0036C3 | {f[129]′, f[128]′} = {1−3327+q = **3**, 0−1598+q = **1731**} ✓ |
| 42.495 µs | `jw`, `jb` | 1, 0x41 | 进入下一步：字 1 和字 65 |

可以用 Python 核对：

```bash
python3 -c "q=3329; print(128*1729%q, 129*1729%q, hex(((1+129*1729)%q)<<12|(128*1729%q)), hex(((1-129*1729)%q)<<12|((-128*1729)%q)))"
# 1598 3327 0xd0063e 0x36c3
```

这一步说明：寻址（`jw` / `jb` / slot）、zeta 查表、Barrett 模乘和模加减都是对的。

### 5.3 逐系数比对：`chk_err` 全程为 0

每次 `done` 之后，`tb_own` 回到 1，testbench 通过 RAM 端口 A 读回 128 个字（能看到 `u_ram.a_addr` 从 {slot, 0} 递增到 {slot, 127}），然后每个时钟比较一个系数：

- `chk_valid = 1` 持续 256 个周期
- `chk_idx` 从 0 递增到 255
- `chk_got`（DUT 结果）和 `chk_exp`（参考模型）在每个周期都相等
- **`chk_err` 始终为 0**：这就是“256/256 coefficients match”在波形上的对应

值得看的几段：
- **T1**：`chk_got` 在 1、0 之间交替（1, 0, 1, 0, …）。这是 NTT(1) 的特征：常数多项式对 128 个二次因子 X² − ζ 取模，每个余式都是 1 + 0·X。
- **T4 的第二次比对**：`chk_exp` 就是 T3 的原始随机输入，`chk_got` 和它完全一致，说明 INTT(NTT(f)) = f，正反变换互逆。
- **T5**：`chk_got` 在 0x5A5 / 0xA5A 之间交替，说明相邻 slot 没被改写。

### 5.4 用 `errors` 做最终确认

把 `errors`（Decimal）加到波形最上面。它在整个仿真过程中始终是 **0**；`checks` 最终为 7。只要有一项检查失败，`errors` 会在失败的那个时刻加 1，从波形上可以直接定位到出错位置。

---

## 6. 故障注入：证明 testbench 真的能发现错误

一个任何设计都能通过的 testbench 什么也证明不了。下面两个实验故意改坏 RTL，确认 testbench 会报 FAIL（在临时拷贝上做即可，不要提交）：

| 注入的错误 | 结果 |
|---|---|
| `mlkem_zetas.sv` 中把 zeta[17] 改成 1 | T2/T3 各有 16 个系数错，T4 有 128 个错，`TB_NTT FAIL`。T1 仍然通过：输入 f = 1 时，每次和 zeta 相乘的那个操作数 f[j+len] 恰好都是 0，zeta 错了也看不出来。所以 T1 单独不够，还需要 T2/T3 这种非零输入 |
| `uart_axi_bridge.sv` 中把地址按大端序拼接 | 完整链路测试（见 7.4）B1 写到了错误的寄存器，B2 读回 0，`TB_UART_LINK FAIL` |

---

## 7. 附注

### 7.1 为了兼容 iverilog 对 RTL 做的改动

iverilog 不支持以下 SystemVerilog 写法，已改成功能等价的写法（Vivado / Verilator 下行为不变）：
- `uart_axi_bridge.sv`：`state inside {…}` 改为逐个 `==` 比较；两处给枚举赋三目表达式的地方改为 `if/else`
- `mlkem_poly_alu.sv`：一处给枚举赋三目表达式的地方改为 `if/else`

### 7.2 关于 `ntt_engine.sv`

`hdl/core/ntt_engine.sv` / `ntt_butterfly.sv` 是早期的通用 NTT 引擎，**没有被 `pqc_mlkem_top` 实例化**，所以本指南不覆盖它。仿真发现它有一个会导致死循环的 bug：`N[LOG_N-1:0]` 在 N = 256、LOG_N = 8 时截断为 0，`half_size` 恒为 0，`start_ntt` 之后 20000 个周期都没有 `done`。如果以后要用它，需要先修复这一处。

### 7.3 文件

| 路径 | 说明 |
|---|---|
| `testbench/iverilog/tb_uart.sv` | UART 单字节收发 testbench（evidence 用） |
| `testbench/iverilog/tb_uart_link.sv` | UART + 协议桥完整链路 testbench（可选，运行时间长） |
| `testbench/iverilog/tb_ntt.sv` | NTT/INTT testbench，含 FIPS 203 参考模型 |
| `testbench/iverilog/Makefile` | `uart` / `ntt` / `uart-link` / `clean` 目标 |
| `build/sim/` | VCD、log、编译产物（已被 .gitignore 忽略） |

### 7.4 可选：完整协议链路测试（`uart-link`）

`tb_uart_link.sv` 按 `arty_a7_top.sv` 的连接方式，把 `uart_rx → uart_axi_bridge → uart_tx` 串起来，由 testbench 充当 Go 上位机，按 `pkg/fpga/uart/serial.go` 的帧格式（小端序 + CRC-32）发送 6 条命令：写寄存器、读寄存器、读版本号、写数据缓冲、非对齐读数据、坏 CRC 拒绝。共 41 项检查，全部通过，同时验证了 CLAUDE.md known gap #4 提到的字节序问题。

运行时间长（115200 波特率下仿真约 15 ms，VCD 约 77 MB），所以不在默认目标里：

```bash
make -C testbench/iverilog uart-link                 # 115200，约 5 秒
make -C testbench/iverilog uart-link BAUD=1000000    # 1 Mbaud，VCD 约 8 MB
```
