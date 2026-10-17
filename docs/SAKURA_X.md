# SAKURA-X（SASEBO-GIII）移植与 LeCroy HDO6104 设置

ML-KEM-768 核心在 SAKURA-X 上运行于**密码 FPGA**：Kintex-7 XC7K160T-1FBG676C（U11）。控制 FPGA（Spartan-6，U17）不改动。`hdl/core/` 和主机协议（[UART_PROTOCOL.md](UART_PROTOCOL.md)）与 Arty 版完全相同，只多了一个板级顶层、一份约束和两份 Tcl。Arty A7 / Z7 的文件没有任何改动。

| 文件 | 内容 |
| ---- | ---- |
| `hdl/xilinx/sakura_x/sakura_x_top.sv` | 时钟（200 MHz 差分 → MMCM）、复位、引脚、LED 映射 |
| `hdl/xilinx/sakura_x/constraints.xdc` | 引脚约束，每个引脚都注明了出处 |
| `scripts/vivado/build_sakura_x.tcl` | 独立的构建脚本，工程在 `build/vivado/sakura-x_mlkem/` |
| `scripts/vivado/program_sakura_x.tcl` | 经 JTAG（CN5）烧录，只认 Kintex-7 器件 |

引脚来源：*SASEBO-GIII Specification* v1.1（产综研 AIST，2013-04-26）第 3.1 节表 13–20、表 7；每个引脚的 bank 和 HR/HP 类型都用 AMD 的封装文件 `xc7k160tfbg676pkg.txt` 核对过。

---

## 1. 为什么串口不走板载 USB

板载 USB（CN15）上的 FT2232H **只连到 Spartan-6**（规格书表 26），Kintex-7 碰不到它。SAKURA 官方的方式是在 Spartan-6 里放一个控制电路，再经两片 FPGA 之间的总线转给 Kintex-7，但这需要另一套主机协议，而且 Spartan-6 只能用 ISE 14.7 编译（Vivado 不支持）。

所以和 Arty Z7 纯 PL 版一样，这里用一个**外接 USB-UART 适配器**，接在 Kintex-7 的 GPIO 排针 CN8 上。主机端代码完全不用改，GUI 里选适配器对应的串口即可。

---

## 2. 接线

### 2.1 CN8 引脚（单排 14 针，Samtec TSW-114）

```
  脚号:   1      2      3     4 … 10    11     12     13     14
        ┌──────┬──────┬──────┬────────┬──────┬──────┬──────┬──────┐
        │ RX ← │ TX → │ TRIG │  ---   │ GND  │ 2.5V │ GND  │ VIN  │
        └──────┴──────┴──────┴────────┴──────┴──────┴──────┴──────┘
         D19    N17    N16              地     电源    地    危险！
```

| CN8 | 信号 | FPGA 管脚 | 方向 | 接到 |
| --- | ---- | --------- | ---- | ---- |
| 1 | `uart_rxd` | D19 | FPGA 输入 | 适配器的 **TXD** |
| 2 | `uart_txd` | N17 | FPGA 输出 | 适配器的 **RXD** |
| 3 | `trig_out` | N16 | FPGA 输出 | 示波器触发探头针尖 |
| 11 / 13 | GND | — | — | 适配器 GND、探头地夹 |
| 12 | 2.5 V | — | 电源 | 只给适配器的 VCCIO / VIO **输入**脚（见 2.2） |
| 14 | VIN | — | 电源 | **什么都不要接**（板子输入电源，规格书表 7 写作 “VIN (12V)”） |

第 1 脚的位置以板上丝印为准。接线前用万用表确认（板子上电）：第 12 脚对第 11 脚应为 **+2.5 V**。读数不对就先别接。

### 2.2 USB-UART 适配器必须是 2.5 V 电平

CN8 所在的 bank 13/14/15 是 HR bank，VCCO = **2.5 V**。HR 引脚的输入电压绝对最大值是 VCCO + 0.55 V = **3.05 V**（DS182），**3.3 V 适配器的 TXD 直接接上会超出这个值**。Arty Z7 用的那个 3.3 V 适配器不能直接拿来用。

可用的做法：

- 带独立 VCCIO / VIO 引脚的适配器（FT232R、FT232H、CP2102N 等模块常有），VCCIO 接 CN8 第 12 脚（2.5 V），I/O 电平就是 2.5 V；
- 3.3 V 适配器加一片双向电平转换（3.3 V ↔ 2.5 V）。

**不要**把适配器自己的 3.3 V / 5 V 电源**输出**接到 CN8 第 12 脚。

### 2.3 开关

| 开关 | 设置 | 说明 |
| ---- | ---- | ---- |
| SW1 | USB 或 EXT | 电源（规格书表 11） |
| SW3.7 | **OFF**（出厂默认） | ON 会关掉 200 MHz 晶振，设计就没有时钟了 |
| SW3.1–3 | 出厂默认 | Kintex-7 配置模式（默认 BPI）；JTAG 烧录不受影响 |
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

```bash
vivado -mode batch -source scripts/vivado/build_sakura_x.tcl
vivado -mode batch -source scripts/vivado/program_sakura_x.tcl -tclargs build/vivado/sakura-x_mlkem/pqc_mlkem_sakura_x.runs/impl_1/sakura_x_top.bit
```

- JTAG 线接 **CN5**（Kintex-7）。CN10 是 Spartan-6 的 JTAG，接错了脚本会报 “no Kintex-7 found”。
- Vivado 在 Docker 里、经 XVC 访问 JTAG 时，在 bitstream 后面加 XVC 地址，例如 `host.docker.internal:3721`。
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
make check PORT=/dev/cu.usbserial-XXXX
```

```bash
make acvp-uart PORT=/dev/cu.usbserial-XXXX
```

先做链路自检，再跑 NIST ACVP 功能测试，全部通过后才开始采集。`XXXX` 是 2.5 V 适配器的串口，不是 SAKURA-X 自己的 USB。

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

1. **引脚**：来自规格书 v1.1 和 AMD 封装文件，没有在实物上逐个测过。接线前按 2.1 节用万用表确认 CN8 第 1 脚和第 12 脚。
2. **时钟输入标准**：U16 按 LVDS 输入、不加片内端接，和一个公开的 SASEBO-GIII 设计相同（规格书信号名写的是 “HSTL”）。LED10 不亮且 SW3.7 已是 OFF 时，先怀疑这一点。
3. **SW4 和 LED 的极性**：假设按键按下为低、LED 高电平点亮，分别由 `BTN_ACTIVE_LOW`、`LED_ACTIVE_LOW` 参数控制（判断方法见 4.1 节）。
4. **时序**：还没有在 Vivado 里构建过。Kintex-7 −1 比 Artix-7 −1 快，206.25 MHz 应该比 Arty 上宽松，但以第一次构建的 WNS 为准。
5. **测量点**：规格书第 2.6 节（Measurement Points）写的是 TBD，只在表 1 写了 “High side of VCCINT (Core)”。探头和放大器按你的实际连接记录在 campaign 备注里。
