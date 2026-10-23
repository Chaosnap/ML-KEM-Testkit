# cpa_toolkit：ML-KEM-768 Decaps 的 CPA / TVLA 侧信道实验平台

这个工具对板上 ML-KEM-768 核心的 Decaps 做实测侧信道评估：真实板子加真实示波器。它包含两套相互独立的实验：

- **CPA**（相关功耗分析）：私钥固定，输入 N 条不同的密文，逐系数恢复私钥。攻击目标是 BaseMul 里 `û[j]·ŝ[j]` 的乘积：假设 `HW(û[j]·g mod q)`（见第 6 节和 [`../docs/LEAKAGE_MODELS.md`](../docs/LEAKAGE_MODELS.md)，硬件背景见 [`../docs/ATTACK_MODEL.md`](../docs/ATTACK_MODEL.md)）。
- **TVLA**（Welch's t 检验）：私钥固定，使用 fixed 和 random 两组密文，检测功耗是否与输入相关。这个实验不涉及任何密钥假设。

主机与板子之间的通信协议见 [`../docs/UART_PROTOCOL.md`](../docs/UART_PROTOCOL.md)。第一次去实验室采集，先读 [`../docs/TRACE_ACQUISITION.md`](../docs/TRACE_ACQUISITION.md)（接线、示波器设置、逐步操作、排错）。

---

## 1. 文件

| 文件 | 作用 |
| ---- | ---- |
| `config.py` | **需要修改的配置**：示波器 IP、串口、BMUL 窗口、CPA 默认假设 |
| `mlkem768.py` | FIPS 203 ML-KEM-768 软件实现（KeyGen / Encaps / Decaps，含批量版本），已与 NIST ACVP 向量核对 |
| `mlkem_ref.py` | NTT / BaseMul / ByteDecode 等 FIPS 203 底层运算、解密自检、结构化密文 |
| `testvectors.py` | 测试向量集：由种子确定性生成，包含每条密文的期望 K，存为 `data/vectors/*.npz` |
| `uart_target.py` | 板子侧驱动：`Link` 负责收发帧，`Target` 提供分步操作（reset / configure / load / run，对应论文的 x/m/s/c/e/a）；自动识别 `0x05` Run |
| `connection.py` | 非阻塞的连接/断开（可随时断开），以及链路监视（空闲时 ping，检测串口被拔出） |
| `scope.py` | 示波器：`LeCroyScope`（Teledyne LeCroy MAUI，如 HDO6104：VICP 或 LXI/VXI-11，`WF?` + WAVEDESC），`VisaScope`（VISA/SCPI，Keysight / Rigol / Siglent / Tektronix 方言）；`open_scope()` 按 `SCOPE_DIALECT` 选择 |
| `hdf5_trace_store.py` | campaign 文件（`.hdf5`）的增量写入、读取与续采 |
| `acquire_cpa.py` | 采集：CPA 或 TVLA，按向量集逐条采集并逐条校验 |
| `cpa_attack.py` | **简单逐系数 CPA**（第 6 节）：两个假设、相关系数矩阵、峰值位置、top-10 与稳定性投票、可选的真实密钥排名；CSV / JSON |
| `tvla_analysis.py` | TVLA：Welch's t、检测曲线 |
| `web_gui.py` + `web_static/` | Web GUI（第 2 节） |

---

## 2. Web GUI

```bash
uv run python cpa_toolkit/web_gui.py              # http://127.0.0.1:8765，自动打开浏览器
uv run python cpa_toolkit/web_gui.py --port 8900 --no-browser
```

操作步骤见 [docs/GUI_GUIDE.md](../docs/GUI_GUIDE.md)。GUI 只依赖标准库 `http.server`，加上随仓库附带的 Plotly.js，不需要联网，只绑定 `127.0.0.1`。左侧导航按实验顺序排列，顶栏的指示灯显示板子、示波器和后台任务的状态，底部可展开 Console 查看完整日志。

