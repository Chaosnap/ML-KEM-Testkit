# cpa_toolkit：ML-KEM-768 Decaps 的 CPA / TVLA 侧信道实验平台

这个工具对板上 ML-KEM-768 核心的 Decaps 做实测侧信道评估：真实板子加真实示波器。它包含两套相互独立的实验：

- **CPA**（相关功耗分析）：私钥固定，输入 N 条不同的密文，逐系数恢复私钥。攻击目标是 `HD(R_prev, BaseMul(û, ŝ_guess) mod q)`，设计见 [`../docs/ATTACK_MODEL.md`](../docs/ATTACK_MODEL.md)。
- **TVLA**（Welch's t 检验）：私钥固定，使用 fixed 和 random 两组密文，检测功耗是否与输入相关。这个实验不涉及任何密钥假设。

主机与板子之间的通信协议见 [`../docs/UART_PROTOCOL.md`](../docs/UART_PROTOCOL.md)。第一次去实验室采集，先读 [`../docs/TRACE_ACQUISITION.md`](../docs/TRACE_ACQUISITION.md)（接线、示波器设置、逐步操作、排错）。

---

## 1. 文件

| 文件 | 作用 |
| ---- | ---- |
| `config.py` | **需要修改的配置**：示波器 IP、串口、BMUL 窗口、CPA 默认模型 |
| `mlkem768.py` | FIPS 203 ML-KEM-768 软件实现（KeyGen / Encaps / Decaps，含批量版本），已与 NIST ACVP 向量核对 |
| `mlkem_ref.py` | NTT / BaseMul / ByteDecode 等 FIPS 203 底层运算、解密自检、结构化密文 |
| `models/` | **泄漏模型，一个文件一个模型**（第 6 节）；`base.py` 是共用部分（BaseMul 中间值、R_prev、汉明重量 / 距离、自检） |
| `testvectors.py` | 测试向量集：由种子确定性生成，包含每条密文的期望 K，存为 `data/vectors/*.npz` |
| `uart_target.py` | 板子侧驱动：`Link` 负责收发帧，`Target` 提供分步操作（reset / configure / load / run，对应论文的 x/m/s/c/e/a）；自动识别 `0x05` Run |
| `connection.py` | 非阻塞的连接/断开（可随时断开），以及链路监视（空闲时 ping，检测串口被拔出） |
| `scope.py` | 示波器：`VisaScope`（VISA/SCPI，Keysight / Rigol / Siglent / Tektronix 方言） |
| `hdf5_trace_store.py` | campaign 文件（`.hdf5`）的增量写入、读取与续采 |
| `acquire_cpa.py` | 采集：CPA 或 TVLA，按向量集逐条采集并逐条校验 |
| `cpa_attack.py` | CPA：相关系数、key rank、`--curve`（key rank 随 trace 数的变化及 MTD）、CSV |
| `tvla_analysis.py` | TVLA：Welch's t、检测曲线 |
| `web_gui.py` + `web_static/` | Web GUI（第 2 节） |

---

## 2. Web GUI

```bash
uv run python cpa_toolkit/web_gui.py              # http://127.0.0.1:8765，自动打开浏览器
uv run python cpa_toolkit/web_gui.py --port 8900 --no-browser
```

GUI 只依赖标准库 `http.server`，加上随仓库附带的 Plotly.js，不需要联网，只绑定 `127.0.0.1`。左侧导航按实验顺序排列，顶栏的指示灯显示板子、示波器和后台任务的状态，底部可展开 Console 查看完整日志。

