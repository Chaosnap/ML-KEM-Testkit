# SAKURA-X：基于 sakura-x-shell 的 ML-KEM-768 移植

本版本按 `RESET / LOAD_DK / LOAD_CT / START / READ_SS` 实现 Decaps 采集链路。
**此移植及本次 GUI 修改未在本机运行测试、仿真、综合、实现或上板验证。**
随仓库提供上游 Spartan-6 预编译 bit；新的 Kintex bit 必须在 Linux 用 Vivado 构建。

## GUI 连接与排障（无需通信命令行）

更新项目并重启 GUI 服务后，在浏览器刷新页面：

1. **Connection → Board interface → SAKURA-X shell — USB FIFO**。
2. 点 **Refresh devices**，选择板卡序列号对应的设备，例如 `FT9A4QV0`。
   GUI 只列出已识别为 interface 00 的数据通道，并显示同一个 USB 设备的 B 通道。
   不需要手写 `sakura-shell:`，也不要按 `/dev/ttyUSB0/1` 的尾号判断 A/B。
   旧名称 `USB <-> Serial Converter` 可以手动选择；不要求先改产品字符串。
3. 点 **Connect & diagnose**。GUI 依次检查 A/B 配对、打开 B 并释放复位、打开 A、
   Spartan 命令应答、Kintex shell 标识、ML-KEM 核。每一步单独显示 PASS/FAIL。
4. 曾出现超时、半包或重新下载 FPGA 时，点 **Reset & connect**。
   此按钮通过 Channel B RTS_N 复位 S6/K7 逻辑，然后重新检查连接。
   它会丢弃当前运行及输入有效状态；恢复后重载 DK/CT。不会写 EEPROM 或烧录 bitstream。
5. 失败时点 **Copy connection report**，直接粘贴报告，或截取 **Connection checks**。
   报告包含 USB 标识、A/B 配对及失败阶段，不包含 DK、CT 或 trace。
   展开 **Detected USB channels** 可看 GUI 服务实际枚举到的设备。

连接诊断的含义：

| 失败阶段 | 能确定的范围 / 下一步 |
| --- | --- |
| USB channel pairing | 软件尚未找到同一个 USB 设备的 A/B；刷新设备、检查板卡数据 USB 是否接入 |
| Open reset channel B / Open FIFO channel A | 设备访问失败；关闭其他占用 A/B 的程序，检查 Linux 账户访问权限 |
| Spartan command reply，`0/1 bytes` | 尚未获得 Spartan 命令应答；检查 S6 是否加载 **shell** bit、A 是否为 245 FIFO、复位是否释放；不能直接归因于 Kintex |
| Kintex shell identity | 先前的 Spartan 探测已通过；检查匹配的 `sakura_shell_top.bit`、两片 FPGA 间总线、时钟及复位 |
| ML-KEM core | shell 标识已收到；继续定位 Kintex 桥接器到核心的 AXI/核心复位 |

独立 Spartan 探测发送一个帧格式正确但类型为 `0xf` 的命令，要求上游控制器返回
`0x02`（未知命令）。根据随库 `command_control.v`，该分支不发起 Kintex 总线事务，
也不复位 FPGA。收到正确应答才继续检查 Kintex。超时后不盲目重复发送，也不自动复位。
但普通 Connect **不会清除前一次挂起的总线事务**。若控制器已卡在等待 Kintex 的状态，
它也无法处理这个独立探测；因此先用 **Reset & connect**，再根据失败阶段定位。
报告中的 `hard_reset: false` 表示这次连接没有主动执行该复位。
Linux 配对优先读取 sysfs 的 `bInterfaceNumber` 及物理 USB 父设备；不可用时回退到
串口元数据和唯一序列号。USB 产品名和 VID/PID **不能证明 EEPROM 的 FIFO 模式或当前 bitstream**。

