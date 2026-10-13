# CPAProtectEvaluation — ML-KEM-768 Decapsulation 的 CPA/TVLA 侧信道实验平台

对 Artix-7（Arty A7）上的 ML-KEM-768 Decaps 做**实测**（真示波器 + 真板子）侧信道评估，
两套互相独立、不混用的实验：

- **CPA**（相关功耗分析）：固定私钥、N 条不同密文、逐系数恢复私钥，目标见
  [`ATTACK_MODEL.md`](ATTACK_MODEL.md)：`HD(R_prev, BaseMul(û, ŝ_guess) mod q)`。
- **TVLA**（Welch's t 检验）：固定私钥、fixed-vs-random 两组密文、检测"功耗是否和
  输入相关"，不涉及任何密钥假设——见 [`docs/TVLA_SIM.md`](../docs/TVLA_SIM.md)（仿真
  级）和本文档（实测级，`tvla_analysis.py`）。

和仿真级 TVLA 是互补关系（`docs/TVLA_SIM.md` 第 10 节）：仿真告诉你*哪里*泄漏最强、
该测哪些寄存器；这里用同一套方法在真实硅片上验证——**先用正负对照跑通整条流程，
再接示波器**。

---

## 0. 这个文件夹和 GitHub 上那个仓库的关系

