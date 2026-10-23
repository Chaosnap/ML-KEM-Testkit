# Trace 采集上手指南（实验室版）

这份文档按实验室里的实际顺序写：出发前准备 → 接线 → 设置示波器 → 在 GUI 中连接和自检 → 调触发与时基 → 正式采集 → 采集后的整理与第一次分析。第 0 节是一页速查表，熟悉之后只看它就够了。

相关文档：通信协议 [UART_PROTOCOL.md](UART_PROTOCOL.md)，攻击模型 [ATTACK_MODEL.md](ATTACK_MODEL.md)，工具说明 [cpa_toolkit/README.md](../cpa_toolkit/README.md)，SAKURA-X 移植与 LeCroy HDO6104 设置 [SAKURA_X.md](SAKURA_X.md)。

---

## 0. 速查表

```
□ 板子上电，绿灯 3（A7 的 LD7 / Z7 的 LD3）1 Hz 闪烁 = bitstream 在运行
  （Z7 PS 版：每次上电先运行 uv run scripts/board/boot_arty_z7_ps.py）
□ 触发探头：A7 → Pmod JA 第 1 脚；Z7 → Pmod JA 第 3 脚（不是 JB）；地夹接 JA 第 5 或 11 脚（GND），别碰 6、12 脚（3.3 V）
  SAKURA-X → CN8 第 3 脚，地夹接 CN8 第 11 或 13 脚，别碰 12 脚（2.5 V）和 14 脚（VIN）
□ 测量探头接测量通道（例如 CH1 / LeCroy 的 C2），触发探头接另一个通道（例如 CH2 / C4）
□ 示波器：触发源 = 触发通道，上升沿，电平 ≈ 1.6 V（Arty，3.3 V）/ ≈ 1.25 V（SAKURA-X，2.5 V），模式 NORMAL（不是 AUTO）
□ 示波器：触发点放在记录的最左端（预触发 0），记录长度 ≥ 25 µs，采样率建议 ≥ 1 GSa/s
□ 示波器：采集模式 Normal/Sample（不要 Average），存储深度固定（不要 Auto）
□ make gui → Connection：选 …1 串口 → Connect → Run link self-test（4 项全部 OK）
□ Connection：示波器 IP、Driver、Transport、Channel = 测量通道（CHANnel1 / LeCroy 写 C2）→ Connect
□ Acquisition → Check trigger（全部 PASS）→ Capture 调垂直档位（不削顶），采样率和点数与示波器屏幕一致
□ Test Vectors → 生成向量集 → Run on board 前 20 条全部 ✓
□ Acquisition → 选向量集 → Start acquisition；采集中不要动示波器
□ Trace Library → 命名、打标签、在备注里写下探头位置和示波器设置
```

---

## 1. 先理解一次采集在做什么

### 1.1 系统连接

```
          USB（串口 115200）                    LAN（VISA / SCPI）
  PC  ───────────────────────►  FPGA 板          PC ◄──────────────► 示波器
 (GUI)                          │                                     ▲  ▲
                                ├── trig_out（Pmod JA）──► 触发通道 ───┘  │
                                └── 被测信号（功耗 / 电磁）──► 测量通道 ───┘
```

- PC 通过串口把密文发给板子，再发一个 `0x05 Run` 命令启动 Decaps；
- 板子开始运算的那个时钟沿，`trig_out` 从 0 跳到 3.3 V，示波器被它触发；
- 示波器在测量通道上记录这一段波形，PC 通过网络把波形读回来；
- 板子算完后把 K 和 CYCLE_COUNT 发回 PC，PC 逐条核对后写入 HDF5 文件。

### 1.2 一条 trace 的时间线

| 步骤 | 内容 | 大约耗时 |
| ---- | ---- | -------- |
| 1 | PC 让示波器进入单次触发等待（`:SINGle`） | 几 ms |
| 2 | 上传 1088 字节密文（每 50 条再额外上传一次 2400 字节的私钥 dk） | ≈ 94 ms（115200 波特率的上限） |
| 3 | 发 `0x05 Run`：`trig_out` 上升，Decaps 开始 | — |
| 4 | Decaps 运算，期间串口完全静默 | ≈ 26 300 个核心周期 ≈ 127.5 µs |
| 5 | 板子回传 STATUS、CYCLE_COUNT、K | 几 ms |
| 6 | PC 从示波器读回波形，核对 K 和 CYCLE_COUNT，写文件 | 取决于记录长度和网络 |

一条 trace 大约 0.1–0.2 秒，GUI 采集时会显示实际速度和剩余时间。

### 1.3 trace 里有什么

核心时钟 206.25 MHz（每个周期 4.85 ns）。**第 0 个采样点就是触发点，也就是 Decaps 开始的那一刻**，后面的一切位置都由此推算。

一次 Decaps（约 26 367 个核心周期 ≈ 127.8 µs）在 trace 里依次是下面这些阶段。位置是在 RTL 仿真里逐周期量出来的（`scripts/sim/bmul_timing.py` 解析 `testbench/core_sim` 的指令时间线，结果在 `cpa_toolkit/config.py: DECAPS_PHASES`），两次不同的 Decaps 周期完全一致：