点 **Open command panel** 可打开五条命令：
**RESET → LOAD_DK → LOAD_CT → START → READ_SS**。
可直接选择 `.bin` / `.hex` / `.txt` 输入文件，也可加载选中的测试向量；文件选择只填表，
点击 LOAD 才传到板上。START 等待完成、不读取 K；READ_SS 单独读取 32 字节结果。
**Run full Decaps & check K** 会自动完成加载、START、读取及预期结果比较。
采集仍使用 Acquisition 页面；shell 模式隐藏波特率与 KeyGen/Encaps，Arty 保留 UART 界面。

## 1. 使用这一套匹配的文件

| 部分 | 文件 / 设置 |
| --- | --- |
| Spartan-6，CN10 | `third_party/sakura_x_shell/sakura-x-shell-ctrl/sakura-x-shell-ctrl.bit` |
| Kintex-7，CN5 | 本项目新构建的 `sakura_shell_top.bit` |
| GUI 数据端口 | 选择 **SAKURA-X shell — USB FIFO**，再选择板卡；内部端口名为 `sakura-shell:<Channel A>` |
| FT2232H A，USB interface 00 | 245 FIFO 模式；传数据和命令 |
| FT2232H B，USB interface 01 | UART 模式；只使用 RTS_N 复位，不发送 UART 字节 |

**不要混用旧 `firmware/sakura_x/sasebo_giii_ctrl.bit`、旧 `sakura_x_top.bit`、
`sakura:` 端口或旧 `sakura_link_check.py`。它们属于 AIST local-bus 方案，协议和引脚方向不同。**
`hdl/xilinx/sakura_x/` 和旧文档保留供参考；新版本单独在 `hdl/xilinx/sakura_shell/`。
Arty A7/Z7 顶层与运算核没有修改。

架构：

```text
PC / GUI
   │ USB，Channel A FIFO
FT2232H ── Spartan-6 shell controller
              │ 32-bit common(address/write data) + 32-bit read data + handshake
              │ forwarded bus clock, reset
         Kintex-7 deskew MMCM + I/O registers (50 MHz)
              │ synchronous parallel handshake
         sakura_shell_bridge (50 MHz)
              │ AXI-Lite → axil_cdc
         pqc_mlkem_top (206.25 MHz)
              │ STATUS.busy
         CN8 pin 3 → scope trigger

Channel B RTS_N → Spartan controller reset → inter-FPGA reset → Kintex logic reset
```

用随仓库快照，不需要安装 ChipWhisperer 插件，不依赖嵌套 clone，也不需要 ISE。
快照版本、逐文件校验和及许可在 `third_party/sakura_x_shell/`。
上游 README 的 20 MHz 描述与 PLL 源码不一致；本移植按 `24 MHz × 25 / 12 = 50 MHz`
设置总线时钟。其预编译 bit 对应的实测时钟仍需要在板上确认。

## 2. Linux 准备与编程

普通 clone 即可（没有新 submodule）：

```bash
git clone <你的 ML-KEM-Testkit GitHub URL>
cd ML-KEM-Testkit
uv sync --extra scope
```

USB CN15 接到 Linux；下载器接 CN10/CN5。数据 USB 与下载器 USB 是两条不同链路。
确认当前账户可访问串口（Ubuntu 常用 `dialout`，加组后重新登录）：

```bash
sudo usermod -aG dialout "$USER"
```

EEPROM 需设置 A=245 FIFO、B=UART。模板在：
`third_party/sakura_x_shell/ft2232h_config/sakura-x-shell.xml`。
用 FT_PROG 配置前导出本块板的 EEPROM 备份，保留唯一序列号。模板的产品字符串是
`SAKURA-X Shell`，GUI 只有精确匹配该字符串与 interface 00 才标为推荐；旧产品名也可手动选择
FIFO 设备。**改产品名本身不会把 UART 自动变成 FIFO**，必须同时检查 A/B 模式。
Linux 使用 `ftdi_sio` 提供的端口，不需要卸载/解绑该驱动，不要让其他程序打开 A 或 B。
不要按 ttyUSB0/1 的尾号猜通道；驱动按 USB interface、物理 USB 父设备或唯一 serial 配对 A/B。

