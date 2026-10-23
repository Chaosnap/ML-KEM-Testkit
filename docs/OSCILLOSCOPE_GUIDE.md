# 示波器操作指南

本指南说明怎样设置示波器来采集 ML-KEM-768 Decaps 的 trace。示例使用 Teledyne LeCroy HDO6104（MAUI 界面）。其他品牌见第 10 节。

GUI 的操作见 [GUI_GUIDE.md](GUI_GUIDE.md)。实验室的完整流程见 [TRACE_ACQUISITION.md](TRACE_ACQUISITION.md)。

本指南按 ASD-STE100 的规则写：一句一个指令；先写条件，再写动作；**警告** 表示可能损坏设备或丢失数据，**注意** 表示可能使测量失败。

## 1. 原理：为什么要两个通道

示波器同时记录两个通道：

| 通道 | 示例 | 信号 | 用途 |
| ---- | ---- | ---- | ---- |
| 信号通道 | C2 | 功耗或 EM 探头 | CPA / TVLA 分析的数据 |
| 标记通道 | C4 | trig_out | 触发源；GUI 用它找每条 trace 的 Decaps 起点 |

trig_out 是 FPGA 的 STATUS.busy：Decaps 开始的那个核心时钟周期变高，Decaps 结束时变低（约 26 300 个核心周期，约 128 µs）。

BMUL 窗口在 Decaps 起点之后的固定核心周期上（`config.py: BMUL_WINDOWS`，约 6.2 µs 到 22.8 µs）。触发抖动、预触发和探头延迟都会改变起点在记录中的位置。所以 GUI 不按触发时间直接画窗口：它在每条 trace 的标记通道中找上升沿（解析，parse），然后从这个起点放 BMUL 窗口。如果标记的下降沿也在记录内，GUI 还用脉宽和 CYCLE_COUNT 测量实际核心时钟。

```text
            ┌──────────── trig_out（标记通道）────────────────── … ┐
  ──────────┘                                                     └──
            ↑ Decaps 起点（GUI 在每条 trace 中找到）
  |<-预触发->|<- BMUL 0 ->|  |<- BMUL 1 ->|  |<- BMUL 2 ->|      记录结束
  记录开始     6.2–7.6 µs      13.8–15.2 µs     21.4–22.8 µs
```

## 2. 接线

**警告：** SAKURA-X 的 trig_out 是 2.5 V 逻辑。不要把探头地夹接到 CN8 第 12 脚（2.5 V）或第 14 脚（VIN）。

**警告：** 先关闭板子的电源，再连接或移动探头。

### 2.1 标记探头（trig_out）

1. 用 10:1 无源探头。
2. 把探头尖接到 trig_out 引脚：

   | 板子 | trig_out | 地 | 逻辑电平 |
   | ---- | -------- | -- | -------- |
   | SAKURA-X | CN8 第 3 脚 | CN8 第 13 或 11 脚 | 2.5 V |
   | Arty A7 | Pmod JA 第 1 脚 | JA 第 5 或 11 脚 | 3.3 V |
   | Arty Z7 | Pmod JA 第 3 脚 | JA 第 5 或 11 脚 | 3.3 V |

3. 把探头接到示波器的 C4。

### 2.2 信号探头

1. 如果用近场 EM 探头，把探头放在 FPGA 芯片上方或核心电源的去耦电容附近。
2. 如果用电流测量，把放大器输出接到 SMA 输入。
3. 把探头接到示波器的 C2。
4. 记下探头的型号和位置。在 Trace Library 中把它写进 Campaign 的备注。

**注意：** 移动信号探头后，新数据不能和旧数据一起分析。

## 3. 网络和远程控制

1. 在示波器上打开 **Utilities › Utilities Setup › Remote**。
2. 在 **Control From** 中选择 **TCPIP (VICP)**。
3. 记下 IP 地址（例如 `192.168.0.174`）。
4. 把 PC 和示波器接到同一个子网。
5. 在 PC 上执行 `ping <示波器 IP>`，确认有回应。
6. 在 PC 上执行 `uv sync --extra scope`。这个命令安装 VICP 驱动（pyvicp）。

如果要用 LXI，在 **Control From** 中选择 **LXI (VXI11)**，然后在 GUI 的 **Transport** 中选择 **VXI-11 / LXI**。

## 4. 通道设置

在屏幕下方点击通道的标签（例如 **C2**），打开通道对话框。

### 4.1 信号通道 C2