| 阶段 | 核心周期 | 触发后时间 | 做什么 |
| ---- | -------- | ---------- | ------ |
| 解密 ŝᵀ∘û | 0 – 6519 | 0 – 31.6 µs | 3×（NTT û + BMUL）、INTT、v − (…)、编码得到 m′；**CPA 的目标在这里面** |
| G(m′‖h) | 6519 – 6623 | 31.6 – 32.1 µs | SHA3-512，得到 (K′, r′) |
| J(z‖c) | 6623 – 7375 | 32.1 – 35.8 µs | SHAKE256 吸收**整条 1088 字节密文**，得到 K̄ |
| 重加密 c′ | 7375 – 25795 | 35.8 – 125.1 µs | K-PKE.Encrypt，以 Keccak 为主 |
| 比较 / 选择 | 25795 – 26367 | 125.1 – 127.8 µs | c == c′ ? K′ : K̄ |

解密阶段里，CPA 用到的三次 BaseMul（BMUL）和它们前后的 NTT / INTT：

| 窗口 | 核心周期 | 触发后时间 |
| ---- | -------- | ---------- |
| NTT û₀ | 146 – 1143 | 0.71 – 5.54 µs |
| **BMUL 0** | 1289 – 1570 | 6.25 – 7.61 µs |
| NTT û₁ | 1712 – 2709 | 8.30 – 13.13 µs |
| **BMUL 1** | 2855 – 3136 | 13.84 – 15.20 µs |
| NTT û₂ | 3278 – 4275 | 15.89 – 20.73 µs |
| **BMUL 2** | 4421 – 4702 | 21.44 – 22.80 µs |
| INTT | 4701 – 5841 | 22.79 – 28.32 µs |

（BMUL 的数据通路实际活动 1289–1564 等 276 个周期，配置里的窗口多留了几个周期给写回的尾巴。）

所以**CPA 的记录长度至少要覆盖触发后约 23 µs**。GUI 会在波形上把三个 BMUL 窗口画成阴影带，并用虚线和文字标出上面的阶段，一眼就能看出一个特征落在哪个阶段。想看完整 Decaps 要开到 130 µs 以上，但 CPA 只用前 23 µs。

只要触发点在记录最左端、示波器报告的采样率正确，GUI 和分析脚本就能把周期换算成采样点。NTT / INTT 窗口不是 CPA 的目标（û 是公开的，ŝ 不是它们的操作数），只用来标注。

> **TVLA 的最强峰通常不在 BMUL 里。** 固定密文 vs 随机密文的 t 检验检测的是“功耗和输入有没有关系”，而整条密文会被 Keccak 吸收（J(z‖c)）、重加密又从 m′ 重新算起，这些阶段的翻转和输入强相关，且功耗更大。所以整条 Decaps 的 \|t\| 峰落在 J(z‖c) 或重加密里是**预期**的，不是窗口算错了。2026-10-17 的实测里 KPI 报告的最大 \|t\| = 9.08 在第 83485 个采样点（2.5 GSa/s，33.39 µs ≈ 第 6888 周期），正好在 J(z‖c) 里。要判断“BMUL 有没有可利用的泄漏”，看的是**BMUL 窗口内**的 \|t\|（点 *BMUL 0/1/2* 缩放）以及足够多 trace 之后的结果。

> 旧版图表为了控制点数按固定步长抽点（125 002 个点只画每第 62 个），会丢掉单点尖峰：那次图上标出的峰只有约 6.0，而 KPI 里的真实最大值是 9.08，BMUL 窗口里的峰同样可能被丢掉。现在每个区间保留 \|t\| 最大的那个采样点（`tvla_analysis.peak_indices`），峰和越过阈值的位置都在图里；旧报告请重新点一次 *Run analysis*。

### 1.4 两种实验

| | TVLA（泄漏检测） | CPA（密钥恢复） |
| --- | --- | --- |
| 问题 | 功耗和输入有没有关系？泄漏在哪里？ | 能不能从功耗里把私钥系数恢复出来？ |
| 向量 | 一半是同一条固定密文，一半是随机密文，乱序交替 | 每条都是不同的合法密文，私钥固定 |
| 需要的 trace 数 | 几千条就能看出明显泄漏 | 通常上万条 |
| 结果 | \|t\| 曲线，超过 4.5 视为检测到泄漏 | 3×256 个系数的排名、key rank、MTD |

**建议的顺序**：先做一个小的 TVLA，确认探头位置能看到泄漏、泄漏落在 BMUL 窗口里，再花时间做大的 CPA。

---

## 2. 出发前

### 2.1 确认 bitstream 是新的

运算只能用 `0x05 Run` 启动，没有 `0x05` 的旧 bitstream 只能读写寄存器，**不能采集**。这台电脑上现有 bitstream 的生成时间：

| 板子 | 文件 | 生成时间 | 能否采集 |
| ---- | ---- | -------- | -------- |
| Arty Z7-20 PS 版 | `build/vivado/arty-z7-ps_mlkem/out/arty_z7_ps_top.bit` | 2026-10-16 10:39 | 可以：包含 `0x05`、新的 LED 和 FIFO 时序修改 |
| Arty A7-100T | `build/vivado/arty-a7-100t_mlkem/pqc_mlkem.runs/impl_1/arty_a7_top.bit` | 2026-10-13 | 不行：早于 `0x05`，需要重新生成 |
| Arty Z7-20 纯 PL 版 | `build/vivado/arty-z7_mlkem/pqc_mlkem_arty_z7.runs/impl_1/arty_z7_top.bit` | 2026-10-14 | 不行：早于 `0x05`，需要重新生成 |

到了实验室也可以确认：Connection 页的 *Run link self-test* 最后一项 “0x05 Run (required for operations)” 必须是 OK。

### 2.2 软件

```bash
cd ~/Downloads/Vivado/ML-KEM-Testkit
uv sync --extra scope        # 连示波器需要 pyvisa（只需装一次）
make test                    # 可选：确认软件本身没问题
```

### 2.3 带上