| 页面 | 内容 |
| ---- | ---- |
| **Connection** | 串口列表：标出推荐接口；Digilent 板的 `…0` 是 JTAG 通道，会被标注出来。连接和断开不阻塞，可随时断开，可开启自动重连。链路自检中的最后一项显示当前 bitstream 是否支持 `0x05` 完成通知。示波器部分可以搜索 VISA 仪器（点击一行即填入 IP），也可以把设置存为 `config.py` 的默认值 |
| **Test Vectors** | 生成 CPA（valid / invalid / structured）或 TVLA（fixed vs random）向量集。可以逐行查看 c / m / K（支持复制），导出 csv / json / dk / ek，用软件 Decaps 复核，或在板上跑前 N 条，核对 K 和恒定周期。向量集可以命名、加标签（见下文“标签与删除”），可以删除到回收站；删除前会提示有几个 campaign 用过它——这些 campaign 不受影响，因为每个 campaign 文件自带密文、期望 K 和种子 |
| **Acquisition** | 开始前会列出检查项（板子、示波器、向量集）。采集时实时显示最新 trace、均值和 fixed−random 差值，以及速度和剩余时间；可随时停止，文件可以续采。另有 Setup capture：用一次性向量抓一条波形，用来调触发、量程和时基；*Check trigger* 检查示波器只在 Decaps 开始时触发（无运算时不触发、Decaps 时触发、波形覆盖全部 BMUL 窗口），结果也显示在检查项里 |
| **Trace Library** | 所有 campaign 的列表，可搜索、按模式和状态筛选、排序。详情页显示 K 正确率、CYCLE_COUNT 分布、trace 是否覆盖全部 BMUL 窗口、来源信息（向量集、种子、板子、示波器），可以命名、写备注、加标签。波形查看器可以按序号、类别或指定索引叠加 trace，并显示均值 ± σ 带。支持导出 .hdf5 和 .npz 片段。回收站里可以恢复或永久删除；永久删除时，由该 campaign 生成的分析报告也一起删除 |
| **Analysis** | 顶部选 campaign（下拉项和信息条上显示标签）和已保存的报告（下拉列出所有历史报告）。*New analysis* 打开配置面板，按三步填写：1 泄漏模型（列出 `models/` 里的全部模型，按 HW / HD 等分组，显示公式、寄存器、流水线拍、所需输入和局限，可选该模型接受的 R_prev，可运行模型自检）、2 打分方式（盲攻击 / 用真实密钥打分）、3 运行；运行时显示进度。*Report ▾* 菜单可以用同样的设置重跑、下载 .json / .csv、删除报告。每份报告顶部列出它的配置（模型、寄存器 / R_prev、打分方式、trace 数、时间）和关键指标，下面分标签页。CPA 有五个标签：**Key map**（3×256 系数热力图，可切换 peak \|ρ\| / 真实密钥排名 / 是否恢复；每个多项式的汇总；点击任一格打开该系数的相关曲线，← → ↑ ↓ 切换）、**Convergence**（key rank 随 trace 数的变化，标出 MTD；跨 campaign 的成功率）、**Coefficients**（可排序、可筛选的表，点击一行跳到 Key map）、**Compare models**（这个 campaign 的全部 CPA 报告，一个模型 / R_prev 一行，对比 rank-1、中位排名、峰值 \|ρ\|、MTD，点击一行打开该报告）、**Details**（完整的配置与文件信息）。TVLA 有四个标签：\|t\| 曲线（可一键缩放到各 BMUL 窗口，点击图中任意位置读取该采样点）、类均值、检测曲线、Details。报告保存在 `data/analysis/` |
| **Advanced** | **Link settings**：操作超时；显示当前 bitstream 是否支持 0x05 Run（不支持则所有运算被禁用，需要重新生成），可重新探测。**Buffer layout**：dk / c / K 以及 KeyGen、Encaps 输入输出所在的偏移，点击即读取该区域。**Protocol steps**：按 SimpleSerial 的 x / m / s / c / e / a 逐步执行一次运算（复位、设模式、发 dk、发 c、运行并等待、读结果），每步记录在 Step log 中，Run 的结果显示 STATUS 各位、周期数、错误码和输出，并与选中的测试向量的期望 K 比较；也可以一键跑完整 Decaps。**Registers**：一次读出全部寄存器并解码（STATUS 各位、OP_MODE、ERROR_CODE、VERSION）。**Data buffer**：原始读写 |