### Spartan：使用现成 shell bit

先关闭占用下载器的 Vivado Hardware Manager、hw_server、OpenOCD 等。
沿用你已经成功使用的 `xc3sprog` 和 Xilinx Platform Cable USB II：

```bash
sudo xc3sprog -c xpc -v -p 1 \
  third_party/sakura_x_shell/sakura-x-shell-ctrl/sakura-x-shell-ctrl.bit
```

`-p 1` 只适用于你之前检测到的 CN10 链：位置 0 是 XCF32P，位置 1 是 XC6SLX45。
输出必须显示目标 `XC6SLX45`。这条命令加载 FPGA SRAM，不写 XCF32P。
保持板子上电，之后移动 JTAG 排线到 CN5；断电后要重新加载两块 FPGA。

### Kintex：Vivado GUI 或批处理

在安装了 Kintex-7 器件支持的 Vivado 环境中运行：

```bash
vivado -mode batch -source scripts/vivado/build_sakura_shell.tcl
```

输出：

```text
build/vivado/sakura-shell_mlkem/pqc_mlkem_sakura_shell.runs/impl_1/sakura_shell_top.bit
```

若要手工点 GUI，先只创建项目，不执行综合：

```bash
vivado -mode batch -source scripts/vivado/build_sakura_shell.tcl -tclargs --project-only
```

打开 `build/vivado/sakura-shell_mlkem/pqc_mlkem_sakura_shell.xpr`，
依次 Run Synthesis → Run Implementation → Generate Bitstream。
Hardware Manager → Open Target → 选择 CN5 上的 `xc7k160t` → Program Device → 选择新 bit。

**已有 GUI 工程更新本次时序修复时：**

1. 更新仓库文件后重新打开此 shell 工程；Sources 中的源码路径应指向当前 clone。
2. **Tools → Run Tcl Script**，选择仓库的 `scripts/vivado/configure_sakura_shell.tcl`。
   只更新当前工程的实现设置并安装生成前检查，不启动构建，也不连接硬件。
   新建工程或使用更新后的 build 脚本时，这一步会自动完成。
3. 在 Design Runs 对 `synth_1` 执行 **Reset Run**，重新 Run Synthesis → Run Implementation。
   不要直接沿用旧的 implemented design/checkpoint。
4. 打开 **Open Implemented Design → Report Timing Summary**，检查 setup/hold 均通过，
   再 Generate Bitstream。生成前检查会阻止 setup/hold slack 为负的构建。
   检查失败不会删除磁盘上原有的旧 bit；旧 bit 不能当作本次成功输出。
5. 下载新 Kintex bit 后，在采集 GUI 点 **Reset & connect**，观察
   Spartan command reply → Kintex shell identity → ML-KEM core 三步结果。
   新增的 I/O 寄存器保持原握手协议，无需因此重建 Spartan bit。

工程独立，不覆盖旧 SAKURA/Arty 工程。构建脚本生成 timing、CDC、pin、utilization、DRC 报告，
**先布线、检查 setup/hold，再生成 bit**；GUI Generate Bitstream 使用相同检查。
还会生成 `shell_outputs_max.rpt` / `shell_outputs_min.rpt` 和
`shell_inputs_max.rpt` / `shell_inputs_min.rpt`，覆盖返回总线及输入总线的 setup/hold 路径。
`cdc.rpt` 现在输出详细端点。仍需检查 `timing.rpt` 的未约束路径、CDC 及板外接口预算；
setup/hold 通过不等于已完成硬件验证，也不代表 CDC 警告可以忽略。
当前 XDC 的源同步 I/O 延迟是明确的实现预算，不是已在实板测得的时序结论。

### 2026-10-22：已收到的布线报告与本次修复范围

用户提供的旧构建报告（Vivado 2024.1，Routed）显示：