- 板子和它的 USB 线（A7：micro-USB 同时供电、JTAG、串口；Z7 PS 版：micro-USB，跳线 JP4 放在 JTAG）；
- 两根示波器探头：一根接触发，一根接被测信号（或者实验室的近场电磁探头 / 电流测量装置）；
- 杜邦线或测试钩，方便从 Pmod 排针上引出触发信号；
- 网线，或者确认电脑能和示波器连到同一个网络；
- 外置硬盘（可选）：大采集的文件可能有几个 GB（见 4.5 节）。

---

## 3. 接线

### 3.1 板子上电

**Arty A7**：插上 USB，用 Vivado Hardware Manager 或 `openFPGALoader -b arty_a7_100t <bitstream>` 烧录。

**Arty Z7-20 PS 版**：跳线 JP4 放在 JTAG，插上 USB，然后**每次上电都要**运行：

```bash
uv run scripts/board/boot_arty_z7_ps.py
```

它会烧录 PL bitstream、初始化 PS、启动 UART 转发程序。运行前要先关掉占用 JTAG 的程序（`openFPGALoader --xvc`、run_vivado.sh、Vivado Hardware Manager）。如果它报 DAP 锁定，就把板子**完全断电**（拔 USB，按 PROG/SRST 不够）后再试。

**SAKURA-X**：每次上电后先烧 Spartan-6（CN10，shell 控制器 bit），再烧 Kintex-7（CN5，`sakura_x_top.bit`）。通信走板载 USB（FT2232H A 通道必须是 245 FIFO），详见 [SAKURA_X.md](SAKURA_X.md)。

**看灯确认**（见 README 的 LED 表）：

| 灯 | Arty A7 | Arty Z7 | SAKURA-X | 应该看到 |
| -- | ------- | ------- | -------- | -------- |
| 心跳 | LD7 | LD3 | LED1 | 闪烁：bitstream 已加载，时钟在运行 |
| 电源 / 复位 | LD0（RGB） | LD4（RGB） | LED10 | Arty：暗绿色，红色表示按住了复位键或时钟没锁定；SAKURA-X：LED10 亮表示时钟锁定且复位已释放 |
| 运算活动 | LD4 | LD0 | LED2 / LED3 | Arty：每次运算后亮 0.1 s；SAKURA-X：LED2 上次完成，LED3 出错 |

核心运算期间所有 LED 都保持静止，不会在功耗 trace 里引入翻转。

### 3.2 触发探头

触发信号 `trig_out` 是 LVCMOS 输出（Arty 上 3.3 V，SAKURA-X 上 **2.5 V**）：Decaps 开始时上升，持续约 127.5 µs（正好 CYCLE_COUNT 个核心周期）后下降。只有真正开始运算时才出现，寄存器读写不会产生脉冲。

需要接的是 **两根线，都在 Pmod JA 上**：触发信号（探头针尖）和它的地（探头地夹）。**JB 不用**：本设计的三块板子都没有往 JB 输出任何信号。板上丝印写着 JA 的那个排母才是要接的，靠近 PROG 按钮的是 JB，不要接。

| 板子 | 触发引脚（针尖） | FPGA 管脚 | 地线（地夹） |
| ---- | ---------------- | --------- | ------------ |
| Arty A7 | JA **第 1 脚** | G13 | 第 5 或第 11 脚 |
| Arty Z7 PS 版 | JA **第 3 脚** | Y16 | 第 5 或第 11 脚 |
| Arty Z7 纯 PL 版 | JA **第 3 脚** | Y16 | 第 5 或第 11 脚 |
| SAKURA-X | CN8 **第 3 脚** | N16 | CN8 第 11 或第 13 脚（引脚图见 [SAKURA_X.md](SAKURA_X.md)） |

#### JA 引脚图

Pmod 是 2×6 的排母：1 到 6 是一排，7 到 12 是另一排，1 和 7 并排。图里 `TRIG` 是触发信号，`---` 是本设计没有使用的脚（FPGA 不驱动，不要接），`GND` 是地，`3V3` 是 3.3 V 电源。

**Arty A7**

```
      ┌───────┬───────┬───────┬───────┬───────┬───────┐
      │   1   │   2   │   3   │   4   │   5   │   6   │
      │ TRIG  │  ---  │  ---  │  ---  │  GND  │  3V3  │
      ├───────┼───────┼───────┼───────┼───────┼───────┤
      │   7   │   8   │   9   │  10   │  11   │  12   │
      │  ---  │  ---  │  ---  │  ---  │  GND  │  3V3  │
      └───────┴───────┴───────┴───────┴───────┴───────┘
        针尖                              地夹
        接这里                            接 5 或 11
```

**Arty Z7 PS 版**（板载 USB 走 PS，JA 上只有触发）

```
      ┌───────┬───────┬───────┬───────┬───────┬───────┐
      │   1   │   2   │   3   │   4   │   5   │   6   │
      │  ---  │  ---  │ TRIG  │  ---  │  GND  │  3V3  │
      ├───────┼───────┼───────┼───────┼───────┼───────┤
      │   7   │   8   │   9   │  10   │  11   │  12   │
      │  ---  │  ---  │  ---  │  ---  │  GND  │  3V3  │
      └───────┴───────┴───────┴───────┴───────┴───────┘
                        针尖                  地夹
                        接这里                接 5 或 11
```

**Arty Z7 纯 PL 版**（1、2 脚是外接 USB-UART 的串口，不能用来接触发）