GUI 和下面的命令行工具调用的是同一套函数，所以结果一致。如果要做很长的采集，也可以直接用命令行。

### 标签与删除

Test Vectors 和 Trace Library 两页的管理方式相同：

- **标签**：在详情里输入，回车或逗号确认，退格删除最后一个，也可以从已有标签中选。每个对象最多 16 个，每个最长 32 个字符，大小写不同算同一个。列表中点击标签即按该标签筛选；工具栏的标签下拉框列出所有标签和数量；搜索框也会匹配标签。campaign 的标签存在 HDF5 元数据里，向量集的名称和标签存在旁边的 `<set>.meta.json` 里。
- **批量操作**：勾选多行（表头的勾选框选中当前筛选结果的全部行），上方会出现操作栏：*Tags…* 批量加减标签，*Move to trash* 移入回收站。正在采集的 campaign 不能被改动。
- **删除分两步**：先移入回收站（`data/traces/.trash/`、`data/vectors/.trash/`），名称、备注、标签随文件一起移动，可以恢复。在回收站视图中可以 *Delete permanently* 或 *Empty trash*，这一步不可撤销。永久删除 campaign 时，从它生成的分析报告（.json 和 .csv）也一起删除；删除向量集不影响已采集的 campaign，而且用同样的设置（用途、密文类型、数量）和种子可以重新生成完全相同的向量集。

---

## 3. 测量前先确认

1. **模型数学正确**：在 Analysis → New analysis 中选好模型后点 *Self-test this model*，或执行 `make test`。如果模型错了，CPA 会表现为怎么都打不出来，很容易被误以为是硬件做了防护。
2. **向量正确**：生成向量集时已经用软件 Decaps 核对过期望 K；在 GUI 中可以再点一次 *Verify in software*。
3. **板子正确**：在 Test Vectors → *Run on board* 跑前 20 条，应当全部 K 一致，且 CYCLE_COUNT 恒定。
4. **触发和时基正确**：在 Acquisition 点 *Check trigger*：示波器必须只在 Decaps 开始时触发，波形要覆盖全部三个 BMUL 窗口（触发后约 23 µs）。再用 *Setup capture* 调量程。

分析流程本身的正负对照（用注入的人工泄漏验证"相关计算 → 排名"这条链路没有 bug）已经做过，结果记录在 [`ATTACK_MODEL.md`](../docs/ATTACK_MODEL.md) 第 8 节：负对照的 rank-1 比例为 0%，正对照为 100%。

---

## 4. 命令行

```bash
cd cpa_toolkit
P="uv run python"

# 测试向量
$P testvectors.py gen --mode cpa -n 20000 --strategy valid       # 也可选 invalid / structured --poly 0 --pair 5
$P testvectors.py gen --mode tvla -n 20000                       # 加 --invalid-random 则 random 类为随机字节
$P testvectors.py list
$P testvectors.py verify ../data/vectors/<set>.npz
$P testvectors.py export --format csv ../data/vectors/<set>.npz

# 采集
$P acquire_cpa.py ports                                          # 找板子的 UART 口
$P acquire_cpa.py check -p /dev/cu.usbserial-XXXX1               # 链路自检（不需要示波器）
$P acquire_cpa.py scopes                                         # 列出 VISA 仪器
$P acquire_cpa.py cpa-run  -p <串口> --ip <示波器IP> --vectors ../data/vectors/<set>.npz
$P acquire_cpa.py tvla-run -p <串口> --ip <示波器IP> -n 20000 --seed <hex>
$P acquire_cpa.py resume   -p <串口> --ip <示波器IP> ../data/traces/<campaign>.hdf5

# 分析
$P cpa_attack.py ../data/traces/<campaign>.hdf5                          # 真实攻击（不给 --dk-ref）
$P cpa_attack.py ../data/traces/<campaign>.hdf5 --dk-ref dk.bin --curve  # oracle-assisted 打分 + 曲线
$P cpa_attack.py --list-models                                          # models/ 里的全部模型
$P cpa_attack.py ../data/traces/<campaign>.hdf5 --model hw_scalar        # 换一个泄漏模型
$P cpa_attack.py ../data/traces/<campaign>.hdf5 --model hd_scalar --rprev zero
$P tvla_analysis.py ../data/traces/<campaign>.hdf5 --csv t.csv
```

