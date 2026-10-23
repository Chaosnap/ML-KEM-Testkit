# SAKURA-X：ML-KEM-768 Decaps 移植

SAKURA-X（SASEBO-GIII）有两片 FPGA：Spartan-6 运行 USB 控制器，Kintex-7 运行 ML-KEM-768 核心。
Spartan-6 使用上游 [sakura-x-shell](https://github.com/hal-lab-u-tokyo/sakura-x-shell) 的预编译控制器
（`third_party/sakura_x_shell/`），Kintex-7 使用本项目的 `sakura_x_top`。

本移植只支持 Decaps。应用命令为 **RESET / LOAD_DK / LOAD_CT / START / READ_SS**。

GUI 的操作步骤见 [GUI_GUIDE.md](GUI_GUIDE.md)。

## 1. 架构

```text
PC / GUI
   │ USB，FT2232H A 通道（245 FIFO）
Spartan-6 shell 控制器（预编译 bit，CN10）
   │ 32 位地址/写数据 + 32 位读数据 + 握手，50 MHz 总线时钟，复位
Kintex-7（CN5）：MMCM 去偏斜 + IOB 寄存器
   │
sakura_x_bridge（50 MHz）── AXI-Lite → axil_cdc
   │
pqc_mlkem_top（206.25 MHz）── STATUS.busy → CN8 第 3 脚（示波器触发）

FT2232H B 通道 RTS_N → Spartan-6 控制器复位 → 板间复位 → Kintex-7 逻辑复位
```

| 部分 | 文件 / 设置 |
| ---- | ----------- |
| Spartan-6，CN10 | `third_party/sakura_x_shell/sakura-x-shell-ctrl/sakura-x-shell-ctrl.bit` |
| Kintex-7，CN5 | 本项目构建的 `sakura_x_top.bit` |
| FT2232H A，USB interface 00 | 245 FIFO：数据和命令 |
| FT2232H B，USB interface 01 | UART：只用 RTS_N 复位，不发送数据 |
| GUI | **Board** = **SAKURA-X — USB FIFO**；内部端口名 `sakura-x:<A 通道设备>` |

| 目录 / 文件 | 内容 |
| ----------- | ---- |
| `hdl/xilinx/sakura_x/` | `sakura_x_top.sv`、`sakura_x_bridge.sv`、`constraints.xdc` |
| `scripts/vivado/build_sakura_x.tcl` | Kintex-7 工程与 bit 构建（含时序门禁） |
| `scripts/vivado/program_sakura_x.tcl` | 通过 JTAG 烧录 Kintex-7 |
| `scripts/board/sakura_x_eeprom.sh` | FT2232H EEPROM 一键修正 / 恢复 |
| `scripts/board/sakura_x_cli.py` | 命令行：INFO、CHECK、五条命令、交互会话 |
| `cpa_toolkit/sakura_x.py` | 主机驱动（`ShellLink`、`ShellTarget`） |
| `testbench/sakura_x/` | 桥接器对接上游 `command_control.v` 的仿真（`make sim-sakura`） |

## 2. Linux 准备

### 2.1 Python 环境和权限

```bash
git clone git@github.com:Chaosnap/ML-KEM-Testkit.git
cd ML-KEM-Testkit
uv sync --extra scope
sudo usermod -aG dialout "$USER"     # 然后重新登录
```

### 2.2 FT2232H EEPROM

A 通道必须是 245 FIFO，B 通道必须是 UART。如果 A 通道是 UART，Spartan-6 不回应（GUI 显示
`No Spartan-6 controller replies`）。

关闭 GUI，然后执行：

```bash
bash scripts/board/sakura_x_eeprom.sh            # 修正（先自动备份）
bash scripts/board/sakura_x_eeprom.sh check      # 只读检查
bash scripts/board/sakura_x_eeprom.sh restore    # 写回第一次修改前的备份
```

修正后，断开 USB 线再连接。如果板子由 USB 供电（SW1 = USB），重新烧录两片 FPGA。

工具的细节：

- 第一次运行时把 pyftdi 装到 `~/ftdi-venv`，用 sudo 访问 USB。
- 备份在 `data/eeprom/`：`<序列号>_original.bin` 是第一次修改前的内容，不会被覆盖；每次运行另存一个带时间的备份。
  旧版脚本留在当前目录的 `ft2232h_<序列号>_*.bin` 会被自动找到并作为原始备份。
- 产品名同时改为 `SAKURA-X Shell`，GUI 据此自动选择板子（`--keep-name` 不改名）。
- Digilent（Arty）的 FT2232H 不会被修改。连接多块 SAKURA-X 时用 `--serial` 选择。
- SAKURA-X 的 EEPROM 是 93C46（128 字节）。pyftdi 默认按 256 字节处理会报
  `IndexError: bytearray index out of range`；工具按芯片实际大小打开，避免这个错误。

### 2.3 烧录 Spartan-6（CN10）

关闭占用下载器的 Vivado Hardware Manager、hw_server、OpenOCD。

```bash
sudo xc3sprog -c xpc -v -p 1 \
  third_party/sakura_x_shell/sakura-x-shell-ctrl/sakura-x-shell-ctrl.bit
```

`-p 1` 是 CN10 链上的 XC6SLX45（位置 0 是 XCF32P）。输出必须显示 `XC6SLX45`。
这条命令只写 SRAM，不写 XCF32P。断电后 Spartan-6 回到出厂控制器。

### 2.4 构建和烧录 Kintex-7（CN5）

```bash
vivado -mode batch -source scripts/vivado/build_sakura_x.tcl
vivado -mode batch -source scripts/vivado/program_sakura_x.tcl -tclargs \
    build/vivado/sakura-x_mlkem/pqc_mlkem_sakura_x.runs/impl_1/sakura_x_top.bit
```

只创建工程（在 GUI 中手动综合）：

```bash
vivado -mode batch -source scripts/vivado/build_sakura_x.tcl -tclargs --project-only
```

然后打开 `build/vivado/sakura-x_mlkem/pqc_mlkem_sakura_x.xpr`。如果是已有工程，先执行
**Tools → Run Tcl Script** → `scripts/vivado/configure_sakura_x.tcl`，再 Reset Run 并重新综合。

构建脚本在生成 bit 前检查布线后的 setup/hold。WNS 或 WHS 为负时不生成 bit。
报告在工程目录：`timing.rpt`、`cdc.rpt`、`shell_outputs_{max,min}.rpt`、`shell_inputs_{max,min}.rpt`。

**注意：** 两片 FPGA 都只写 SRAM。每次上电后，先烧 Spartan-6，再烧 Kintex-7，中间不要断电。

### 2.5 连接

```bash
make gui
```

按 [GUI_GUIDE.md](GUI_GUIDE.md) 第 3.1 节连接。每次 **Connect** 先用 B 通道 RTS 复位 Spartan-6 控制器，
然后在访问 Kintex-7 之前判断 Spartan-6 上运行的是哪个控制器：

| 结果 | 含义 |
| ---- | ---- |
| `The shell controller replies (0x02)` | shell 控制器正常，继续检查 Kintex-7 |
| `FACTORY controller` | Spartan-6 运行出厂 AIST 控制器（断电后的状态）。重新烧录 Spartan-6，再烧录 Kintex-7 |
| `No Spartan-6 controller replies` | Spartan-6 没有配置，或 A 通道不是 245 FIFO，或出厂控制器被锁死 |
| `RTS reset does not reach it` | shell 控制器可用，但卡住后只能断电恢复 |

判定顺序：RTS 复位 → 出厂读命令 `00 FF FC`（必须在任何 shell 帧之前发送，shell 帧的第一个字节会让出厂控制器进入 TRAP）
→ 再次 RTS 复位 → 未知命令探测（shell 控制器回 `0x02`，不产生板间事务）。

Spartan-6 LED：

| 设计 | LED |
| ---- | --- |
| shell 控制器 | LED17 约每 1.3 s 翻转；LED16 常亮 = 控制器停在事务中 |
| 出厂控制器 | LED11 每秒闪约 3 次 |
| 未配置 | 无闪烁 |

Kintex-7 LED：LED1 心跳，LED2 上次运算完成，LED3 出错，LED10 时钟锁定且复位已释放。

示波器触发：CN8 第 3 脚（2.5 V），GND 接 CN8 第 13 或 11 脚；上升沿，约 1.25 V。

## 3. 命令行工具

```bash
uv run python scripts/board/sakura_x_cli.py --list
uv run python scripts/board/sakura_x_cli.py --port /dev/ttyUSB0 INFO
uv run python scripts/board/sakura_x_cli.py --port /dev/ttyUSB0 CHECK
uv run python scripts/board/sakura_x_cli.py --port /dev/ttyUSB0 SESSION
```

`--port` 填 A 通道。`CHECK` 做 identity、1 KB 回读、合法 CT 与修改过的 CT 的 Decaps，并与软件参考 K 比较。
`SESSION` 打开交互提示符 `sakura>`：

```text
RESET
LOAD_DK /path/to/dk.bin
LOAD_CT /path/to/ct.bin
START
READ_SS /path/to/k.bin
QUIT
```

## 4. 五条命令

| 命令 | 行为 | 主机 API |
| ---- | ---- | -------- |
| `RESET` | 清除运算状态、输入完整标志、结果有效标志 | `target.reset()` |
| `LOAD_DK` | 写入 2400 字节 DK，不运算 | `target.load_dk(dk)` |
| `LOAD_CT` | 写入 1088 字节 CT，不运算 | `target.load_ct(ct)` |
| `START` | 输入完整后运行整个 Decaps，完成后回应 | `target.start()` |
| `READ_SS` | 读取 32 字节 K（仅在成功完成后） | `target.read_shared_secret()` |

- DK 可以在多条 trace 间复用。每次 START 使 CT 失效，下一次 START 前要再 LOAD_CT。
- RESET 后 DK 和 CT 都失效。部分或乱序写入不构成完整输入。
- 命令名是应用层名称。底层是上游的二进制读写帧，不要向端口发送 ASCII 字符串。
- 超时后不会自动重发 START。连接错误后在 GUI 点击 **Connect**，然后重新加载 DK/CT。

## 5. 运算期间静默

START 是一次延迟回应的读请求：

1. 主机发出请求，Spartan-6 回 ACK 并转发地址。
2. Kintex-7 等待 2 ms 静默间隔，然后内部写 CTRL.start；`trig_out` 上升。
3. Kintex-7 等待同步后的 busy 下降，不通过 AXI 轮询 STATUS。
4. busy 下降后再等待 quiet tail（默认 20 ms），然后回应。
5. 主机随后读取 CYCLE_COUNT、ERROR_CODE 和 K。

采集程序从示波器 metadata 取记录时长，加 5 ms 余量设置 quiet tail（至少 20 ms）。
静默的是应用数据传输；USB SOF、FPGA 时钟和 Spartan-6 心跳仍然存在。

## 6. 二进制接口（维护用）

上游 6 字节帧头（高位在前）：`[0x8:4][type:4][words-1:4][address:32][0x1:4]`。
读 type=1，写 type=2，每次 1..16 个 32 位字；地址按字节、4 字节对齐。
Spartan-6 先回 1 字节 ACK（0 = 帧头合法），读命令随后返回大端字。ACK 不表示写入已完成；主机用后续读作为写屏障。
协议没有 CRC。驱动按核心的小端字布局转换 DK/CT/K 字节。

| 地址 | 含义 |
| ---- | ---- |
| 0x0000..0x002C | 核心 CSR；禁止直接写 CTRL.start |
| 0x0100（读 1 字） | START；成功回应为 STATUS=2，bit31 = 桥接器故障 |
| 0x0104 | 0x534B0001（shell ABI） |
| 0x0108 | quiet tail，50 MHz 周期数，可读写（最少 2 ms） |
| 0x010C | 50,000,000（设计总线频率） |
| 0x0110 | 故障码：1 访问错误，2 AXI，3 超时，4 busy，5 输入不完整 |
| 0x0114 | bit0 = DK 完整；bit1 = CT 完整且未使用 |
| 0x0118 | 结果有效 |
| 0x4000..0x495F | DK，2400 字节 |
| 0x4960..0x4D9F | CT，1088 字节 |
| 0x4E00..0x4E1F | K，32 字节 |

## 7. 验证状态

| 项目 | 状态 |
| ---- | ---- |
| 73 根板间信号 | Spartan UCF 与 Kintex XDC 按规格书表 29 逐一核对，0 处不符 |
| 总线时钟 | 预编译 Spartan bit 的 DCM：24 MHz × 25 / 12 = 50 MHz，与 Kintex MMCM 一致 |
| 握手协议 | `make sim-sakura`：桥接器对接未修改的上游 `command_control.v` / `fifo.v` 通过；板间延迟 0.5–15 ns 通过 |
| 时序 | 总线时钟经 MMCM + BUFG 去偏斜，输入/输出全部 IOB 寄存；构建脚本阻止 setup/hold 为负的 bit |
| 实板 | 2026-10-23：EEPROM 修正（A = 245 FIFO）后，GUI 连接与运行在 Linux 实板上正常 |

RTL 仿真不含建立/保持时间；板级时序以 Vivado 布线后报告为准。