```
      ┌───────┬───────┬───────┬───────┬───────┬───────┐
      │   1   │   2   │   3   │   4   │   5   │   6   │
      │FPGA-TX│FPGA-RX│ TRIG  │  ---  │  GND  │  3V3  │
      ├───────┼───────┼───────┼───────┼───────┼───────┤
      │   7   │   8   │   9   │  10   │  11   │  12   │
      │  ---  │  ---  │  ---  │  ---  │  GND  │  3V3  │
      └───────┴───────┴───────┴───────┴───────┴───────┘
        串口    串口    针尖                  地夹
        别碰    别碰    接这里                接 5 或 11
```

`FPGA-TX` 接 USB-UART 适配器的 RXD，`FPGA-RX` 接适配器的 TXD，适配器的 GND 接第 5 或 11 脚，适配器的 VCC 不接。

#### 哪一排在上面、哪一端是 1

图只表示引脚的逻辑排列。板子摆成什么方向，哪一排在上面不一定，所以**以板上丝印为准**：先找到丝印写着 JA 的排母，再找到它的 1 号脚。A7 的约束文件注释说第 1 脚是“靠 USB 口那排里最顶上的一个”，这只是代码注释里的描述，请以你手上这块板子的丝印或 Digilent 参考手册为准。

接示波器之前，用万用表确认一次（板子上电）：

1. 红表笔放在你认为是第 6 脚的位置，黑表笔放在你认为是第 5 脚的位置，应该读到约 **+3.3 V**；
2. 如果读到 −3.3 V，说明红黑放反了，也就是你认为的 5 其实是 6，**这时千万不要把示波器地夹接到那里**；
3. 第 5 脚和第 11 脚之间应该是 0 V，第 6 脚和第 12 脚之间也是 0 V。三个都对上了，才接线。

#### 接线步骤

1. 探头针尖接触发脚（A7 是第 1 脚，Z7 是第 3 脚），地夹接第 5 或第 11 脚；
2. **地夹千万不要碰到第 6 / 12 脚（3.3 V）**，否则会把板子电源短路。这两个脚就紧挨在地脚旁边，夹之前先看清楚；
3. 这根探头接示波器上一个**不用于测量**的通道（例如 CH2），有外部触发输入（EXT TRIG）的示波器也可以接那里；
4. 测量信号的探头是另一根，接测量通道，不接 Pmod（见 3.3 节）。

### 3.3 测量信号

这个项目对传感器没有限定，分析只需要一个和核心功耗相关的信号。按实验室现有的设备选：

- **近场电磁探头**：不需要改板子，放在 FPGA 芯片上方或核心电源（VCCINT）的去耦电容附近。位置对信号强度影响很大，第一次可以边看 Setup capture 边移动，找到 BMUL 窗口里活动最明显的位置后固定住。
- **电流 / 电压测量**：在核心电源路径上串电阻（需要改板子），配合放大器或差分探头。

不管用哪一种，**记下探头的位置和型号**（后面要写进 campaign 备注），换了位置的数据不能和原来的混在一起分析。

#### 只有一个 EM 探头时怎么找位置

探头测的是 dI/dt：同一批触发器翻转，探头朝向反一下，波形就整个倒过来，而且每个时钟沿后面会拖一串响铃。
所以**不要**用"哪个采样点幅度最大"来判断位置好坏——那既依赖相位也依赖朝向。按这个顺序做：

1. **先确认记录是对的**：Check trigger 五项全绿（尤其"record starts at the trigger"），时基至少覆盖触发后 23 µs。
2. **用 TVLA 判断位置，不要用眼睛**。在每个候选位置采 300–500 条 TVLA，看 BMUL 窗口里的 \|t\|
   （Analysis → \|t\| over time → 缩放到 *BMUL 0/1/2*）。注意整条 trace 的最强峰几乎一定在 J(z‖c) 或
   重加密里（1.3 节），那个峰在任何位置都看得到，**不能用来挑位置**；要看的是三个 BMUL 窗口内的值。
3. 位置固定后再采 CPA。采样率越高越好（每个核心周期 ≥ 8 个采样点，206.25 MHz 对应 ≥ 1.65 GSa/s）：CPA 对 BMUL 窗口里的每个采样点都算相关系数，周期内的点越多，峰值落在正确采样点上的机会越大。
4. 分析时先用默认的 `hw_product_mod`，搜索范围选 **整个 BMUL 窗口**、每个多项式先看 8 个系数（[LEAKAGE_MODELS.md](LEAKAGE_MODELS.md)）。结果里的 *offset* 列是峰的位置和 RTL 预测周期的差：所有系数堆在同一个值上就说明对齐了、泄漏是真的；这个值就是探头 / 放大器延迟，填进 *shift* 后改用 *Around the predicted cycle* 再跑全部系数。
5. 同一批 trace 把 `hw_product_mod` 和 `hw_product_raw` 都跑一遍（*Compare* 标签）。**哪个明显更好，就说明探头主要耦合到哪一组触发器**（DSP 乘积寄存器还是 Barrett 输出），可以据此微调位置。

### 3.4 网络

- 在示波器的 Utility / I/O / LAN 菜单里查看它的 IP 地址（或设一个固定 IP）；
- 让电脑和示波器在同一个子网，先在终端里 `ping <示波器 IP>` 确认能通；
- 传输方式（GUI 的 *Transport*，`config.py` 的 `SCOPE_TRANSPORT`）要和示波器的远程控制设置一致：
  - **VICP**（`VICP::<IP>::INSTR`）：Teledyne LeCroy 的协议，示波器 Utilities › Remote 选 *TCPIP (VICP)*；需要 `pyvicp`（`uv sync --extra scope` 会装上）；
  - **VXI-11 / LXI**（`TCPIP0::<IP>::INSTR`）：LeCroy 选 *LXI (VXI11)*；Keysight / Rigol / Siglent / Tektronix 默认都支持；
  - **原始 SCPI 端口**（`TCPIP0::<IP>::5025::SOCKET`，Rigol / Siglent 一般是 5555，改 `config.py` 的 `SCOPE_RESOURCES`）：LeCroy 不支持。