| 页面 | 内容 |
| ---- | ---- |
| **Connection** | 先选板子：SAKURA-X（USB FIFO，自动配对 FT2232H A/B 通道，Connect 时逐步显示检查结果）或 Arty（UART）。串口列表：标出推荐接口；Digilent 板的 `…0` 是 JTAG 通道，会被标注出来。连接和断开不阻塞，可随时断开，可开启自动重连。链路自检中的最后一项显示当前 bitstream 是否支持 `0x05` 完成通知。示波器部分可以搜索 VISA 仪器（点击一行即填入 IP），也可以把设置存为 `config.py` 的默认值 |
| **Test Vectors** | 生成 CPA（valid / invalid / pc-oracle）或 TVLA（fixed vs random）向量集。可以逐行查看 c / m / K（支持复制），导出 csv / json / dk / ek，用软件 Decaps 复核，或在板上跑前 N 条，核对 K 和恒定周期。向量集可以命名、加标签（见下文“标签与删除”），可以移到回收站（可撤销）——用过它的 campaign 不受影响，因为每个 campaign 文件自带密文、期望 K 和种子 |
| **Acquisition** | 开始前会列出检查项（板子、示波器、向量集）。采集时实时显示最新 trace、均值和 fixed−random 差值，以及速度和剩余时间；可随时停止，文件可以续采。另有 Setup capture：用一次性向量抓一条波形，用来调触发、量程和时基；*Check trigger* 检查示波器只在 Decaps 开始时触发（无运算时不触发、Decaps 时触发、波形覆盖全部 BMUL 窗口），结果也显示在检查项里 |
| **Trace Library** | 所有 campaign 的列表，可搜索、按模式和状态筛选、排序。详情页显示 K 正确率、CYCLE_COUNT 分布、trace 是否覆盖全部 BMUL 窗口、来源信息（向量集、种子、板子、示波器），可以命名、写备注、加标签。波形查看器可以按序号、类别或指定索引叠加 trace，并显示均值 ± σ 带。支持导出 .hdf5 和 .npz 片段。回收站里可以恢复或永久删除；永久删除时，由该 campaign 生成的分析报告也一起删除 |
| **Analysis** | 顶部选 campaign 和已保存的报告。*New analysis* 打开配置面板：1 假设（`hw_product_mod` / `hw_product_raw`，显示公式、寄存器和发生的周期）、2 攻击什么（哪几个多项式、每个多项式前几个系数、在整个 BMUL 窗口里找，还是只看 RTL 预测的周期附近 ± N 个周期）、3 打分（盲攻击 / 用 campaign 的真实密钥给猜测排名 / dk 文件）、4 运行。CPA 报告有四个标签：**Overview**（一行一个系数的表：最佳猜测、峰值 \|ρ\|、是噪声底的几倍、领先第二名多少、峰值位置、与预测周期的偏差、4 个窗口里的稳定性投票；下方是“峰值是真的吗”条形图和“峰值落在哪”直方图）、**Coefficient**（单个系数的五张图：相关系数随时间、3329 个候选各自的峰值、随 trace 数的收敛、top-10 表、候选×时间热图，← → ↑ ↓ 切换系数）、**Compare**（同一个 campaign 的全部 CPA 报告对比）、**Details**。TVLA 有四个标签：\|t\| 曲线（可一键缩放到各 BMUL 窗口，点击图中任意位置读取该采样点）、类均值、检测曲线、Details。报告保存在 `data/analysis/`；旧模型目录生成的旧报告仍能列出，但需要重新运行 |
| **Advanced** | **Link settings**：操作超时；显示当前 bitstream 是否支持 0x05 Run（不支持则所有运算被禁用，需要重新生成），可重新探测。**Buffer layout**：dk / c / K 以及 KeyGen、Encaps 输入输出所在的偏移，点击即读取该区域。**Protocol steps**：按 SimpleSerial 的 x / m / s / c / e / a 逐步执行一次运算（复位、设模式、发 dk、发 c、运行并等待、读结果），每步记录在 Step log 中，Run 的结果显示 STATUS 各位、周期数、错误码和输出，并与选中的测试向量的期望 K 比较；也可以一键跑完整 Decaps。**Registers**：一次读出全部寄存器并解码（STATUS 各位、OP_MODE、ERROR_CODE、VERSION）。**Data buffer**：原始读写 |