| 路径 | 结果 |
| --- | --- |
| ML-KEM `core_raw → core_raw` | setup WNS **+0.411 ns**，没有 setup 失败端点 |
| 返回总线 `shell_bus → shell_bus` | setup WNS **−3.641 ns**，35 个失败端点；截图包括三个握手输出及 `o_read_data` |
| 最差路径 | `u_bridge/state_reg[2]_replica` → `o_write_data_ready` |
| 路径预算 | 半周期 10 ns，输出预算 4 ns；源时钟插入延迟 4.863 ns、数据路径 4.743 ns、不确定度 0.035 ns |

计算为 `10 − 4 − 0.035 − 4.863 − 4.743 = −3.641 ns`。
这说明旧构建的返回接口没有满足所设约束，不能保证 Spartan 正确接收；
并不能仅凭 WNS 证明这就是所有 `Spartan ACK timeout` 的唯一原因。

修改顶层为 `bus_clk → MMCM → BUFG`，反馈也经过 BUFG，以补偿总线时钟插入延迟；
50 MHz 输入、50 MHz 输出，MMCM VCO 1000 MHz。引脚 AB11 为 MRCC，可使用专用时钟路径。
复位包含两组 MMCM 的锁定状态，并在各自时钟域同步释放。
所有总线输入同时寄存，所有返回数据/握手同时经 IOB 输出寄存器，避免 FSM 译码直接驱动引脚。
这会增加总线握手延迟；ML-KEM 核、206.25 MHz 核频率及其运算周期没有修改。
XDC 继续检查原 10 ns 半周期，并将新生成的总线时钟加入与核心隔离的异步时钟组。

这组改动**只经过源码及已有报告核对，没有本地运行仿真、综合、实现或上板测试**。
必须用重新布线的结果确认 I/O 寄存器放置、全部 setup/hold 裕量和实际通信。
旧 `cdc.rpt` 另外列出 core→bus 的两个 unsafe 端点，但摘要没有具体路径；
新报告的 `-details` 用于继续定位，不能将它们宣称为已解决。

