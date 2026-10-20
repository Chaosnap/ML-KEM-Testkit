# SAKURA-X（SASEBO-GIII）移植与 LeCroy HDO6104 设置

ML-KEM-768 核心在 SAKURA-X 上运行于**密码 FPGA**：Kintex-7 XC7K160T-1FBG676C（U11）。控制 FPGA（Spartan-6 XC6SLX45，U17）必须运行 AIST 的 USB/local-bus 控制器。若无法确认其 Flash 内容，可先通过 CN10 加载参考包的 `sasebo_giii_ctrl.bit`，再通过 CN5 加载 Kintex-7 的 ML-KEM bitstream。`hdl/core/` 和主机协议（[UART_PROTOCOL.md](UART_PROTOCOL.md)）与 Arty 版完全相同。

| 文件 | 内容 |
| ---- | ---- |
| `hdl/xilinx/sakura_x/sakura_x_top.sv` | 时钟（200 MHz 差分 → MMCM）、复位、引脚、LED 映射 |
| `hdl/xilinx/sakura_x/sakura_lbus_bridge.sv` | 板载 USB 链路：local bus 寄存器 ↔ 字节流 |
| `hdl/xilinx/sakura_x/constraints.xdc` | 引脚约束，每个引脚都注明了出处 |
| `cpa_toolkit/sakura_lbus.py` | 主机端：把 local bus 包装成一个“串口”（端口名 `sakura:<设备>`） |
| `testbench/sakura/` | 桥接逻辑对 AIST 原版 Spartan-6 控制器源码的仿真（默认使用仓库内参考源码；可传 `SASEBO_DIR=<参考包路径>` 覆盖） |
| `scripts/vivado/build_sakura_x.tcl` | 独立的构建脚本，工程在 `build/vivado/sakura-x_mlkem/` |
| `scripts/vivado/program_sakura_x.tcl` | 经 JTAG（CN5）烧录，只认 Kintex-7 器件 |
| `scripts/ise/build_sakura_spartan6.tcl` | ISE 14.7 构建原版 Spartan-6 控制器，输出到 `build/ise/sakura-x_ctrl/` |
| `scripts/board/program_sakura_dual.tcl` | 普通 `tclsh` + openFPGALoader，单根 DLC10 依次加载 CN10、CN5 |

引脚来源：*SASEBO-GIII Specification* v1.1 第 3.1 节表 13–20、表 7、表 29；local bus 引脚和协议取自 AIST 的 SASEBO-GIII 参考包（最小子集位于 `third_party/aist_sasebo_giii/`：`sasebo_giii_ctrl/` 是出厂 Spartan-6 设计的源码，`sasebo_giii_aes/pin_sasebo_giii_k7.ucf` 是对应的 Kintex-7 引脚），并与规格书表 29 核对过。约束文件里每个引脚单独一行：XDC 只支持受限的 Tcl，`for` / `foreach` 会被 Vivado 拒绝（`[Designutils 20-1307]`），引脚就会没有约束，DRC 报 NSTD-1 / UCIO-1，生成不了 bitstream。

---

## 1. 主机链路：板载 USB（CN15）

板载 USB 上的 FT2232H 只连到 Spartan-6（规格书表 26）。Spartan-6 的出厂 bitstream（`sasebo_giii_ctrl`）把 FT2232H 通道 A（245 FIFO 模式）收到的字节翻译成两片 FPGA 之间 16 位 **local bus** 上的寄存器读写：

```
主机 → USB:  01 AH AL DH DL   写地址 AH:AL，数据 DH:DL（无应答）
             00 AH AL         读地址 AH:AL，应答 DH DL
```

Kintex-7 里的 `sakura_lbus_bridge` 用几个寄存器把它变回字节流（两个方向各一个 FIFO），再经内部的高速串行线（CLK/16 ≈ 6.4 Mbaud）接到原封不动的 `mlkem_uart_system`。主机端 `cpa_toolkit/sakura_lbus.py` 的 `LbusPort` 看起来就是一个 `serial.Serial`，所以 GUI、`acquire_cpa.py`、`acvp_test.py` 只需要换一个端口名：