采集过程中的检查：

- **每条 trace 都检查 K**：板子输出的 K 与向量集中预先算好的期望值逐条比较。如果不一致，说明板子被复位、密钥不对或链路出错，该 trace 会被标记，并在下一条前重写配置寄存器。**每条 trace 也检查 CYCLE_COUNT**：周期数变化就说明出现了时序侧信道。
- **dk 只在需要时重发**：默认每 50 条重发一次（`--resend-dk-every`），其余 trace 只上传 1088 字节的密文。
- **触发**：使用 `trig_out`（`STATUS.busy`）。Arty A7 接 Pmod JA1，Arty Z7 接 Pmod JA3。
- **BMUL 窗口**（`config.py: BMUL_WINDOWS`）来自 Decaps 的周期精确仿真。修改 RTL 或微码后需要重新测量，方法见 `ATTACK_MODEL.md` 第 4.3 节。

CPA 的密文策略（`--strategy`）：

| 策略 | 说明 |
| ---- | ---- |
| `valid`（默认） | 每条都是一次全新的、合法的 FIPS 203 封装 |
| `invalid` | 随机字节当密文，走隐式拒绝路径（BaseMul 照样执行） |
| `structured` | 只让一个 BaseMul 对（`--poly` / `--pair`）的 `û` 非零，其余清零，提升该点的信噪比，见 `ATTACK_MODEL.md` 第 7 节 |

TVLA 中，class 0 是同一条固定密文的重复（`m0 = SHA3-256("pqc-testkit tvla fixed m" ‖ seed)`），class 1 每次都是新封装（加 `--invalid-random` 时为随机字节）。两类以确定的乱序交替出现。

**不给 `--dk-ref` 才是真实的“从零开始”CPA**：脚本不知道真实密钥，只报告每个系数的最佳猜测及其相关系数峰值。

---

## 5. 数据文件

| 位置 | 内容 |
| ---- | ---- |
| `data/vectors/*.npz` | 测试向量集：dk、ek、密文、类别、期望 K，以及生成参数（recipe） |
| `data/vectors/<set>.meta.json` | 向量集的名称和标签（GUI 中编辑；.npz 本身不改） |
| `data/vectors/.trash/` | 被删除的向量集（连同 .meta.json） |
| `data/traces/*.hdf5` | campaign：`/traces`、`/intermediates/ct`、`/intermediates/k_expected`、输出、周期数、逐条检查结果，以及元数据（向量集 recipe、板子、示波器、采样率、名称/备注/标签） |
| `data/traces/.trash/` | Trace Library 的回收站 |
| `data/analysis/*_report.json` | CPA/TVLA 报告：完整数组和模型配置，GUI 可以直接重新打开 |

---

## 6. 泄漏模型（`models/`）

每个泄漏模型是 `cpa_toolkit/models/` 里的一个文件，模型名就是文件名。命令行（`--model`）、GUI 和报告都用这个名字：

| 模型（文件） | 寄存器 | R_prev（第一个为默认） | 假设 |
| ------------ | ------ | ---------------------- | ---- |
| `hw_scalar` | `p_self`，12 位，`p00`/`p11`，T+11 | 无 | `HW(û[j]·ŝ_guess mod q)`：单次 Barrett 乘积的汉明重量 |
| `hd_scalar` | `p_self` | prev-word / zero | `HD(R_prev, û[j]·ŝ_guess mod q)`：同一寄存器，R_prev 为前一对的乘积 |
| `hd_basemul_c0` | `c0`，12 位，`h0`，T+17 | prev-word / zero / acc | BaseMul 输出的偶系数（含累加） |
| `hd_basemul_c1` | `c1`，12 位，`h1d[4]`，T+18 | prev-word / zero / acc | BaseMul 输出的奇系数（含累加） |
| `hd_basemul_word`（默认） | `word`，24 位，`wb`/`rb_din`，T+21 | prev-word / zero / acc | `HD(R_prev, {c1, c0})`：24 位 RAM 写回字，物理上最接近真实探头 |