实现参考：[AMD UG949：主时钟作为 I/O 延迟基准](https://docs.amd.com/r/2023.2-English/ug949-vivado-design-methodology/Using-a-Primary-Clock)、
[UG912：IOB 寄存器属性](https://docs.amd.com/r/en-US/ug912-vivado-properties/IOB)、
[UG894：生成 bit 前的 Tcl hook](https://docs.amd.com/r/2021.1-English/ug894-vivado-tcl-scripting/Defining-Tcl-Hook-Scripts)。

## 3. 五条应用命令

| 命令 | 行为 | 主机 API |
| --- | --- | --- |
| `RESET` | 清运算状态、输入完整标志、结果有效标志；下次必须重载 DK/CT | `target.reset()` |
| `LOAD_DK` | 将恰好 2400 字节写入输入 BRAM；不运行 Decode/NTT | `target.load_dk(dk)` |
| `LOAD_CT` | 将恰好 1088 字节写入 DK 后面的 BRAM；不运行运算核 | `target.load_ct(ct)` |
| `START` | 输入完整后开始整个 Decaps，包括输入读取、Decode/Decompress、NTT 等；阻塞等待一次完成响应 | `target.start()` |
| `READ_SS` | 完成成功且结果有效时读取 32 字节 K | `target.read_shared_secret()` |

命令名是应用层接口；底层保持上游 shell 的二进制读写格式，**不要往串口发送 ASCII `START` 等字符串**。
Spartan 使用现成 bit，不在 Spartan 新增这五个文本命令。
GUI 的 `Target.run()` 会组合 `START` 和完成后的结果读取，保持原采集程序的返回接口。
本 shell 移植只开放 ML-KEM-768 Decaps；GUI 的 KeyGen/Encaps 操作会明确拒绝。

DK 可在多条 trace 间复用；每次 START 会消耗 CT 的有效标志，下一轮必须重新 LOAD_CT。
若改变 DK，再 LOAD_DK。`RESET` 后两者都需重载；不会依赖 BRAM 复位后残留的内容。
部分/乱序输入写入不能组成有效完整输入；新输入会让旧 K 失效。

### 手动命令会话（只打开一次两个 USB 通道）

```bash
uv run python scripts/board/sakura_shell.py --list
uv run python scripts/board/sakura_shell.py --port /dev/ttyUSB0 --hard SESSION
```

将示例 `/dev/ttyUSB0` 换成你确认过的 **Channel A**。在 `sakura>` 提示符中输入：

```text
RESET
LOAD_DK /path/to/dk.bin
LOAD_CT /path/to/ct.bin
START
READ_SS /path/to/k.bin
LOAD_CT /path/to/next_ct.bin
START
READ_SS
QUIT
```

推荐持续 SESSION，不要为了每个命令反复打开 B：部分串口驱动打开时可能短暂改变 RTS。
正常运行 RTS=False（RTS_N 为高），复位脉冲 RTS=True → False。
`--hard` 会在初始 identity 查询之前复位 S6/K7，能恢复半包导致的控制器卡住。
不要在采集中操作这个端口或复位。

## 4. 运算期间静默

`START` 底层是**一次延迟返回的读取请求**：

1. PC 请求 → Spartan 发原生 header ACK、转发地址。
2. Kintex 等待 2 ms，留出前一笔传输与 ACK 的静默间隔。
3. Kintex 内部写 CTRL.start；`trig_out` 上升，开始完整 Decaps。
4. 主机只等待当前读响应，不再提交 FIFO 命令；Kintex 根据同步后的 busy 信号判断完成，
   **不通过 AXI 轮询 STATUS**。
5. busy 下降后继续等待 quiet tail（默认 20 ms），才读取一次 STATUS 并送回响应。
6. 主机在这之后读取 CYCLE_COUNT、ERROR_CODE 和 K。

因此 `DONE` 是由 Kintex 经 Spartan/FIFO 返回的 START 完成响应，不是运算核直接发 UART，
也不是 FIFO 自动生成一个通知。无需 `READ_STATUS` 命令。
结果读取位于 quiet tail 之后，避免短 Decaps 结束后仍在采集的波形被通信污染。

GUI/采集程序在 arm 之前从 scope metadata 取得采集时长，加 5 ms 余量并设置 quiet tail
（至少 20 ms）。无法确定时长时会报错；连接 scope 后修改了前面板时基/延迟，应重新连接 scope
以刷新 metadata，采集中保持设置不变。手工会话可用 `--quiet-ms 100` 延长静默尾部。

这里静止的是应用数据传输和轮询；USB SOF、FPGA 时钟、Spartan 心跳等仍存在。
它不能保证消除全部噪声；需要实测比较 trace、SNR、TVLA 和 CPA。

## 5. GUI 与 Linux 验证

```bash
uv run python scripts/board/sakura_shell.py --port /dev/ttyUSB0 --hard RESET
uv run python scripts/board/sakura_shell.py --port /dev/ttyUSB0 INFO
uv run python scripts/board/sakura_shell.py --port /dev/ttyUSB0 CHECK
make gui
```

这些命令**没有在本机执行过**。`CHECK` 在 Linux 上才会执行：identity、1 KB buffer 回读、
合法 CT 与修改过的 CT 的 Decaps，并与项目软件参考 K 比较（包含 implicit rejection）。
这只是最初 bring-up，不替代后续全面验证。

GUI Connection 选择 **SAKURA-X shell — USB FIFO**，再选择板卡；此模式不显示波特率。
连接时 A/B 一起独占，后台 monitor 不会在 `run()` 持有锁时发查询。
然后连接示波器，按原流程生成向量、Check trigger、开始小批量 CPA/TVLA 采集。
`trig_out` 保持原核心 busy 定义，因此现有 BaseMul 窗口和 206.25 MHz 时间换算不变。

示波器触发：CN8 pin 3，2.5 V；GND 用 CN8 pin 11/13。上升沿、NORMAL/SINGLE，约 1.25 V 阈值。
新 Kintex LED：LED1 心跳、LED2 done、LED3 error、LED10 接口复位已释放；START 静默区间内保持输出。
Spartan LED 按随附 shell 的定义；不要继续套用旧 AIST bit 的心跳/LED 解释。

| Linux 上需要确认 | 通过依据 |
| --- | --- |
| 两块 FPGA 与 EEPROM 匹配 | INFO 返回 `SK ABI 1`，ALG_ID=1；A/B serial 对应 |
| 上传 | INPUT_READY 在完整 DK+CT 后为 3；RESET 后为 0 |
| 完整性保护 | 缺 DK/CT 时 START 报错；RESET、重载恢复；不能读旧 K |
| 功能 | CHECK 两条 Decaps 成功；GUI 多条向量的 K 全匹配 |
| quiet window | 抓取触发及跨 FPGA 数据/握手：计算期间无新的访问，结果晚于记录末尾 |
| 触发/时序 | 脉宽对应 CYCLE_COUNT；Vivado setup/hold/CDC/I/O 报告可接受 |
| 异常恢复 | GUI 的 **Reset & connect** 可用于恢复半包、超时；重连后重载 DK/CT |

超时不会自动重发 `START`，避免重复运算。收到 fatal link error 时停止采集，在 GUI 点
**Reset & connect**，重连后重载 DK/CT。普通 RESET 不覆盖 bitstream，不清 EEPROM，不编程 PROM。

## 6. 二进制接口（维护用）

上游 6 字节 header（最高位在前）：
`[0x8:4 bits][type:4][words-1:4][address:32][0x1:4]`。
读 type=1，写 type=2，每次 1..16 个 32-bit 字；地址按字节计且 4-byte 对齐。
Spartan 先回 1-byte ACK（0=有效 header）；读随后返回大端字，写 payload 也是大端字。
**ACK 仅表示 header 合法，不代表 payload 已写完或运算完成。** 主机用后续读作为写入屏障。
无 CRC；驱动把 DK/CT/SS 的原始字节按核心 little-endian word 布局转换，不能直接对调所有字节。

| 地址 | 含义 |
| --- | --- |
| 0x0000..0x002C | 原核心 CSR；直接写 CTRL.start 被禁止，必须用 START |
| 0x0100，读 1 字 | START；成功响应是核心 STATUS=2，bit31 表示 bridge fault |
| 0x0104 | 0x534B0001，shell ABI |
| 0x0108 | quiet tail，50 MHz bus cycles，RW（最少 2 ms；主机最少 20 ms） |
| 0x010C | 50,000,000，设计使用的总线频率（不是频率测量） |
| 0x0110 | sticky fault：1 访问错误，2 AXI，3 超时，4 busy，5 输入不完整 |
| 0x0114 | bit0 完整 DK；bit1 完整且未消耗 CT |
| 0x0118 | 结果有效；RESET、新输入和 START 会清零，成功完成置 1 |
| 0x4000..0x495F | DK，2400 字节 |
| 0x4960..0x4D9F | CT，1088 字节 |
| 0x4E00..0x4E1F | K，32 字节 |

RESET 是写 CTRL=2，同时清理 bridge 标志。硬复位则是 Channel B RTS_N。
地址、长度和输出布局在本 shell 固定；只支持顺序完整的输入上传。

## 7. 上传 GitHub

源码、Python 驱动、Tcl、文档与上游必要 bit/许可均放在普通目录中。
`/sakura-x-shell/` 原始嵌套 clone 已加入根 `.gitignore`，其内容保留，但不用作为 gitlink 提交。
`build/`、`data/`、`.venv/`、`.history/` 不入库。无需上传 Vivado 工程缓存或这台 Mac 的路径。

确认这次修改后再按自己的 Git 工作流 commit/push。另有你自己的未跟踪图文件，
提交时按需选择；本次未提交、未推送，也没有删除这些材料。