| 系统 | 端口名 |
| ---- | ------ |
| Linux | `sakura:/dev/ttyUSB0`（板子的第一个 ttyUSB = 通道 A） |
| macOS | `sakura:/dev/cu.usbserial-XXXX0` |

GUI 的端口列表里会单独列出一项 “SAKURA-X board USB via the Spartan-6 local bus”。

| 寄存器 | 方向 | 作用 |
| ------ | ---- | ---- |
| 0x0010 / 0x0012 | 写 | 推入 1 / 2 个字节（主机 → FPGA） |
| 0x0030 | 写 | bit0 清空 FPGA → 主机 FIFO，bit1 清空主机 → FPGA FIFO |
| 0x0002 | 读 | FPGA → 主机待取字节数 |
| 0x0004 | 读 | 主机 → FPGA 剩余空间 |
| 0x0020 / 0x0022 | 读 | 取出 1 / 2 个字节 |
| 0xFFFC | 读 | ID 0x4D4B，连接时检查 |

**速度**：上传 1088 字节密文约 10 ms 量级，比 115200 波特 UART（~94 ms）快。

**侧信道注意**：应答靠轮询。每次写完之后，第一次轮询推迟 `quiet` = 2 ms（Decaps 约 128 µs），所以运算期间 USB、Spartan-6 和 local bus 上都没有主机流量。Spartan-6 自己的 3 MHz local bus 时钟（`lbus_clkn`，Kintex-7 管脚 AB11）是出厂设计一直在跑的，本设计不用它，但它会出现在 trace 的背景里；需要更干净的测量时，这是剩下的噪声源之一。

Linux 前提：`ftdi_sio` 驱动（内核自带）会把通道 A 识别成 `/dev/ttyUSB0`。用户需要在 `dialout` 组里（或者用 udev 规则放开权限）。`LbusPort` 会尝试把 FTDI 的 latency timer 设成 1 ms，失败也能用，只是慢一些。

SW3：Spartan-6 必须从它自己的 Flash 正常启动（出厂状态，上电后 LED11 心跳灯闪烁）。如果有人改写过 Spartan-6 的 Flash，用 AIST 参考包里的 `sasebo_giii_ctrl.mcs` 经 CN10 恢复。

### 1.0 连不上时（“got 0 of 2 reply bytes”）

GUI 连接时先读一次 ID 寄存器（发 `00 FF FC`，等 2 个字节）。Spartan-6 出厂设计对**每一次**读都会回 2 个字节，Kintex-7 是空的也一样，所以一个字节都没回，问题在 Kintex-7 之前：USB → FT2232H → Spartan-6。在 Linux 上运行：

```bash
uv run python scripts/board/sakura_link_check.py
```

它会列出板子的 FTDI 端口和 EEPROM 报告的名字，在通道 A 和 B 上各读一次 ID，然后告诉你：

| 结果 | 含义 |
| ---- | ---- |
| `OK … 0x4D4B` | 链路全通；GUI 选 `sakura:/dev/ttyUSB0` |
| Spartan-6 回了，但不是 `4D 4B` | FT2232H 和 Spartan-6 正常，Kintex-7 没有运行带 local bus 桥的 bitstream（重新烧；断电后会丢） |
| 通道 B 回了、A 没有 | 这块板的通道顺序不同，用 `sakura:/dev/ttyUSB1` |
| 都没回 | Spartan-6 出厂设计没有运行：看 LED11（Spartan-6 心跳，约每秒 3 次）；或 FT2232H 通道 A 不是 245 FIFO 模式：加 `--eeprom`（需要 pyftdi：`uv run --with pyftdi python …`，之后要重新插拔 USB） |

产品名 “Dual RS232-HS” 只能作为默认配置的线索，不能单凭字符串判断通道模式。先读取 EEPROM，确认通道 A 是否为 245 FIFO；模式不对时可用 AIST 的 `sasebo_giii.xml` 通过 FT_Prog（Windows）恢复。重新插拔 CN15 会断电，因此之后要再次加载两个 SRAM bitstream。