R_prev 的含义：`zero` = 前态为 0（即汉明重量）；`prev-word` = 同一遍 BMUL 中上一个字；`acc` = 前面多项式的累加和 C_old。每个模型对应哪个 RTL 寄存器、哪一个时钟拍，完整推导见 [`ATTACK_MODEL.md`](../docs/ATTACK_MODEL.md) 第 5 节。旧的预设名（`HW`、`HD`、`basemul-c0`、`basemul-c1`、`basemul-word`）仍然可以用，旧报告也会自动对应到新的模型名。

**比较不同模型**：对同一个 campaign 用不同的模型（或 R_prev）各运行一次 CPA，然后在报告的 *Compare models* 标签中对比。只在相同条件下比较：同样的 trace 数，打分模式下看 rank-1 比例和真实密钥的中位排名，盲攻击模式下看峰值 |ρ|。

**加一个新模型**：

1. 复制 `models/_template.py` 为 `models/<名字>.py`（小写，不以 `_` 开头）；
2. 选寄存器（`p_self` / `c0` / `c1` / `word`）、它接受的 R_prev（或不用），写 `leak(x, r_prev)`：`x` 是（trace × 候选值）的寄存器值，返回每个 (trace, 候选值) 的假设泄漏；
3. 在 GUI 的模型下拉框旁点 ↻（或刷新页面），新模型就出现在列表里，不需要重启 GUI；
4. 先运行 *Self-test this model*：它检查模型看到的寄存器值和硬件实际计算的值一致（与整多项式 BaseMul 独立计算的结果比较），并且假设确实随猜测值变化。

模板里的示例是一个“单比特”模型（24 位写回字的第 0 位，DPA 风格）。`models/base.py` 提供 `hw()`、`hd()` 和 `Guess`（给定猜测值时整个 BaseMul 对的中间值），需要更特殊的假设时可以直接重写 `hypotheses()`。

---

## 7. 这个评估能回答什么、不能回答什么

**能回答的问题**：在这套假设模型、这个 trace 数、这个示波器设置下，相关系数能否从 3329 个候选中把密钥系数挑出来。

- 能挑出来：说明这个具体攻击在这些条件下成功，**现有实现（未加防护时）确定不安全**。
- 挑不出来：只能说这一次没打穿，**不能**说明它安全。

**不能回答的问题，以及常见的误判来源：**

1. **trace 数不够，不等于没有泄漏。** 先用 TVLA 确认泄漏的量级和位置，再决定 CPA 需要多少条 trace。
2. **窗口或触发没对齐，会把真实泄漏淹没。** 先用 Setup capture 和 Trace Library 的波形查看器确认 BMUL 窗口落在波形中；TVLA 的 |t| 峰值也应该落在窗口内。
3. **模型本身是近似的。** BaseMul 实际的延迟链比这里建模的更细（`ATTACK_MODEL.md` 第 9 节），这里给出的是泄漏强度的下界，不是精确预测。
4. **逐系数攻击有“先有鸡还是先有蛋”的问题**：同一对系数中先猜的那个，在不给 `--dk-ref` 时它的搭档系数仍按 0 处理（见 `cpa_attack.py` 的模块文档）。
5. **换一次固定密钥或种子，结果会变。这是预期行为，不是故障。** 要评估是否安全，应当用多个不同的种子分别采集，GUI 会把它们汇总成跨 campaign 的成功率。
6. **这里只测了 Decaps 的三条 BMUL。** INTT 之后的减法、KeyGen 的 CBD/NTT、Encaps 的重加密路径都没有覆盖。
7. **`structured` 策略构造出的密文不是合法密文**，核心照常走隐式拒绝路径，返回的 K 没有意义。它只用于提升目标 BaseMul 对的信噪比。在 KEM 场景下攻击者确实可以发送任意密文，所以这仍然是合理的威胁模型。