你提到的 [Chaosnap/Post-Quantum-Cryptography-ML-](https://github.com/Chaosnap/Post-Quantum-Cryptography-ML-)
里 `src/pqc_bench/models/cpa_attack.py` 和 `src/pqc_bench/hardware/hdf5_loader.py`：
**思路可以参考，代码不能直接拿来跑这块板子**——那边的假设模型是 `HW(plaintext XOR
key)`，一个 8 位 SBox 玩具模型，traces 是现场合成的正弦包络，和 ML-KEM 的
NTT/BaseMul 没有关系。这里的 CPA 核心算法（Pearson 相关、标准化后矩阵乘）沿用了同样
的写法，HDF5 的 `/traces /samples /metadata /intermediates` 分组结构也保留了（两边
loader 可以互相读对方写的文件），但 ML-KEM 相关的数学和寄存器模型是照这颗核心的真实
RTL（`mlkem_poly_alu.sv`、`mlkem_modmul.sv`、Decaps 微码）重写的。

---

## 1. 文件

| 文件 | 作用 |
| ---- | ---- |
| `config.py` | **改这里**：示波器 IP、串口、触发窗口、CPA 默认模型 |
| `mlkem_ref.py` | FIPS 203 的 ByteDecode/ByteEncode/NTT/BaseMul 软件实现、假设模型（`PairModel`）、命名模型预设（`MODEL_PRESETS`）、结构化密文生成（`structured_ciphertext`） |
| `uart_target.py` | 板子侧：UART-AXI 桥协议、`Target.decaps()`、`SimTarget`（无板子时用） |
| `scope.py` | 示波器侧：`VisaScope`（真实仪器）、`SyntheticScope`（假的，用于跑通流程） |
| `hdf5_trace_store.py` | 增量写入/读取 campaign 的 `.hdf5` 文件，CPA/TVLA 两种 `mode` |
| `acquire_cpa.py` | **采集脚本**：CPA 模式（`cpa-dry-run`/`cpa-run`，三种密文策略）+ TVLA 模式（`tvla-dry-run`/`tvla-run`） |
| `cpa_attack.py` | **CPA 攻击脚本**：相关系数、key rank、`--curve`（key-rank-vs-trace-count + MTD）、CSV 导出 |
| `tvla_analysis.py` | **TVLA 分析脚本**：Welch's t 检验，和 `pkg/sca/tvla.go` 同一套统计量 |
| `model_registry.py` | 发现/加载 CPA 泄漏模型模块（任何实现 `mlkem_ref.py` 接口的 `*.py` 文件）——目前只用于 CPA Model tab 的展示和自检，**不**接入 `cpa_attack.py` 的实际攻击引擎（见第 9 节局限） |
| `gui_config_io.py` | GUI 用来改写 `config.py` 里单个字段的小工具（正则替换，保留注释） |
| `web_gui.py` + `web_static/` | **网页 GUI**（见第 1.1 节）：六个 tab，FPGA 连接、示波器设置、CPA 模型、采集、分析、调试 |

### 1.1 网页 GUI（跨三个平台通用）

```bash
python3 web_gui.py                   # 起在 http://127.0.0.1:8765，自动开浏览器
python3 web_gui.py --port 8900        # 换端口
python3 web_gui.py --no-browser        # 不自动开浏览器标签
```

纯标准库实现（`http.server`），**不需要装任何新包**。只绑定 `127.0.0.1`。顶部有一条
统一状态条（FPGA / Scope / Model / Dataset / Acquisition 五个指示灯），四个主 tab +
一个 Advanced/Debug tab，底部是可折叠的 Console（原来常驻的 Log 卡片改成了默认收起
的抽屉，不再占页面空间）：

- **FPGA Connection**：选串口、连接、看核心信息、跑 preflight 自检、读写 CSR 寄存器。
- **Oscilloscope Setup**：示波器 IP/channel/dialect/points/timeout，测试连接，列出 VISA 仪器，存回 `config.py`。
- **Trace Acquisition**：拆成两步。**Step 1 Generate Dataset**（离线，只需要
  `build/pqc-testkit`，不碰 FPGA/示波器）选 CPA/TVLA、密文策略、trace 数、种子，生成
  一份"配方"（recipe，存在 `sim_result/inputsets/`，不含 dk/密文本身）并预览；
  **Step 2 Acquire Real Traces**（必须先连好板子和示波器）挑一份配方真正采集，这是
  真正生成密文并写进 HDF5 的唯一时刻——之后（包括 Stop/Resume 之间）不会再重新生成。
  支持 **Stop**（随时停止，文件保留已采集部分）和 **Resume**（对同一份不完整的真实
  文件续采，数据集列表里标 `Resumable`）；另有 **Capture One Trace / Waveform
  Preview**，用一次性 dk/密文（从不写入任何 campaign）快速看一眼波形，确认触发/量
  程/时基设对了再开始正式采集。**正式 GUI 不再提供 Synthetic dry-run**——
  `SimTarget`/`SyntheticScope` 底层代码还在，但只能从开发者测试入口
  （`dev_regression_test.py`，或 `acquire_cpa.py cpa-dry-run`/`tvla-dry-run` 这两个
  CLI 子命令，完全未改）用，不再出现在网页上，所以这个 GUI 产出的文件永远是真实硬件
  数据。历史上的 synthetic `.hdf5` 文件会继续显示在数据集列表里，明确标成
  `synthetic`，不会和真实结果混在一起判断。
- **CPA Analysis**：按 Dataset → Leakage Model → Run Analysis → **Results Viewer**
  排布（原来独立的 "CPA Model" tab 已并入 Leakage Model 区块，见第 1.2 节）。Results
  Viewer 不是新 tab，是这个 tab 内部的三个子视图：
  - **CPA Viewer**：Correlation vs. Time、每系数结果表、key-rank-vs-trace-count 曲线
    + MTD，以及**跨 campaign 的 Success Rate vs. Trace Count**（只在同一个
    target/R_prev 下有 ≥2 个独立 campaign 的 oracle-assisted `--curve` 结果时才画，
    否则明确显示 `Not Available`，绝不编曲线）。
  - **TVLA Viewer**：Welch |t| vs. time（±4.5 阈值虚线 + BMUL 窗口底色，NTT/INTT 窗口
    的 cycle 范围还没实测过，`config.py` 里是 `None`，图上就不画，不瞎猜）、
    fixed/random 均值曲线、Max |t|/Peak Sample/Traces-to-Detection 统计、
    detection-vs-trace-count 曲线。
  - **Raw Trace Viewer**：直接读 HDF5 里的真实波形，多条叠加、数值型 ROI
    缩放（输入起止 sample，Reset 还原）、PNG/SVG/CSV 导出，标注 BMUL 窗口（cycle 正确
    换算成 scope sample）。
  每次 CPA/TVLA 分析都会整份存成 `sim_result/analysis/*_report.json`（数据集身份、
  采集参数、模型配置+版本、全部逐点数组、时间戳）——切换 Dataset 下拉会自动加载该
  数据集最新的一份存档，不用重跑；也能从 Results Viewer 的下拉手动挑一份旧存档打开。
  选中 TVLA 数据集时仍会自动隐藏所有 CPA 专属控件并切到 TVLA Viewer。**原来"2. 隐藏
  时编号从 1 跳到 3"的问题已修**：每个步骤标题的编号现在按可见顺序实时算，不是写死
  在 HTML 里的。
- **Advanced / FPGA Debug**：原始数据缓冲区读写、手动 `dk`/`c` 跑一次 Decaps——仅用于
  协议调试，随机字节不是合法私钥/密文，这里**不提供**一键填充随机数据的按钮，要测试
  得自己粘贴真实的 `dk_ref.bin` 内容或别处生成的向量。

GUI 和 `acquire_cpa.py`/`cpa_attack.py`/`tvla_analysis.py` 是同一套后端代码：GUI 的
"Trace Acquisition"/"CPA Analysis" tab 直接调用这些脚本里的函数（不是 subprocess
shell 出去），结果和命令行跑出来的完全一致。长时间的真实硬件采集建议直接用命令行
（可以后台跑、可以用 `--seed` 之类参数、不用开着浏览器）。

### 1.1.1 开发者测试入口（synthetic，仅软件验证）

```bash
uv run python3 dev_regression_test.py
```

跑 CPA/TVLA 的正负对照（`SimTarget`/`SyntheticScope`，和以前一样）、Stop/Resume 的
正确性（含一个曾经真实踩到的坑：`pqc-testkit sca gen` 的 `--seed` 只保证 `dk`
可复现，`valid`/`invalid` 策略的密文内容和洗牌顺序用的是真随机数，**不**可重放——
所以 resume 现在从文件自己已经写入的 `/intermediates/ct` 读密文，而不是重新生成，
细节见 `hdf5_trace_store.py: TraceWriter` 和 `acquire_cpa.py: run_campaign` 的
docstring）、Results Viewer 的存档/读档、Raw Trace Viewer、跨 campaign 成功率聚合。
每个产物都标 `simulated=True`、文件名带 `devtest_` 前缀，不会和真实数据混淆，也不会
被当作硬件结论。

### 1.2 CPA Analysis 的 Leakage Model 区块（原 CPA Model tab）

选"Model preset"下拉（`HW`/`HD`/`basemul-c0`/`basemul-c1`/`basemul-word`），下方立刻
显示这个模型的 **Equation / Register / Bit width / Pipeline stage / Required inputs /
Limitations** 六项，内容和 RTL 行号都来自 `mlkem_ref.py: MODEL_PRESETS`（单一数据源，
不在前端另外维护一份）。选 `HW` 时 R_prev 整行隐藏（`HD(0,X)=HW(X)` 恒成立，没有可选
的前态）；选其余命名预设时 R_prev 只列出对该寄存器物理上有意义的选项（比如 `p_self`
不会出现 `acc`，因为 `acc` 对它是个 no-op——见 `mlkem_ref.py` 里 `valid_rprevs` 的注
释）；选"Custom"才解锁手动指定 target/R_prev（这时 R_prev 不再过滤，全部 3 个选项
都给）。

"Advanced Model Settings"（默认折叠）收纳了模型插件发现/加载/自检——这是
`model_registry.py` 的"任意一个实现 `mlkem_ref.py` 接口的模块"机制，和上面选哪个
"Model preset"是两件独立的事：这里加载的模块只决定 Advanced Model Settings 自己显示
什么（doc/Q/K、自检结果），**不会**反过来悄悄改变 Leakage Model 区块正在用的
target/rprev——整个页面只有 Leakage Model 区块这一处决定一次分析实际用什么模型，避免
两处配置互相冲突。"Save current selection as CLI default" 是唯一会写 `config.py` 的
按钮，且只在你点它的时候写，换预设或跑分析都不会触发。

`cpa_attack.py` 目前硬编码 import 了 `mlkem_ref`（`ATTACK_MODEL.md` 第 9 节第 1 条已
经提到这个局限）——也就是说 Advanced Model Settings 里加载别的模型模块，看得到它的
信息和自检结果，但**还不能**让 CPA Analysis 真的用那个模块跑攻击，Leakage Model 区块
目前只服务 `mlkem_ref.py` 自己的五个预设 + custom target/rprev。

---

## 2. 示波器/串口地址——先填的占位符

同之前：`config.py` 里 `SCOPE_IP = "192.0.2.100"` 是 RFC 5737 `TEST-NET-1` 占位符，
`SERIAL_PORT` 是占位设备名，拿到硬件后改掉或用 `--ip`/`-p` 覆盖。具体字段说明见
`config.py` 里的注释。

---

## 3. 跑之前先自己验证

### 3.1 环境

```bash
cd CPAProtectEvaluation
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

（没有真示波器也能跑到 3.3 节；`pyvisa`/`pyserial` 只有连真实仪器时才会被用到。）

### 3.2 数学对不对：对着 Go 侧参考实现自检

```bash
python3 - <<'EOF'
import sys, subprocess, csv
sys.path.insert(0, ".")
import mlkem_ref as R

seed = bytes(range(64))
subprocess.run(["../pqc-testkit", "sca", "gen", "--op", "decaps", "-n", "4",
                 "--seed", seed.hex(), "-o", "/tmp/v.csv"], check=True, cwd="..")
rows = list(csv.DictReader(open("/tmp/v.csv")))
m0 = R.fixed_message(seed)
for row in rows:
    if row["class"] != "0":
        continue
    d = bytes.fromhex(row["input_hex"])
    dk, ct = d[:2400], d[2400:]
    assert R.decrypt(dk, ct) == m0, "software decrypt != expected message -- NTT/BaseMul is wrong"
print("selftest OK")
EOF
```

同样的自检可以从 GUI 的 CPA Model tab 点"Run self-test"跑。**跳过这步直接采集，模型
错了会表现成"CPA 怎么都打不出来"，容易误以为是硬件做了防护。**

### 3.3 正负对照：跑通整条 CPA 流程

```bash
SEED=000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f202122232425262728292a2b2c2d2e2f303132333435363738393a3b3c3d3e3f

# 负对照：纯噪声，cpa_attack.py 必须报接近随机排名
python acquire_cpa.py cpa-dry-run -n 300 --leak-mv 0 --seed $SEED

# 正对照：注入和假设模型完全一致的泄漏，cpa_attack.py 必须打穿
python acquire_cpa.py cpa-dry-run -n 300 --leak-mv 6 --seed $SEED
```

导出同一个种子的 `dk` 喂给 `--dk-ref`（这是**oracle-assisted** 评估，只用来打分，
从不参与构造猜测——见 `cpa_attack.py` 模块文档和 `ATTACK_MODEL.md` 第 5.4 节）：

```bash
go run ../cmd/pqc-testkit sca gen --op decaps -n 4 --seed $SEED -o /tmp/v.csv
python3 -c "
import csv
rows = list(csv.DictReader(open('/tmp/v.csv')))
open('/tmp/dk_ref.bin', 'wb').write(bytes.fromhex(rows[0]['input_hex'])[:2400])
"
python cpa_attack.py ../sim_result/traces/<负对照文件>.hdf5 --dk-ref /tmp/dk_ref.bin
python cpa_attack.py ../sim_result/traces/<正对照文件>.hdf5 --dk-ref /tmp/dk_ref.bin --curve
```

**本仓库已经跑过一次**（`--model HD`/`basemul-word` 等多个组合，详见
`ATTACK_MODEL.md` 第 8 节）：

| | rank-1 比例 | 中位排名 | 结论 |
| -- | ----------: | -------: | ---- |
| 负对照（`--leak-mv 0`） | 0% | 1738 / 3329（随机应为 1664） | 正确：纯噪声下没有误报 |
| 正对照（`--leak-mv 6`） | **100%** | **1** | 正确：模型确实能检出它自己注入的泄漏 |

两边都对，说明"生成密文→跑协议→存 HDF5→算相关→排名"这条链路本身没有 bug。**这证明
的是分析流程是对的，不是硬件安全或不安全**——真实结论只能来自接了示波器之后的数据。

### 3.4 TVLA 的对照：fixed-vs-fixed

```bash
python acquire_cpa.py tvla-dry-run -n 300 --leak-mv 0 --seed $SEED   # 负对照
python acquire_cpa.py tvla-dry-run -n 300 --leak-mv 6 --seed $SEED   # 正对照
python tvla_analysis.py ../sim_result/traces/<文件>.hdf5
```

负对照的 `max|t|` 应该在噪声底（约 4.5）附近；正对照应该远超阈值、报 `leakage:
DETECTED`。和仿真级 TVLA 一样，先做这一步，再谈真实采集的结果能不能信。

---

## 4. CPA 实测流程

```bash
python acquire_cpa.py ports                                    # 找板子的 UART 口
python acquire_cpa.py check -p /dev/cu.usbserial-XXXXB           # 链路自检（不需要示波器）
python acquire_cpa.py scopes                                     # 列出当前能看到的 VISA 仪器
python acquire_cpa.py cpa-run -p /dev/cu.usbserial-XXXXB --ip <示波器IP> -n 20000
```

- 触发信号：`status_busy` 已经接到 Arty A7 的 LD4（引脚 H5），不用改 RTL。
- `BMUL_WINDOWS`（`config.py`）取自 `sim_result/tvla/<时间戳>_decaps/raw/main/tvla_t.csv`
  的微码列；改了 RTL/微码之后要重新核对（见 `ATTACK_MODEL.md` 第 4.3 节）。
- 采集中每 25 条插入一次已知密文（CPA 模式）或利用 TVLA 本身 fixed 类的重复
  （TVLA 模式），比较输出 K 是否保持一致，以及 `CYCLE_COUNT` 是否恒定——异常会打印
  警告，分别对应链路/板子复位和时序侧泄漏。

**密文策略**（`--strategy`，仅 CPA 模式）：

| 策略 | 说明 |
| ---- | ---- |
| `valid`（默认） | 每条都是一次全新的、合法的 FIPS 203 封装 |
| `invalid` | 随机字节当密文，走隐式拒绝路径（`c1` 怎么解都行，BaseMul 照样跑） |
| `structured` | 只让一个 BaseMul 对（`--poly`/`--pair` 指定）的 `û` 非零，其余全部清零，提升那一个点的信噪比——见 `ATTACK_MODEL.md` 第 7 节 |

采集完：

```bash
python cpa_attack.py sim_result/traces/<时间戳>_cpa.hdf5                       # 真实攻击（无 --dk-ref）
python cpa_attack.py sim_result/traces/<时间戳>_cpa.hdf5 --dk-ref dk.bin --curve  # oracle-assisted 评估 + 曲线
python cpa_attack.py sim_result/traces/<时间戳>_cpa.hdf5 --model HW               # 命名模型预设
```

**没有 `--dk-ref` 就是真实的"从零开始"CPA**：脚本不知道真密钥，只报告它自己算出来
的每个系数的最佳猜测、相关系数峰值——这正是你要评估的问题本身，没有参考答案。

---

## 5. TVLA 实测流程

```bash
python acquire_cpa.py tvla-run -p /dev/cu.usbserial-XXXXB --ip <示波器IP> -n 20000
python tvla_analysis.py sim_result/traces/<时间戳>_tvla.hdf5 --csv t.csv
```

TVLA 的向量生成直接复用 `pkg/sca`（`pqc-testkit sca gen` 的原生 fixed/random 输出，
`GenerateMLKEMTVLA`）——class 0 是固定密文重复，class 1 是每次新封装（或
`--invalid-random` 时是随机字节）。`tvla_analysis.py` 的 Welch's t 统计量和
`pkg/sca/tvla.go: WelchTTest` 是同一个公式（无偏方差、同样的 4.5 阈值）。

---

## 6. 命名模型预设

`--model` 对应 `mlkem_ref.py: MODEL_PRESETS`，CLI 和 GUI 都读这同一份定义：

| `--model` | 等价于 | 说明 |
| --------- | ------ | ---- |
| `HW` | `--target p_self --rprev zero` | `HW(BaseMul(û, ŝ_guess) mod q)`——单次 Barrett 乘积的汉明权重（`HD(0,X)=HW(X)`），寄存器 `p00`/`p11`，T+11 |
| `HD` | `--target p_self --rprev prev-word` | 同一个寄存器，`R_prev` 换成前一对的乘积 |
| `basemul-c0` / `basemul-c1` | `--target c0/c1 --rprev prev-word` | BaseMul 输出的偶/奇系数 |
| `basemul-word`（默认） | `--target word --rprev prev-word` | 24 位 RAM 写回字，物理上最贴近真实探头 |

每个预设对应哪个 RTL 寄存器、哪一拍，完整推导见 [`ATTACK_MODEL.md`](ATTACK_MODEL.md)
第 5 节。

---

## 7. 这个评估能回答什么、不能回答什么

**能回答**："在这套假设模型、这个采样点数、这个示波器设置下，相关系数能不能把密钥
系数从 3329 个候选里挑出来。" 能挑出来 → 这个具体攻击在这些条件下成功，**现有实现
（未加防护时）对此确定不安全**。挑不出来 → 只能说这一次没打穿，**不能**说"安全"。

**不能回答、常见的误判来源**：

1. **trace 数不够，不是没有泄漏。** 负对照干净不代表正式评估也干净，先按
   `docs/TVLA_SIM.md` 1.3 节的经验法则估计量级。
2. **窗口/触发没对齐，会把真实泄漏拍没。** 先做 TVLA 的 fixed-vs-fixed 对照
   （第 3.4 节）；如果对照组也报出高相关，说明是测量链路的问题，不是硬件的问题。
3. **模型本身是近似的。** BaseMul 的实际延迟链比这里建模的更细（`ATTACK_MODEL.md`
   第 9 节），这个脚本给出的是泄漏强度的下界，不是精确预测。
4. **逐系数攻击有"先鸡后蛋"的弱点**：同一对系数里先猜的那个，其搭档系数在没给
   `--dk-ref` 时仍按 0 处理（`cpa_attack.py` 模块文档）。
5. **换一次固定密钥/密文种子，结果会变——这是预期的，不是故障。** 评估"是否安全"
   应该跑多个不同种子。
6. **这只测了 Decaps 的三条 BMUL（P1/P2）。** P3、KeyGen 的 CBD/NTT、Encaps 重加密
   路径都没有覆盖。
7. **`structured` 策略构造出来的密文不是合法密文**，核心的隐式拒绝路径照常运行、
   返回的 K 没有意义——只用来提升目标 BaseMul 对的信噪比，不代表"真实攻击者会发
   这样的密文"（选择密文攻击在 KEM 场景下，攻击者确实可以发任意密文，所以这**是**
   一个合法的威胁模型，只是解密结果本身不重要）。

**结论句式应该是**："在 N 条 trace、这个窗口、这个模型下，CPA [能/不能] 用 <rank-1
比例> 的成功率恢复密钥系数"——不是笼统的"硬件安全"或"硬件不安全"。要支持"加了
防护之后变安全了"这个结论，需要在**同一个种子、同一套示波器设置**下，防护前后各跑
一次，比较 MTD（traces-to-disclosure），而不是只看某个固定 trace 数下跑没跑通。

---

## 8. Oracle-assisted 评估——明确的边界

`--dk-ref` / GUI 的 "dk-ref file path" 字段，**唯一**作用是事后打分：拿真实密钥核对
攻击猜对了没有、排第几名。它**从不**参与构造假设（见 `cpa_attack.py` 模块文档、
`mlkem_ref.py: PairModel` 文档、`ATTACK_MODEL.md` 第 5.4 节）。任何用了 `--dk-ref`
的结果，CLI 输出和 JSON 报告都会明确标成 `oracle_assisted: true` 并打印警告横幅；GUI
的 Analysis tab 也会显示同样的标注。**不要把 oracle-assisted 的排名/成功率当成"真实
攻击的结果"展示——它衡量的是"这个假设模型有多接近成功"，真实攻击是不给 `--dk-ref`
的那次运行。**

---

## 9. 已知局限

1. **`model_registry.py` 的"加载不同模型模块"目前只用于展示和自检**，`cpa_attack.py`
   的攻击引擎硬编码 import 了 `mlkem_ref`，还不支持真正切换成另一个模型模块跑攻击
   ——要做到这点需要把 `cpa_attack.py` 改成接受一个模型模块参数，本次没有做。
2. **`--curve` 对大 trace 数的文件会比较慢**：每个 checkpoint 都重新跑一次完整的
   768 系数攻击（没有做增量复用），这是离线分析工具可以接受的代价，不是实时工具。
3. **GUI 的结果展示是表格，不是渲染曲线图**：相关系数曲线、key-rank 曲线都以表格
   `+ CSV 下载`的形式提供，没有内嵌的 SVG/canvas 图表（降低没有执行测试环境下的实现
   风险）；要看实际曲线图，用 `cpa_attack.py --plots DIR`（需要 matplotlib）。
4. **真实硬件采集时，同一时刻只能有一个调用方使用串口/示波器**：Trace Acquisition
   启动后会把板子和示波器标记为"busy"，FPGA Connection/Advanced 的寄存器读写会被
   拒绝，直到采集完成——这是为了不让两路 UART 流量打架，不是说板子真的被锁住了。
5. 其余局限（延迟链未单独建模、先鸡后蛋问题、trace 数估计、多重检验）见第 7 节和
   `ATTACK_MODEL.md` 第 9 节。

---

## 10. 常见问题

| 现象 | 处理 |
| ---- | ---- |
| `cannot open <resource>` | 先确认 `config.py` 里的 `SCOPE_IP` 不再是占位符；`python acquire_cpa.py scopes` 看能不能发现仪器 |
| `pyvisa is missing` | `pip install -r requirements.txt` |
| `preflight failed` | 先跑 `check` 子命令看哪一项失败 |
| 负对照 CPA/TVLA 却报出高相关/高|t| | 分析脚本本身有 bug（窗口切片切错了），先在这里查，不要往下采集 |
| 正对照打不穿 | `mlkem_ref.py` 的数学有问题，先做第 3.2 节的自检 |
| `only N usable traces` | `--max-traces`，或部分 trace 的完整性检查失败（见采集日志） |
| GUI 报 "board is busy running acquisition job ..." | 有一个采集任务正在跑，等它结束，或者那是上一次异常退出遗留的状态——重启 `web_gui.py` 清掉 |
| `--curve` 报 "needs --dk-ref" | key-rank-vs-trace-count 曲线只能在 oracle-assisted 模式下算（需要真实密钥判断"成功"） |