| 设置 | 值 | 说明 |
| ---- | -- | ---- |
| Trace | On | |
| Coupling | DC50Ω（SMA / 放大器）；DC1MΩ（无源探头） | 与探头一致 |
| Bandwidth | Full（或 350 MHz） | 不要用 20 MHz：它会滤掉核心时钟（206.25 MHz）附近的信号 |
| V/div | 从 20 mV/div 开始，第 8 节再调整 | |
| Offset | 0 V，然后把信号移到屏幕中间 | |
| Probe attenuation | 与探头一致 | |

### 4.2 标记通道 C4

| 设置 | 值 | 说明 |
| ---- | -- | ---- |
| Trace | On | 关闭的通道不能读取 |
| Coupling | DC1MΩ | 10:1 无源探头 |
| Probe attenuation | 10× | |
| V/div | 1 V/div | |
| Offset | 调整到 0 V 和逻辑高电平都在屏幕内 | 标记波形不能削顶 |

## 5. 时基和采样率

打开 **Timebase**（屏幕下方右侧的时基标签）。

1. 在 **Sampling Mode** 中选择 **Real Time**。不要用 RIS 或 Sequence。
2. 在 **Memory Mode** 中选择 **Fixed Sample Rate**。
3. 把 **Sample Rate** 设为 2.5 GS/s。CPA 需要每个核心周期 8 个或更多的采样点（206.25 MHz × 8 ≈ 1.65 GS/s）。
4. 把 **Time/div** 设为 5 µs/div。10 格的记录长 50 µs，每条 125 000 个采样点。
5. 调整 **Delay**，把触发点放在网格左侧约 1 格的位置。这时记录从触发前约 5 µs 开始，到触发后约 45 µs 结束。

记录必须满足两个条件：

- 记录在 Decaps 开始之前开始。标记在第 0 个采样点必须是低电平，否则 GUI 找不到上升沿。
- 记录在 Decaps 开始后至少持续 23 µs（BMUL 2 在 22.8 µs 结束）。

| Time/div | 记录 | 每条采样点（2.5 GS/s） | 每条文件大小（约） | 适用 |
| -------- | ---- | ---------------------- | ------------------ | ---- |
| 5 µs/div | 50 µs | 125 000 | 0.5 MB（含标记） | CPA（推荐）；TVLA 的 J(z‖c) 也在记录内 |
| 20 µs/div | 200 µs | 500 000 | 2 MB | 完整 Decaps（标记下降沿在记录内，GUI 测量实际时钟） |

**注意：** 采集过程中不要改变时基、采样率或存储深度。如果每条 trace 的长度改变，GUI 停止采集。

## 6. 触发设置

打开 **Trigger › Trigger Setup**。

| 设置 | 值 |
| ---- | -- |
| Type | Edge |
| Source | C4（标记通道） |
| Coupling | DC |
| Slope | Positive |
| Level | SAKURA-X：1.25 V；Arty：1.6 V |

1. 按前面板的 **Normal**。不要用 **Auto**：没有触发时 Auto 也会采集，Check trigger 会失败。
2. 采集时 GUI 自动发送 SINGLE（`TRMD SINGLE`）。不要在采集时按前面板的按钮。

**注意：** SAKURA-X 上不要把触发电平设为 2.5 V。这个电平等于逻辑高电平，示波器会漏掉触发。

## 7. 采集模式

1. 关闭平均（Averaging = 1）。平均会把不同密文的 trace 混合，CPA 没有意义。
2. 关闭 ERES、Peak Detect 和 Envelope。
3. 不要用数学通道（F1…）作为信号通道，除非你知道它的作用。

## 8. 在 GUI 中检查

1. 在 GUI 的 **Connection** 页面输入：
   - **IP address**：示波器的 IP 地址。
   - **Driver**：Teledyne LeCroy。
   - **Transport**：VICP。
   - **Signal channel**：`C2`。
   - **Marker channel (trig_out)**：`C4`。