`/dev/ttyS0`…`ttyS31` 是 Linux 为主板的传统串口预留的设备节点，不管有没有硬件都会出现；GUI 现在把没有硬件的隐藏掉。`ttyUSB1` 是板上 FT2232H 的通道 B，出厂设计不用它。

### 1.1 备用：CN8 外接 2.5 V USB-UART

CN8.1（D19，FPGA 输入，带上拉）/ CN8.2（N17，FPGA 输出）仍然接着同一个系统（两条接收线相与，应答两边都发）。只在没有板载 USB 时用，一次只用一条链路。适配器必须是 **2.5 V** 电平：CN8 所在的 HR bank VCCO = 2.5 V，3.3 V 的 TXD 超出绝对最大值（VCCO + 0.55 V）。VCCIO 可以接 CN8.12（2.5 V），**不要**把适配器的电源输出接到 CN8.12。

---

## 2. 接线

### 2.1 CN8 引脚（单排 14 针）

```
  脚号:   1      2      3     4 … 10    11     12     13     14
        ┌──────┬──────┬──────┬────────┬──────┬──────┬──────┬──────┐
        │(RX)  │(TX)  │ TRIG │  ---   │ GND  │ 2.5V │ GND  │ VIN  │
        └──────┴──────┴──────┴────────┴──────┴──────┴──────┴──────┘
         D19    N17    N16              地     电源    地    危险！
```

示波器触发：探头针尖接 **CN8.3**（从上往下第 3 针），地接 **CN8.13**（从下往上第 2 针）或 CN8.11。CN8.14 是 VIN，什么都不要接。第 1 脚以丝印为准（CN8 那一列旁边印着 “1”）。

### 2.2 其他连接

| 连接 | 接到 |
| ---- | ---- |
| USB 线 | CN15（供电 + 主机链路），SW1 = USB |
| Platform Cable USB II | **CN5**（Kintex-7 JTAG，“Cryptographic FPGA Flash ROM Programming Connector”）。CN10 是 Spartan-6 的 JTAG，恢复控制器时接 CN10 |

### 2.3 开关

| 开关 | 设置 | 说明 |
| ---- | ---- | ---- |
| SW1 | USB 或 EXT | 电源（规格书表 11） |
| SW3.7 | **OFF**（出厂默认） | ON 会关掉 200 MHz 晶振，设计就没有时钟了 |
| SW3.1–3 | 出厂默认（QSG：只有 SW3.2 ON） | Kintex-7 配置模式（BPI）；JTAG 烧录不受影响 |
| SW4 | — | 本设计的复位键：按下时两个时钟域都复位，LED5 亮 |

---

## 3. 时钟

| 时钟 | 频率 | 来源 |
| ---- | ---- | ---- |
| `clk200` | 200 MHz | 晶振 U16（K_CLK_OSC1，AA3/AA2，MRCC，LVDS 输入） |
| `clk_core` | **206.25 MHz** | MMCM：200 × 4.125 / 4，VCO 825 MHz，整个 ML-KEM 核心 |
| `clk` | 103.125 MHz | MMCM：VCO / 8，UART、协议桥、LED |

206.25 MHz 与 `cpa_toolkit/config.py` 的 `CORE_MHZ` 相同（Arty Z7 也是这个频率），所以主机端的周期 ↔ 时间换算和 BMUL 窗口位置都不用改。

---

## 4. 构建与烧录

**另一台 Linux clone 后直接烧录：见 [SAKURA_X_LINUX.md](SAKURA_X_LINUX.md)。** 两个预编译固件均在 `firmware/sakura_x/`，默认命令不依赖 `build/` 或项目外目录。

### 两个独立构建、一个统一烧录入口

CN10 与 CN5 是两条独立 JTAG 链，不能把同一个 `.bit` 在两个口各烧一次。
Spartan-6 的源码构建需要 **ISE 14.7 / xtclsh**；Kintex-7 继续使用 Vivado。
统一烧录用 **普通 `tclsh` + openFPGALoader**，支持照片中的 Xilinx Platform Cable USB II（DLC10）；不要求 Vivado 能识别 Spartan-6。

先在安装 ISE 的电脑上构建控制器（默认使用仓库内参考源码，也可传入其他参考包目录）：