---

## 4. 示波器设置

这一节决定数据能不能用，值得多花时间。软件**不会**替你设置示波器的模拟前端（采样率、时基、档位、触发电平），只负责选择通道、选择传输格式、每条 trace 前按一次 SINGLE、然后读数据。所有设置都在示波器面板上完成。

### 4.1 设置总表

| 项目 | 设置 | 为什么 |
| ---- | ---- | ------ |
| 测量通道 | 例如 CH1，接被测信号 | 就是 GUI 里的 *Channel* |
| 触发源 | 接 `trig_out` 的通道（例如 CH2）或 EXT | |
| 触发类型 | 边沿触发，上升沿 | `trig_out` 在 Decaps 开始时上升 |
| 触发电平 | ≈ 1.6 V（Arty，3.3 V 的一半）；≈ 1.25 V（SAKURA-X，2.5 V 的一半） | 避开噪声；SAKURA-X 上设成 2.5 V 正好卡在高电平上，会漏触发 |
| 触发模式 | **NORMAL**（不是 AUTO） | AUTO 在没有触发时也会自己采一次，Check trigger 会报错 |
| 触发位置 | **记录的最左端**（预触发 / 水平延迟为 0） | 软件认为第 0 个采样点就是 Decaps 开始 |
| 记录长度 | **≥ 25 µs**（CPA）；想看完整 Decaps 用 ≥ 130 µs | BMUL 窗口到 22.8 µs 结束 |
| 采样率 | 建议 ≥ 1 GSa/s，至少 500 MSa/s | 1 GSa/s 时每个核心周期约 5 个采样点 |
| 存储深度 | 固定值，不要 Auto | 一次采集中每条 trace 的长度必须相同 |
| 采集模式 | **Normal / Sample**，不要 Average、Peak Detect、Envelope | 平均会把不同密文的 trace 混在一起，CPA 就没有意义了 |
| 垂直档位 | 信号占屏幕高度的 70–80%，不能削顶 | Keysight / Rigol / Tektronix 以 8 位 ADC 码传输，LeCroy HDO 以 16 位字传输（12 位有效）；档位太大都会损失分辨率 |
| 垂直偏移 | 把信号移到屏幕中间 | |
| 耦合 | 交流信号（电磁探头等）可用 AC 耦合 | 去掉直流偏置，档位可以调得更细 |

### 4.2 触发位置为什么重要

采集时会把示波器报告的“第一个采样点相对触发的时间”（LeCroy 的 `HORIZ_OFFSET`）存进 campaign（`record_start_s`），GUI 和 CPA 按**触发后的时间**放 BMUL 窗口，所以触发点在屏幕中间（有预触发数据）时位置也是对的；但驱动不报告这个值的示波器（Keysight / Rigol / Siglent / Tektronix 的通用驱动）、以及旧 campaign，仍按“第 0 个采样点 = Decaps 开始”处理，这时预触发数据会让窗口错位。无论如何，**触发点放在屏幕最左边**仍然最稳妥，也是 *Check trigger* 检查的内容。记录必须覆盖触发后 0 … 约 23 µs（预触发部分不需要）。

不同品牌的叫法不同，目标都是让**触发点出现在屏幕最左边**：

- Keysight InfiniiVision：Horizontal 菜单中 Time Ref 选 Left，延迟设为 0；
- Rigol / Siglent：调水平位移（Horizontal Position / Delay），把屏幕上方的触发位置标记移到最左边；
- Tektronix：Horizontal Position 设为 0%（或关闭 Delay 后把位置设为 0%）；
- Teledyne LeCroy（HDO / WaveRunner / WavePro）：用 Horizontal 区的 *Delay* 旋钮把触发位置移到网格最左边：屏幕下方的时间轴应从 0 开始（默认是触发在正中，时间轴从 −5 格开始）。LeCroy 驱动会读回第一个采样点相对触发的时间，*Check trigger* 的第 4 项和 *Capture* 的 “trigger position” 直接告诉你是否对齐。

具体菜单以你用的示波器为准。4.4 节有一个用软件验证触发位置的方法。

### 4.3 采样率、记录长度、文件大小

每条 trace 以 float32 保存（文件内 gzip 压缩，实际会小一些）。上限估计：

| 记录长度 | 采样率 | 每条采样点 | 每条大小 | 10 000 条 |
| -------- | ------ | ---------- | -------- | --------- |
| 25 µs | 500 MSa/s | 12 500 | 50 kB | 0.5 GB |
| 25 µs | 1 GSa/s | 25 000 | 100 kB | 1.0 GB |
| 25 µs | 2.5 GSa/s | 62 500 | 250 kB | 2.5 GB |
| 130 µs | 1 GSa/s | 130 000 | 520 kB | 5.2 GB |

采样点越多，网络传输越慢、分析越吃内存。**CPA 的第一次实验建议用 25–30 µs、1 GSa/s 左右**；想探索整个 Decaps 的泄漏，可以另做一个小的 TVLA，用长记录。

### 4.4 用软件验证触发位置（推荐做一次）