GUI 和下面的命令行工具调用的是同一套函数，所以结果一致。如果要做很长的采集，也可以直接用命令行。

### 标签与删除

Test Vectors 和 Trace Library 两页的管理方式相同：

- **标签**：在详情里输入，回车或逗号确认，退格删除最后一个，也可以从已有标签中选。每个对象最多 16 个，每个最长 32 个字符，大小写不同算同一个。列表中点击标签即按该标签筛选；工具栏的标签下拉框列出所有标签和数量；搜索框也会匹配标签。campaign 的标签存在 HDF5 元数据里，向量集的名称和标签存在旁边的 `<set>.meta.json` 里。
- **批量操作**：勾选多行（Shift+点击选范围，Ctrl/⌘+点击加一行，表头勾选框或 Ctrl/⌘+A 选当前筛选结果的全部行），上方会出现操作栏：*Tags…* 批量加减标签，*Move to trash* 移入回收站。下方的 *Move to trash* / *Restore* / *Delete permanently* 按钮也作用于勾选的行。Del 键移到回收站，Esc 取消勾选。正在采集的 campaign 不能被改动。
- **删除分两步**：先移入回收站（`data/traces/.trash/`、`data/vectors/.trash/`），不需要确认，提示框里的 *Undo* 可以撤销；名称、备注、标签随文件一起移动，可以恢复。在回收站视图中可以 *Delete permanently* 或 *Empty trash*，这一步不可撤销。永久删除 campaign 时，从它生成的分析报告（.json 和 .csv）也一起删除；删除向量集不影响已采集的 campaign，而且用同样的设置（用途、密文类型、数量）和种子可以重新生成完全相同的向量集。

---

## 3. 测量前先确认