```bash
xtclsh scripts/ise/build_sakura_spartan6.tcl
```

脚本复制原版 `.xise`、RTL 和 UCF 到 `build/ise/sakura-x_ctrl/run-*/`，保留原版工程选项，成功后输出 `build/ise/sakura-x_ctrl/sasebo_giii_ctrl.bit`。
**排查通信时可跳过这一步**：参考包已经提供可直接加载的 `sasebo_giii_ctrl.bit`。仓库内的副本位于 `firmware/sakura_x/sasebo_giii_ctrl.bit`。

Kintex 的构建仍为：

```bash
vivado -mode batch -source scripts/vivado/build_sakura_x.tcl
```

关闭占用下载线的 Vivado Hardware Manager / iMPACT / XVC 进程，仅连接一根 DLC10，保持 CN15 供电。在仓库根目录的终端运行：

```bash
tclsh scripts/board/program_sakura_dual.tcl \
  firmware/sakura_x/sasebo_giii_ctrl.bit \
  build/vivado/sakura-x_mlkem/pqc_mlkem_sakura_x.runs/impl_1/sakura_x_top.bit
```

若使用重新构建的控制器，将第一个路径换为 `build/ise/sakura-x_ctrl/sasebo_giii_ctrl.bit`。clone 后直接使用预编译固件时，只需运行 `tclsh scripts/board/program_sakura_dual.tcl`。

1. 排线接 **CN10**，输入 `CN10`：脚本检查扫描链并加载 Spartan-6 控制器。
2. 第一步完成、脚本提示后，将排线换到 **CN5**，输入 `CN5`：加载 Kintex-7 的 ML-KEM。
3. **两步之间保持板卡供电**。两个 `.bit` 都是 SRAM 配置，断电丢失。脚本不写 Flash，也不修改 USB EEPROM。
4. 关闭 GUI 的板卡连接，运行 `uv run python scripts/board/sakura_link_check.py`；ID 应为 `0x4D4B`。然后 GUI 选 `sakura:<通道A端口>`。

末尾加 `--dry-run` 可只检查两个文件的器件型号、打印计划，不连接硬件。脚本不能单凭 `.bit` 型号证明内容是 ML-KEM；第二个文件必须来自本项目的 `sakura_x_top`，**不能用参考包里的 AES bitstream 替代**。

`openFPGALoader` 须预先安装并在 PATH 中。固定使用 `xilinxPlatformCableUsb` 和 3 MHz JTAG；CN10 含 XCF32P PROM，脚本显式注册其 IDCODE 和 16 位指令长度以便旁路，并根据扫描结果选择 FPGA 的索引。DLC10 固件/USB 权限仍须正常；绿灯不等于两个 FPGA 已配置成功。此入口为终端脚本，不是在 Vivado Tcl Console 中 `source` 的脚本。

手册 QSG 第 4 页的 `.mcs` 流程是在写配置 Flash，和上述 `.bit` 临时加载不同；只有需要掉电保留时再按手册用 iMPACT 恢复 `sasebo_giii_ctrl.mcs`。板载 USB 还要求 FT2232H 通道 A 为 245 FIFO，两片 FPGA 的 DONE 高也不能证明 USB 模式正确。