**结论应该这样写**：“在 N 条 trace、这个窗口、这个模型下，CPA [能 / 不能] 以 <rank-1 比例> 的成功率恢复密钥系数。”不要笼统地说“硬件安全”或“硬件不安全”。

要说明“加了防护之后变安全了”，需要在**同一个种子、同一套示波器设置**下，对防护前后各跑一次，比较 MTD（traces-to-disclosure），而不是只看某个固定 trace 数下有没有打穿。

---

## 8. Oracle-assisted 评估的边界

`--dk-ref`（在 GUI 中是 “Score with campaign key”）的**唯一**作用是事后打分：拿真实密钥核对攻击是否猜对、真实值排在第几名。它**从不**参与构造假设（见 `cpa_attack.py` 模块文档、`models/base.py: Guess` 文档、`ATTACK_MODEL.md` 第 5.4 节）。

凡是用了真实密钥的结果，报告中都会标注 `oracle_assisted: true`，GUI 也会显示同样的提示。**不要把 oracle-assisted 的排名或成功率当成“真实攻击的结果”来展示**：它衡量的是“这个假设模型离成功还有多远”，真实攻击指的是不给真实密钥的那次运行。

---

## 9. 已知局限

1. **`--curve` 处理大文件较慢**：每个 checkpoint 都重新跑一次完整的 768 系数攻击，没有增量复用。
2. **NTT / INTT 的周期窗口还没有测量**（`config.py` 中为 `None`），图上只标 BMUL 窗口。
3. **115200 baud 下，每条 trace 上传 1088 字节密文约需 94 ms**，这是吞吐量的上限。提速方法见 `docs/UART_PROTOCOL.md` 第 2 节。
4. **采集进行时，板子和示波器归采集任务独占**：寄存器读写等操作会被拒绝，直到采集结束。这是为了避免两路 UART 流量互相冲突。

---

## 10. 常见问题

| 现象 | 处理 |
| ---- | ---- |
| 连不上板子，“no response from the core” | 检查选的是不是 `…1` 接口（`…0` 是 JTAG）；Arty Z7 PS 版每次上电后要先运行 `uv run scripts/board/boot_arty_z7_ps.py` |
| 串口 “busy” | 端口被其他程序占用（另一个 GUI 实例、openFPGALoader、Vivado 硬件管理器） |
| 链路自检中 “0x05 Run (required for operations)” 失败 | bitstream 是旧版本，不能运行运算（只能读写寄存器和缓冲）；重新生成 bitstream |
| *Check trigger*：没有运算时示波器也触发了 | 示波器处于 AUTO 模式，或触发源 / 电平不对：改为 NORMAL / SINGLE、上升沿、约 1.6 V，触发源选接 `trig_out` 的通道 |
| *Check trigger*：Decaps 后示波器没有触发 | 探头没接在 Pmod JA1（A7）/ JA3（Z7），或地线没接到 Pmod GND，或触发源选错了通道 |
| `cannot open <resource>` | 确认 `config.py` 中的 `SCOPE_IP` 已不是占位符（`192.0.2.100`）；在 Connection 页点 *Find instruments* |
| `pyvisa is missing` | `uv sync --extra scope` |
| K 正确率 < 100% | 板子在采集中被复位，或链路出错；对应 trace 已被标记，下一条前会自动重写配置 |
| 分析报错 “BMUL n lies outside the captured traces” | 示波器时基太短，trace 至少要覆盖触发后约 23 µs |
| `--curve` 报 “needs --dk-ref” | key rank 随 trace 数的曲线只能在 oracle-assisted 模式下计算 |