2. 点击示波器卡片的 **Connect**。**Instrument** 中必须显示 **Marker: C4 (trig_out)**。
3. 打开 **Acquisition** 页面，在 **Setup capture** 中点击 **Capture**。
4. 确认图中有黄色的标记波形（右侧纵轴），并且上方显示 **Decaps start: found at … µs after the record start**。
5. 确认 BMUL 0、1、2 三个窗口都在图中。
6. 调整 C2 的 V/div：信号占屏幕高度的 70–80 %，不能削顶。LeCroy 削顶时在通道标签上显示过载标记。
7. 点击 **Check trigger**。所有行必须是 PASS：

   | 检查 | 意思 |
   | ---- | ---- |
   | no trigger without an operation | 没有 Decaps 时示波器不触发（触发模式不是 Auto） |
   | trigger on the Decaps start | 一次 Decaps 产生一次触发 |
   | Decaps result | 板子的 K 正确 |
   | marker: Decaps start found | GUI 在标记中找到上升沿 |
   | record covers the BMUL windows | 记录包含三个 BMUL 窗口 |
   | record start | 预触发正确（有标记时预触发是正确的） |

8. 如果要保存这些设置，点击 **Save as default**。

## 9. 采集中和采集后

1. 采集时不要碰探头、示波器和板子。
2. GUI 保存每条 trace 的信号和标记，并在采集时找到每条 trace 的 Decaps 起点。
3. 采集后，在 **Parse & View** 页面查看解析结果：
   - **parsed** 必须是全部 trace。
   - **spread** 是起点位置的最大差。几个采样点是正常的触发抖动。
4. 如果改变了解析设置，在 **Parse & View** 中点击 **Parse**，用保存的标记重新解析。不需要重新采集。

## 10. 其他品牌

GUI 用同一个方法读取标记通道：采集后把波形源切换到标记通道，读取，再切换回信号通道。

| 品牌 | 通道名称 | 触发 | 预触发 |
| ---- | -------- | ---- | ------ |
| Keysight InfiniiVision | `CHANnel2`（信号），`CHANnel4`（标记） | Edge，Source CH4，上升沿 | Horizontal › Time Ref = Left，然后把延迟设为约 1 格 |
| Rigol / Siglent | `CHANnel2`，`CHANnel4` | Edge，CH4，上升沿 | 调水平位移，把触发标记移到左侧约 1 格 |
| Tektronix | `CH2`，`CH4` | Edge，CH4，上升沿 | Horizontal Position 约 10 % |

**注意：** 通用 SCPI 驱动以 8 位码传输数据。把信号调到屏幕高度的 70–80 %，减少量化误差。

## 11. 故障排除

| 问题 | 原因 | 处理 |
| ---- | ---- | ---- |
| GUI 不能连接示波器 | IP、Transport 或远程设置不对 | 执行 `ping`。确认 **Control From** 与 GUI 的 **Transport** 相同 |
| "marker channel C4 returned no samples" | C4 没有打开 | 在示波器上打开 C4 的 Trace |
| "marker is high at sample 0" | 记录在 Decaps 开始之后才开始 | 调整 Delay，把触发点放到网格左侧约 1 格 |
| "the marker does not change" | 探头没有接 trig_out，或接错通道 | 检查探头尖和地夹。确认 **Marker channel** 与示波器通道相同 |
| "noisy marker" | 标记通道只有噪声 | 检查探头。把 C4 设为 1 V/div、DC1MΩ |
| Check trigger："no trigger without an operation" 失败 | 触发模式是 Auto，或触发源不对 | 按 **Normal**。把 Source 设为 C4 |
| Check trigger："trigger on the Decaps start" 失败 | 触发电平太高或太低 | SAKURA-X 设为 1.25 V，Arty 设为 1.6 V |
| BMUL 窗口缺少或被截断 | 记录太短 | 把 Time/div 设为 5 µs/div 或更大 |
| 采集停止："the scope returned N samples" | 采集中改变了时基 | 把时基改回原来的值，然后继续采集 |
| CPA 没有结果，offset 分散 | 信号太弱或探头位置不好 | 用 TVLA 比较几个探头位置（[TRACE_ACQUISITION.md](TRACE_ACQUISITION.md) 第 3.3 节） |

## 12. 设置速查

```text
C2 信号   DC50Ω（或 DC1MΩ）  Full BW   20 mV/div 起   信号占 70–80 %
C4 标记   DC1MΩ  10×  1 V/div  Trace On
时基      Real Time  Fixed Sample Rate  2.5 GS/s  5 µs/div（50 µs）
预触发    触发点在左侧约 1 格（记录在 Decaps 之前开始）
触发      Edge  C4  DC  Positive  1.25 V（SAKURA-X）/ 1.6 V（Arty）  Normal
采集      Averaging 1，无 ERES / Peak Detect
远程      Utilities › Remote › TCPIP (VICP)
GUI       Signal channel C2，Marker channel C4
```