1. **模型数学正确**：执行 `make test`（`tests/test_cpa_attack.py` 在带注入泄漏的合成 campaign 上检查：正对照必须恢复密钥，纯噪声和错误的密钥必须不行）。如果模型错了，CPA 会表现为怎么都打不出来，很容易被误以为是硬件做了防护。
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
$P testvectors.py gen --mode cpa -n 20000 --strategy valid       # 也可选 invalid / pc-oracle
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
$P cpa_attack.py --list-models                                          # 两个假设
$P cpa_attack.py ../data/traces/<campaign>.hdf5                          # 盲攻击：每个多项式的前 8 个系数
$P cpa_attack.py ../data/traces/<campaign>.hdf5 --polys 0 --coeffs 0-15 --dk-ref dk.bin   # + 真实密钥排名
$P cpa_attack.py ../data/traces/<campaign>.hdf5 --search gate --margin 10 --delay 3       # 只看预测周期附近
$P cpa_attack.py ../data/traces/<campaign>.hdf5 --coeffs 0 --out report.json --csv r.csv   # 全部 256 个系数
$P tvla_analysis.py ../data/traces/<campaign>.hdf5 --csv t.csv
```

采集过程中的检查：

- **每条 trace 都检查 K**：板子输出的 K 与向量集中预先算好的期望值逐条比较。如果不一致，说明板子被复位、密钥不对或链路出错，该 trace 会被标记，并在下一条前重写配置寄存器。**每条 trace 也检查 CYCLE_COUNT**：周期数变化就说明出现了时序侧信道。
- **dk 只在需要时重发**：默认每 50 条重发一次（`--resend-dk-every`），其余 trace 只上传 1088 字节的密文。
- **触发**：使用 `trig_out`（`STATUS.busy`）。Arty A7 接 Pmod JA1，Arty Z7 接 Pmod JA3，SAKURA-X 接 CN8 第 3 脚（地接第 13 脚）。
- **BMUL 窗口**（`config.py: BMUL_WINDOWS`）来自 Decaps 的周期精确仿真。修改 RTL 或微码后需要重新测量，方法见 `ATTACK_MODEL.md` 第 4.3 节。

CPA 的密文策略（`--strategy`）：

| 策略 | 说明 |
| ---- | ---- |
| `valid`（默认） | 每条都是一次全新的、合法的 FIPS 203 封装 |
| `invalid` | 随机字节当密文，走隐式拒绝路径（BaseMul 照样执行） |
| `pc-oracle` | 明文检查预言机的选择密文，用 `pc_oracle.py` 恢复密钥，见 `ATTACK_MODEL.md` 第 9 节 |

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

## 6. 简单 CPA（`cpa_attack.py`）

2026-10-20 起，旧的泄漏模型目录（`models/`、按流水线逐周期对齐的攻击）已经移除，换成公开的 ML-KEM CPA 代码库共同使用的最简配方。完整说明、两个假设的推导、图怎么读、与参考仓库的对照表见 [`LEAKAGE_MODELS.md`](../docs/LEAKAGE_MODELS.md)。

**做什么**：对每个秘密系数 `ŝ[poly][j]`（0…3328 共 3329 个候选 g），取已知密文算出的公开系数 `u = û[poly][j]`，假设 `HW(f(u·g))`；对 BMUL 窗口里的**每一个采样点**算 Pearson 相关系数（一次矩阵乘法）；|ρ| 最大的候选就是猜测，峰值所在的位置是 POI。每个系数还给出 top-10，以及“稳定性”：在 POI 周围 3、5、7、9 个采样点的窗口里分别累加 ρ² 取前 10，数每个候选出现在几个窗口里（4/4 = 最稳）。

| 假设（`--model`） | 公式 | 对应的硬件寄存器 | 周期（T = 该系数所在字的 RAM 读地址周期，每个字 +2） |
| ---- | ---- | ---- | ---- |
| `hw_product_mod`（默认） | `HW(u·g mod q)`，12 位 | Barrett 输出 `r` → `p00` / `p11` | T + 10 / T + 11 |
| `hw_product_raw` | `HW(u·g)`，24 位 | DSP 乘积寄存器 `P`（`p1` / `p2`） | T + 5 / T + 6 |

`T = BMUL_READ_CYCLES[poly] + 2·(j // 2)`（`config.py`）；一对系数（2w，2w+1）在同一个周期由两个乘法器算完，所以搭档系数对另一个来说是算法噪声，需要上千条 trace。假设只依赖 `u` 和 `g`，所以**攻击天然是盲的**：真实密钥（`--dk-ref`）只在事后给猜测排名，从不参与构造假设。

**预测周期 ↔ 实际峰值**是最重要的健康检查：每个系数的峰值位置都会和 RTL 预测的周期比较（表里的 *offset* 列）。真实泄漏的峰值会堆在同一个偏移上（偏移不是 0 就是探头/放大器延迟，填进 *shift* 即可），噪声的峰值则散在整个窗口里。

```bash
python cpa_attack.py FILE.hdf5                                  # 盲攻击，每个多项式的前 8 个系数，整个 BMUL 窗口
python cpa_attack.py FILE.hdf5 --dk-ref dk.bin                  # + 真实密钥的排名
python cpa_attack.py FILE.hdf5 --search gate --margin 10        # 只看预测周期 ± 10 个核心周期（快得多）
python cpa_attack.py FILE.hdf5 --polys 0 --coeffs 0-15 --model hw_product_raw
```

`--coeffs N` = 每个多项式的前 N 个，`0` = 全部 256 个，`0-15` / `3,7` = 指定的；全部 768 个系数用整个窗口大约要几十分钟，用 `gate` 只要几分钟。

**时间轴**：示波器报告的“第一个采样点相对触发的时间”（LeCroy 的 `HORIZ_OFFSET`）现在随每条 campaign 一起存下来（`record_start_s`），BMUL 窗口按**时间**放在 trace 上，所以触发位置在屏幕中间（有预触发数据）也不会错位；旧 campaign 没有这个字段时按“采样 0 = 触发”处理，GUI 的覆盖检查会提示。窗口两侧的余量 `TRIGGER_SLACK_CYCLES` 的单位是**核心周期**（旧版误当成采样点，2.5 GS/s 下只有 1/12 的预期）。

**加一个新假设**：在 `cpa_attack.py` 的 `MODELS` 里加一个 `Model(...)`，`leak(x)` 接收 `u·g`（整数，shape = trace × 候选）并返回汉明权重 / 汉明距离；GUI 刷新后就能在下拉框里选到。

把 RTL 里每个触发器的翻转和流水线孪生逐周期比对的工具仍然保留（需要 Verilator）：

```bash
python scripts/sim/pipeline_check.py        # 全部 35 个寄存器、三次 BMUL、每一拍都必须一致
python scripts/sim/register_leakage.py      # 哪些触发器携带数据相关的翻转，按分组排序
```

---

## 7. 这个评估能回答什么、不能回答什么

**能回答的问题**：在这套假设模型、这个 trace 数、这个示波器设置下，相关系数能否从 3329 个候选中把密钥系数挑出来。

- 能挑出来：说明这个具体攻击在这些条件下成功，**现有实现（未加防护时）确定不安全**。
- 挑不出来：只能说这一次没打穿，**不能**说明它安全。

**不能回答的问题，以及常见的误判来源：**

1. **trace 数不够，不等于没有泄漏。** 先用 TVLA 确认泄漏的量级和位置，再决定 CPA 需要多少条 trace。
2. **窗口或触发没对齐，会把真实泄漏淹没。** 先用 Setup capture 和 Trace Library 的波形查看器确认 BMUL 窗口落在波形中；TVLA 的 |t| 峰值也应该落在窗口内。
3. **假设本身是近似的。** 这里只建模了一个寄存器的汉明权重，真实芯片每拍翻转上千个触发器（`ATTACK_MODEL.md` 第 9 节）；结果是泄漏强度的下界，不是精确预测。
4. **同一对系数共用一个周期**：搭档系数是算法噪声，所以 `hw_product_*` 每个系数需要的 trace 数比单个乘法器的教科书估计多。
5. **换一次固定密钥或种子，结果会变。这是预期行为，不是故障。** 要评估是否安全，应当用多个不同的种子分别采集，每个种子的报告在 Analysis 的 *Compare* 标签里并排。
6. **这里只测了 Decaps 的三条 BMUL。** INTT 之后的减法、KeyGen 的 CBD/NTT、Encaps 的重加密路径都没有覆盖。

**结论应该这样写**：“在 N 条 trace、这个窗口、这个模型下，CPA [能 / 不能] 以 <rank-1 比例> 的成功率恢复密钥系数。”不要笼统地说“硬件安全”或“硬件不安全”。

要说明“加了防护之后变安全了”，需要在**同一个种子、同一套示波器设置**下，对防护前后各跑一次，比较 MTD（traces-to-disclosure），而不是只看某个固定 trace 数下有没有打穿。

---

## 8. Oracle-assisted 评估的边界

`--dk-ref`（在 GUI 中是 “Rank the campaign key”）的**唯一**作用是事后打分：拿真实密钥核对攻击是否猜对、真实值排在第几名。它**从不**参与构造假设——假设只由公开的 `u` 和候选值 `g` 算出（见 `cpa_attack.py` 模块文档）。

凡是用了真实密钥的结果，报告中都会标注 `oracle_assisted: true`，GUI 也会显示同样的提示。**不要把 oracle-assisted 的排名或成功率当成“真实攻击的结果”来展示**：它衡量的是“这个假设模型离成功还有多远”，真实攻击指的是不给真实密钥的那次运行。

---

## 9. 已知局限

1. **全部 768 个系数 × 整个窗口较慢**（几十分钟）：先用 `--coeffs 8`，确认偏移后用 `--search gate --delay <偏移>` 再跑全部。
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
| 分析报错 “BMUL n is outside the record” | 记录没有覆盖触发后 0 … 约 23 µs：把触发位置拉到屏幕最左、时基加长；Acquisition 的 *Check trigger* 会显示记录实际覆盖的范围 |
| CPA 报告 “No signal” | 噪声底以上没有任何峰：加 trace、换另一个假设、检查 Check trigger；先看 TVLA 的 \\|t\\| 峰是否落在 BMUL 窗口内 |
| CPA 的 offset 全是同一个非零值 | 探头 / 放大器延迟：把它填进“shift”，改用 *Around the predicted cycle* |