参考：两条链和配置介质见本地规格书第 9–12 页、QSG 第 4 页；[AMD 支持器件表](https://docs.amd.com/r/2025.1-English/ug973-vivado-release-notes-install-license/Supported-Devices)、[openFPGALoader 链扫描实现](https://github.com/trabucayre/openFPGALoader/blob/master/src/main.cpp)、[OpenOCD XCFxxP 链参数](https://github.com/openocd-org/openocd/blob/master/tcl/cpld/xilinx-xcf-p.cfg)。

### 只更新 Kintex-7（Spartan 控制器已正常运行）

```bash
vivado -mode batch -source scripts/vivado/build_sakura_x.tcl
vivado -mode batch -source scripts/vivado/program_sakura_x.tcl -tclargs build/vivado/sakura-x_mlkem/pqc_mlkem_sakura_x.runs/impl_1/sakura_x_top.bit
```

- **在 Vivado GUI 里**（Tcl Console）：构建完直接 `source <仓库>/scripts/vivado/program_sakura_x.tcl`，不需要参数。GUI 里没有 `-tclargs`，脚本会自动用当前打开工程 `impl_1` 生成的 `sakura_x_top.bit`（没有打开工程时用 `build/vivado/sakura-x_mlkem/` 下的那个）；烧完 Hardware Manager 保持打开。要换文件 / XVC / JTAG 频率，先 `set sakura_bit <文件>`、`set sakura_xvc <host:port>`、`set sakura_jtag_hz 3000000` 再 `source`。
- JTAG 线接 **CN5**（Kintex-7）。CN10 是 Spartan-6 的 JTAG，接错了脚本会报 “no Kintex-7 found”。
- 脚本会打印它看到的每个 JTAG target 和上面的器件，报错时把这段输出一起看。
- **Linux + Platform Cable USB II**：Vivado 自带的线缆驱动要用 root 装一次（`<Vivado>/data/xicom/cable_drivers/lin64/install_script/install_drivers/install_drivers`），装完拔插线缆。固件加载后线缆灯变绿，`lsusb` 显示 `03fd:0008`。没装驱动时脚本会报 “hw_server sees no JTAG cable”。
- 扫描链出错时降低 JTAG 时钟：第 2 个参数写 `-`，第 3 个参数是频率，例如 `-tclargs <bit> - 3000000`。
- Vivado 在 Docker 里、经 XVC 访问 JTAG 时，第 2 个参数写 XVC 地址，例如 `host.docker.internal:3721`。
- 不用 Vivado 也能烧：`openFPGALoader -c xilinxPlatformCableUsb <bit>`。
- 这是易失性烧录：断电后 Kintex-7 会从板上 BPI Flash 重新配置（出厂内容），所以**每次上电都要重新烧录**。脚本不写 Flash。
- 构建脚本会额外生成 `io_impl.rpt`（实际布局的引脚），接线前可以对照约束检查一遍。

### 4.1 上电检查（看 LED）

| LED | 含义 | 应该看到 |
| --- | ---- | -------- |
| LED10 | MMCM 锁定 | 常亮。不亮：没有 200 MHz 时钟，检查 SW3.7 |
| LED4 | 心跳 | 1 Hz 闪烁 |
| LED6 | 运行 | 暗亮 |
| LED5 | 复位中 | 平时灭。**一直亮**：SW4 的极性和假设相反，把 `sakura_x_top` 的 `BTN_ACTIVE_LOW` 改成 0 后重新构建 |
| LED1 | 运算活动 | 每次运算后亮 0.1 s |
| LED2 / LED3 | 上次运算成功 / 出错 | |
| LED7 / LED8 | 主机链路：应答帧闪 / 帧被拒绝 0.5 s | |
| LED9 | 0x05 完成通知 | 每次运算闪一下 |

如果所有 LED 的亮灭都和上表相反，说明 LED 是低电平点亮，把 `LED_ACTIVE_LOW` 改成 1。核心运算期间 LED 全部静止，不会在功耗 trace 里引入翻转。

然后按 Arty 的流程走：

```bash
make check PORT=sakura:/dev/ttyUSB0
```

```bash
make acvp-uart PORT=sakura:/dev/ttyUSB0
```

先做链路自检，再跑 NIST ACVP 功能测试，全部通过后才开始采集。`/dev/ttyUSB0` 是 SAKURA-X 板载 USB 的通道 A；用 CN8 外接适配器时直接写适配器的串口（不加 `sakura:`）。

---

## 5. Teledyne LeCroy HDO6104 设置

GUI 和 `acquire_cpa.py` 有专门的 LeCroy 驱动（`cpa_toolkit/scope.py` 的 `LeCroyScope`）。它只设置传输格式（`CHDR OFF`、`CFMT DEF9,WORD,BIN`、`CORD LO`、`WFSU`），每条 trace 前 `TRMD SINGLE`，等待采集完成后用 `C<n>:WF? ALL` 读回 16 位采样值（HDO 是 12 位有效）。它**不改**任何模拟设置，下面这些都在示波器面板上做。

### 5.1 网络

| 示波器（Utilities › Utilities Setup › Remote） | GUI 的 Transport | 备注 |
| ---------------------------------------------- | ---------------- | ---- |
| Control from **TCPIP (VICP)**（2026-10-16 拍照时的设置） | VICP | 需要 `pyvicp`：`uv sync --extra scope` |
| Control from **LXI (VXI11)** | VXI-11 / LXI | 只需要 pyvisa-py |

IP 地址在同一页的 TCP/IP 栏（拍照时是 `192.168.0.174`，已写进 `config.py` 的 `SCOPE_IP`）。如果示波器用 DHCP，地址可能会变，变了就回这一页看。电脑要在同一网段，先 `ping 192.168.0.174`。

### 5.2 通道和触发

| 项目 | 设置 | 说明 |
| ---- | ---- | ---- |
| 测量通道 | 例如 **C2**（拍照时 C2 是 20 mV/div、DC50、开了带宽限制） | GUI 的 *Channel* 填 `C2` |
| 触发通道 | 例如 **C4**，DC 1 MΩ，探头接 CN8 第 3 脚 | |
| 触发 | Edge，源 C4，上升沿 | |
| 触发电平 | **≈ 1.25 V** | `trig_out` 是 2.5 V 信号。拍照时电平是 2.50 V，正好卡在高电平上，会漏触发或时有时无 |
| 触发模式 | Normal | 驱动每条 trace 会自己切到 SINGLE，所以 Auto 不会造成误触发，但 Normal 更直观 |
| 触发位置 | **记录最左端** | 用 *Delay* 旋钮让时间轴从 0 开始。默认触发在正中（时间轴从 −5 格开始），整条 trace 会错位 |
| 记录长度 | **≥ 25 µs**（例如 2.5 µs/div 或 5 µs/div） | 三个 BMUL 窗口到 22.8 µs 结束。拍照时是 200 ns/div（只有 2 µs），太短 |
| 采样率 | 2.5 GS/s | 25 µs 约 62 500 点，一条 trace 约 250 kB |
| 采集 | 不开平均 | 带宽限制可以开，但整个 campaign 中不要改 |

### 5.3 验证

1. GUI → Connection：Driver = Teledyne LeCroy，Transport 与 5.1 一致，Channel = C2 → *Connect*。信息栏会显示型号、采样率、触发设置（源和电平）和时基；
2. Acquisition → *Check trigger*：四项都应 PASS。第 4 项 “record starts at the trigger” 读的是 LeCroy 报告的第一个采样点相对触发的时间，没对齐时会直接告诉你偏了多少；
3. *Capture*：调垂直档位，不削顶；KPI 里的 “trigger position” 应接近 0。

注意：`CHDR OFF`（应答不带命令头）在断开连接时不会自动恢复。如果之后用别的软件控制这台示波器，而那个软件依赖命令头，需要它自己再设回来。

---

## 6. 尚未在硬件上确认的地方

下面几点按规格书和公开资料做了选择，第一次上板时请确认：

1. **引脚**：来自规格书 v1.1、AIST 参考设计和 AMD 封装文件，没有在实物上逐个测过。local bus 桥在仿真里对 AIST 原版控制器源码验证过（`make sim-sakura`），但还没上板。
2. **时钟输入标准**：U16 按 LVDS 输入、不加片内端接，和一个公开的 SASEBO-GIII 设计相同（规格书信号名写的是 “HSTL”）。LED10 不亮且 SW3.7 已是 OFF 时，先怀疑这一点。
3. **SW4 和 LED 的极性**：假设按键按下为低、LED 高电平点亮，分别由 `BTN_ACTIVE_LOW`、`LED_ACTIVE_LOW` 参数控制（判断方法见 4.1 节）。
4. **时序**：以当前构建的 `timing_impl.rpt` 为准；构建成功不等于板载 USB 已连通。
5. **测量点**：规格书第 2.6 节（Measurement Points）写的是 TBD，只在表 1 写了 “High side of VCCINT (Core)”。探头和放大器按你的实际连接记录在 campaign 备注里。