1. 在 GUI 的 Connection 页把示波器的 *Channel* 临时改成**触发通道**（例如 `CHANnel2`），断开后重新 Connect；
2. 到 Acquisition 页点 *Capture*；
3. 看到的应该是一个从 x = 0 就开始的高电平（≈ 3.3 V），一直持续到约 127.5 µs（或者持续到记录结束）；
4. 如果高电平是从中间某处才开始的，说明触发点不在最左端，回到 4.2 节调整；
5. 把 *Channel* 改回测量通道，重新 Connect。

### 4.5 采集中不要动示波器

开始采集后不要改时基、采样率、存储深度或档位。软件会检查每条 trace 的长度：如果和开始时不同（说明时基或存储深度被改了），采集会**立即停止**，报错 “the scope returned N samples but this campaign records M per trace”。已经采到的数据完整保留，把设置改回去后可以续采（见 7.4 节）。

---

## 5. 在 GUI 中连接

```bash
make gui        # 打开 http://127.0.0.1:8765
```

### 5.1 板子

Connection 页左边：

1. *Serial port*：选标着 “recommended” 的那个，也就是 `…1` 结尾的口（`…0` 是 JTAG 通道，不是串口）；
2. *Baud rate* 保持 115200；
3. 点 *Connect*，状态变成 connected 后，下面会显示核心的版本信息；
4. 点 *Run link self-test*，4 项都应该是 OK：

| 检查项 | 内容 | 失败时 |
| ------ | ---- | ------ |
| link identity (ALG_ID) | 读回 1 = ML-KEM | 串口选错、板子没烧录、Z7 PS 版没跑 boot 脚本 |
| datapath reset | 复位后核心空闲 | 按一下 BTN0 后重试 |
| bulk transfer (1 KB) | 1 KB 数据回环一致 | 链路不稳：换 USB 线 / 接口 |
| 0x05 Run (required for operations) | bitstream 支持完成通知 | bitstream 太旧，需要重新生成（2.1 节） |

可以勾选 *Reconnect automatically*：USB 松动时会自动重连。

### 5.2 示波器

Connection 页右边：

| 字段 | 填什么 |
| ---- | ------ |
| IP address | 示波器的 IP |
| Driver | Teledyne LeCroy（HDO / WaveRunner / WavePro），Keysight / Rigol / Siglent，或 Tektronix |
| Transport | 和示波器的远程控制设置一致（3.4 节）：LeCroy 用 VICP 或 VXI-11 / LXI |
| Channel | **测量通道**：LeCroy 写 `C1`…`C4`，Keysight / Rigol / Siglent 写 `CHANnel1`，Tektronix 写 `CH1`（切换 Driver 时会自动改写法） |
| Points per trace | 先保持 0（传输示波器给出的记录）。如果 6.3 节发现传回来的点数远少于示波器屏幕上的“采样率 × 记录长度”，就在这里填完整点数（例如 25 µs × 1 GSa/s = 25000），重新 Connect |
| Timeout (ms) | 保持 20000；记录很长、网络慢时可以加大 |

点 *Connect*，下面会显示 `*IDN?`（示波器型号）和它报告的采样率；LeCroy 还会显示触发设置（源和电平）、时基、ADC 位数，以及上一次记录的起点相对触发的时间。不知道 IP 时可以点 *Find instruments* 搜索（搜不到就直接填 IP）。点 *Save as default* 会把 IP、通道、驱动和传输方式写进 `config.py`，下次自动填好。

---

## 6. 采集前的检查

### 6.1 生成向量集，先在板上核对

Test Vectors 页：

1. *Purpose* 选 CPA 或 TVLA，*Vectors* 填数量，*Seed* 留空（自动随机生成并记录下来，同样的设置和种子可以重新生成完全相同的向量集）；
2. CPA 的 *Ciphertexts* 第一次用默认的 *Valid encapsulations*；
3. 点 *Generate vector set*。生成非常快（2 万条也只需要几秒），生成时已经用软件 FIPS 203 核对过期望 K；
4. 右边列表里选中这个向量集，可以命名、打标签（例如 `cpa`、`key-A`）；
5. 下方 *Verify on the board* 卡片：*first 20 vectors* → *Run on board*，应该 **20/20 ✓**，并且显示 constant time（CYCLE_COUNT 每条相同）。

如果板上核对失败，后面的采集也一定会失败，先解决这里。

### 6.2 Check trigger

Acquisition 页下方 *Setup capture* 卡片，点 *Check trigger*。它会自动做三件事，结果显示在表格中：

| 检查 | 含义 | 失败时怎么办 |
| ---- | ---- | ------------ |
| no trigger without an operation | 示波器已 arm，PC 只读写寄存器和数据缓冲，不启动运算：示波器**不能**触发 | 触发模式改成 NORMAL（不是 AUTO）；触发源选错了通道；电平太低被噪声触发 |
| trigger on the Decaps start | 执行一次 Decaps：示波器**必须**触发 | 探头没接在 JA1（A7）/ JA3（Z7）；地没接；触发源不是这个通道；不是上升沿；电平不对 |
| Decaps result | 这次 Decaps 的 K 和 FIPS 203 一致 | 回到 5.1 节自检 |
| record covers the BMUL windows | 记录长度覆盖了全部三个 BMUL 窗口 | 加长时基，至少到触发后 23 µs |

全部 PASS 后，Acquisition 页左上角检查清单中的 “Trigger checked” 会打勾。

### 6.3 Setup capture：调垂直档位

点 *Capture*：板子用一组新的随机向量做一次 Decaps，示波器采一条波形显示出来，最近 5 条叠在一起方便比较。上方的信息块：

| 信息块 | 看什么 |
| ------ | ------ |
| Decaps | K ✓ |
| coverage | 三个 BMUL 窗口都在记录里 |
| sample rate | 传回来的数据的采样率和点数。**应当和示波器屏幕上的采样率一致**，点数 ≈ 采样率 × 记录长度。明显偏低（例如只有 1000 或 1200 点）说明示波器只传了抽取后 / 屏幕分辨率的数据：在 Connection 页的 *Points per trace* 填完整点数（5.2 节）。显示 unknown 说明示波器没有报告采样率，窗口位置会算错 |
| range | 最小值 … 最大值。数据是 8 位 ADC 码：Keysight / Rigol / Siglent 是 0–255，Tektronix 是 −128–127。**出现 0 或 255（−128 或 127）说明削顶了**，要调大垂直档位；范围只占很小一段说明档位太大，可以调小 |

一边调档位、偏移和探头位置，一边反复点 *Capture*，直到：信号不削顶、占满大部分量程、三个阴影窗口里能看到明显的活动。

修改示波器设置后不需要重新连接，下一次 Capture 会自动读到新的采样率。

---

## 7. 正式采集

### 7.1 开始

Acquisition 页左边 *Campaign* 卡片：

1. 检查清单四项都打勾：Board connected、Oscilloscope connected、Test-vector set chosen、Trigger checked；
2. *Test-vector set*：选要用的向量集（Test Vectors 页的 *Use for acquisition →* 也会直接跳到这里并选好）；
3. *Samples per trace*：**保持 0**。开始时软件会先用一次额外的 Decaps 测出示波器的记录长度，之后每条都按这个长度保存；
4. *Resend dk every*：保持 50。每 50 条重新上传一次私钥，防止板子意外复位后用错密钥；
5. 点 *Start acquisition*。

### 7.2 采集中看什么

- **进度条**：已完成条数、速度、剩余时间；
- **Live preview**：*latest* 是最新一条 trace，*mean* 是运行均值；TVLA 还可以勾选 *fixed − random*，看两类均值的差，**差值在 BMUL 窗口里出现明显的峰，说明能看到泄漏**；
- **每条都会核对**：
  - K 和向量集里预先算好的期望值比较。不一致（板子被复位、链路出错）会标记这一条，下一条前自动重写配置；
  - CYCLE_COUNT 必须每条相同。一旦变化，说明出现了时序侧信道，是一个需要记录的发现。

采集期间板子和示波器由采集任务独占，其他操作（寄存器读写、Capture）会被拒绝。

### 7.3 停止

随时可以点 *Stop*，当前这条结束后停止。文件是完整、可续采的，已采数据不会丢。采完后会显示 traces、K correct、integrity、CYCLE_COUNT 四个指标，可以直接点 *Open in Trace Library* 或 *Analyse*。

### 7.4 续采

以下情况会留下一个可续采的文件：点了 Stop、USB 断开（GUI 提示 link lost）、示波器记录长度被改动（4.5 节）、连续多次核心错误。

处理好原因后（重新连接板子或示波器、把示波器设置改回去），在 Acquisition 页展开 *Resume a stopped campaign* 选择这个文件，再点 *Start acquisition*；也可以在 Trace Library 的详情页点 *Resume capture*。续采会从文件里的种子重建私钥，并核对它和文件记录的 dk 哈希一致。

---

## 8. 采集后

### 8.1 在 Trace Library 中整理

文件保存在 `data/traces/<日期时间>_<cpa|tvla>.hdf5`。文件里自动记录了：向量集的生成参数（种子）、板子信息、示波器型号和波形参数、采样率、每条的 K 和周期数。**示波器的档位、探头位置不会自动记录**，请在详情页手动补上：

- **Name**：例如 “EM 探头 A 点 1 GSa/s”；
- **Tags**：例如 `em-probe`、`pos-A`、`1GSa`、`2026-10-16`、`baseline`。之后可以按标签筛选；
- **Notes**：探头型号和位置、通道、档位、耦合方式、带宽限制、触发电平、实验时的观察。

详情页同时会显示 K 正确率、CYCLE_COUNT 分布、trace 是否覆盖了三个 BMUL 窗口、前几百条的均值 ± σ。*View traces* 可以逐条或按类别叠加查看。

不需要的 campaign 可以 *Move to trash*（可恢复），在回收站里再永久删除。

### 8.2 第一次分析

Analysis 页顶部选 campaign，点 *New analysis*：

- **TVLA**：直接 *Run analysis*。看 \|t\| 曲线：超过 ±4.5 的峰说明检测到泄漏。峰会标出它落在哪个阶段（KPI 里的 *peak at*、点击读数）；整条 Decaps 的最强峰在 J(z‖c) 或重加密里是预期的（见 1.3 节），要看 BMUL 有没有泄漏，用工具栏缩放到 *BMUL 0/1/2* 看窗口内的 \|t\|。
- **CPA**：*Hypothesis* 先用 `hw_product_mod`（默认），*What to attack* 先用前 8 个系数、*Whole BMUL window*（[LEAKAGE_MODELS.md](LEAKAGE_MODELS.md)）；*Scoring* 选 *Blind attack*（真实攻击，不使用真实密钥）或 *Rank the campaign key*（用真实密钥给猜测排名，结果会标注 oracle-assisted，**不能当作真实攻击结果展示**）。结果在 *Overview* 里是一行一个系数的表（峰值、是噪声底的几倍、峰值位置和 RTL 预测的差、稳定性），*Coefficient* 里是单个系数的五张图（相关系数随时间、候选排名、随 trace 数的收敛、top-10、热图）；换另一个假设再跑一次，在 *Compare* 标签里对比。

结果怎么解读，见 [cpa_toolkit/README.md](../cpa_toolkit/README.md) 第 7、8 节。

---

## 9. 建议的第一次实验流程

| 步骤 | 内容 | 时间 |
| ---- | ---- | ---- |
| 1 | 接线、上电、看心跳灯；GUI 连接板子并自检 | 10 min |
| 2 | 设置示波器（第 4 节）；GUI 连接示波器；用触发通道验证触发位置（4.4 节） | 15 min |
| 3 | Check trigger 全部 PASS；Setup capture 调档位和探头位置 | 15–30 min |
| 4 | 生成 TVLA 向量集 2000 条，Run on board 20 条 | 2 min |
| 5 | **TVLA 采集 2000 条** | 约 4–7 min |
| 6 | Analysis → TVLA：确认 \|t\| 峰出现、位置合理。没有泄漏就回到第 3 步换探头位置 | 2 min |
| 7 | 生成 CPA 向量集 10 000–20 000 条 | 1 min |
| 8 | **CPA 采集** | 约 20–70 min |
| 9 | 命名、打标签、写备注；Analysis → CPA | — |

步骤 5 和 8 的时间按每条 0.1–0.2 s 估计，以 GUI 显示的实际速度为准。

每次改变探头位置或示波器设置，都应该当作一个新的 campaign，并在备注里写清楚。

---

## 10. 常见问题

| 现象 | 处理 |
| ---- | ---- |
| 连不上板子，“no response from the core” | 选的不是 `…1` 接口；板子没烧录；Z7 PS 版每次上电要先运行 `boot_arty_z7_ps.py` |
| 串口 “busy” | 端口被别的程序占用（另一个 GUI、openFPGALoader、Vivado Hardware Manager） |
| 自检 “0x05 Run” 失败 | bitstream 太旧，需要重新生成（2.1 节） |
| Z7 boot 脚本报 DAP 锁定 | JP4 放在 JTAG，板子完全断电（拔 USB）后重试 |
| 示波器 `cannot open …` | IP 不对或不在同一网段（先 `ping`）；示波器的 LAN / VXI-11 没打开；改用 SOCKET 资源（3.4 节） |
| `pyvisa is missing` | `uv sync --extra scope` |
| Check trigger：没有运算时示波器也触发了 | 触发模式是 AUTO，改为 NORMAL；或电平太低、触发源选错 |
| Check trigger：Decaps 后没有触发 | 探头 / 地线没接好；触发源不是接 `trig_out` 的通道；不是上升沿；电平不对 |
| `scope did not trigger within 10 s` | 同上；或采集中触发线松了 |
| Check trigger：BMUL 窗口超出记录 | 时基太短，加长到触发后 ≥ 23 µs |
| sample rate 显示 unknown | 示波器没有在波形参数里报告采样率；检查方言是否选对 |
| Setup capture 的点数 / 采样率比示波器屏幕上小很多 | 示波器只传了抽取后的数据：在 *Points per trace* 填完整点数后重新 Connect |
| Capture 的 range 出现 0 或 255 | 削顶：调大垂直档位或调整偏移 |
| 波形上 BMUL 窗口的位置看起来不对 | 触发点不在记录最左端（4.2、4.4 节） |
| 采集停止：“the scope returned N samples but this campaign records M” | 采集中改了时基或存储深度；改回原来的设置后续采 |
| K 正确率 < 100% | 采集中按了 BTN0 或链路出错；这些 trace 已被标记，分析时会自动排除 |
| CYCLE_COUNT 出现变化 | 说明核心对这个输入不是恒定时间的，记录下来（这本身是一个发现） |
| 采集很慢 | 115200 波特率下上传密文约 94 ms/条是上限；记录太长时网络传输也会变慢 |

---

## 附录 A：命令行采集

GUI 和命令行调用的是同一套函数，长时间无人值守的采集也可以用命令行：

```bash
cd cpa_toolkit
uv run python acquire_cpa.py ports                                   # 列出串口
uv run python acquire_cpa.py check -p /dev/cu.usbserial-XXXX1        # 链路自检
uv run python acquire_cpa.py scopes                                  # 列出 VISA 仪器
uv run python acquire_cpa.py cpa-run  -p <串口> --ip <示波器IP> --vectors ../data/vectors/<set>.npz
uv run python acquire_cpa.py tvla-run -p <串口> --ip <示波器IP> -n 2000
uv run python acquire_cpa.py resume   -p <串口> --ip <示波器IP> ../data/traces/<campaign>.hdf5
```

`--samples 0`（默认）表示用一次采集测出示波器的记录长度，和 GUI 一样。

## 附录 B：关键数字

| 项目 | 数值 |
| ---- | ---- |
| 核心时钟 | 206.25 MHz（4.85 ns / 周期） |
| Decaps | ≈ 26 300 周期 ≈ 127.5 µs（同一密钥下恒定） |
| 触发 | `trig_out` = STATUS.busy，3.3 V，上升沿 = Decaps 开始，高电平 = CYCLE_COUNT 个周期 |
| BMUL 窗口 | 6.25–7.61 µs、13.84–15.20 µs、21.44–22.80 µs（NTT/INTT 和各阶段见 1.3 节） |
| 最短记录长度 | 触发后 ≈ 23 µs |
| 串口 | 115200 8N1，密文 1088 B ≈ 94 ms，私钥 2400 B |
| TVLA 阈值 | \|t\| > 4.5 |
| 数据文件 | `data/vectors/*.npz`（向量集）、`data/traces/*.hdf5`（campaign）、`data/analysis/`（报告） |
