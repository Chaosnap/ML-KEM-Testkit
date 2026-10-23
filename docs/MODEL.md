# CPA 模型设计文档 — 打的是哪里、怎么打

> **2026-10-20：本文第 5 节描述的泄漏模型目录（`models/`：hw / hd / `pipe_*`）和逐周期对齐的（`aligned_cpa.py`、`cycle_features.py`）已经从代码里移除**，换成更简单的逐系数 CPA（`cpa.py`，说明见 [`LEAKAGE_MODELS.md`](LEAKAGE_MODELS.md)）。第 1–4 节（原理、目标、BMUL 在硬件里的时序、窗口位置）仍然有效；第 5 节及之后涉及 `models/`、`R_prev`、`pipe_*`、`--curve` 的内容只作为设计记录保留，对应旧代码在 git 历史里。

这份文档只回答一个问题：**这套 CPA 工具的假设模型具体是怎么设计的，物理上对应
硬件里的哪个信号、哪一拍、哪一行代码**。用法（怎么跑、怎么连示波器）见
[`README.md`](../cpa_toolkit/README.md)；这里只讲设计本身，带着源码行号和 RTL 行号，方便你答辩时
被问到细节能一条条指出来。

---

## 0. 一句话总结

对 ML-KEM-768 的 Decaps 做选择密文 CPA（者可以控制并知道每条 trace 的密文 `c`），
目标是解密运算 `K-PKE.Decrypt` 里的那一次 **ŝᵀ∘û 基乘（BaseMul）**，假设是：

```
HD( R_prev,  BaseMul(û, ŝ_guess) mod q )
```

其中 `û` 是者从密文算出来的已知量，`ŝ_guess` 是私钥系数的候选猜测（0..3328 共
3329 个候选），`R_prev` 是硬件寄存器在这次写入之前保存的旧值——建了三种物理上不同
的 `R_prev` 假设（寄存器清零 / 流水线连续覆写 / RAM 读-改-写累加），因为 CMOS 电路
翻转功耗天然对应汉明**距离**（旧值与新值的比特差异数），不是汉明权重。

---

## 1. CPA 原理回顾

Correlation Power Analysis（Brier, Clavier, Olivier, *Correlation Power Analysis with
a Leakage Model*, CHES 2004）的标准流程：

1. 采集 N 条功耗 trace，每条对应一个者已知的输入（这里是密文 `c`，因为 Decaps
   是个公开可调用的操作——任何人发一个密文过去都能触发一次解密）。
2. 对每个密钥候选 `k`，构造一个**假设功耗模型** `H(c_i, k)`：如果 `k` 是真的密钥，
   这个假设值应该和真实功耗线性相关；如果 `k` 是错的，假设值和真实功耗应该统计无关。
3. 对每个采样点 `t`，计算 `H(·, k)` 和 `trace[:, t]` 的 Pearson 相关系数 `ρ(k, t)`。
4. 真密钥对应的 `k` 应该在某个 `t` 上有明显突出的 `|ρ|`；取 `max_t |ρ(k, t)|` 给
   256（或这里是 3329）个候选排名，真密钥应该排第一。

这套流程本身和算法无关（AES、RSA、ML-KEM 都能套），**真正决定成不成的是第 2
步的假设模型**——模型是否对应硬件里真实存在、真实翻转、真实携带密钥信息的寄存器。
下面整篇文档都在讲这一步怎么设计的。

---

## 2. 为什么不能直接套 AES 的模型

教科书 CPA 模型几乎都是 `HW(SBox(P ⊕ K))`——这是 AES 的结构决定的：8 位明文字节异
或 8 位密钥字节，查一次非线性 S 盒，结果写进一个寄存器，汉明权重模型假设总线预充
电为 0（软件实现常见）或寄存器被清零。

ML-KEM 完全是另一套结构，这个模型在三个地方都套不上：

- **没有异或，没有 S 盒。** 私钥和明文/密文之间的运算是**模格上的多项式乘法**
  （`ŝᵀ∘û mod q`），不是比特异或。
- **没有字节粒度的密钥。** 私钥是 768 个系数（3 个多项式 × 256 个系数），每个系数
  在 `[0, 3328]` 取值（12 bit），候选空间是 3329，不是 256。
- **没有"总线预充电为 0"这个假设的物理基础。** 这是硬件 RTL 实现，寄存器是流水线
  寄存器和 RAM 写端口，上一拍写的是别的系数，不是 0——用 HW 模型会直接漏掉真实的
  泄漏来源（见下面第 5.1 节）。

所以这个工具的第一步工作就是：**抛掉 HW(P⊕K)，按这颗核心的真实 RTL 重新设计模型**
（`mlkem_ref.py` 的由来）。

---

## 3. 目标定位：Decrypt 的哪一步、为什么是它

### 3.1 K-PKE.Decrypt 整体流程（FIPS 203 Algorithm 14）

```
输入：解密密钥 dk_PKE = ByteEncode₁₂(ŝ)，密文 c = c₁ ‖ c₂
  û  = NTT(Decompress₁₀(ByteDecode₁₀(c₁)))      ← 密文部分，者已知
  v  = Decompress₄(ByteDecode₄(c₂))              ← 密文部分，者已知
  w  = v − INTT(ŝᵀ∘û) mod q                      ← 唯一用到私钥 ŝ 的地方
  m′ = ByteEncode₁(Compress₁(w))
```

`ŝᵀ∘û` 展开是三次多项式的 NTT 域逐项相乘再累加：`Σᵢ₌₀² ŝᵢ ∘ ûᵢ`，`∘` 是 NTT 域的
`BaseCaseMultiply`（FIPS 203 Algorithm 12）。**整个 Decrypt 唯一一处私钥和明文相关
量同时存在、相乘的地方，就是这三次 BaseMul。** 其余运算（`INTT`、`v − (…)`、压缩编
码）要么只碰密文、要么已经是乘积之后的结果——乘法本身是信息量最大的点（标准功耗
分析里"乘法比加法泄漏更多比特"的结论；早期的仿真级 TVLA（该仿真工具已从仓库移除）
也得到同样结果：P2（基乘组合后的结果）泄漏强度是 P3（后面那个减法）的 2.4 倍）。

### 3.2 恢复 ŝ 等价于恢复整条私钥

`dk_PKE = ByteEncode₁₂(ŝ)` 就是 ByteDecode 之后直接存的 NTT 域私钥，没有再做一次
NTT——也就是说 `mlkem_ref.py: s_hat_from_dk()` 里 `byte_decode(dk[:1152], 12)` 拿到
的就是**寄存器/RAM 里实际存在的那组系数**，不是还要再变换一次的中间量。CPA 恢复出
的 768 个系数猜测，拼起来就是完整私钥，等价于拿到了 `dk`。

### 3.3 为什么用 Decaps 而不是 Encaps/KeyGen

- **Decaps 是解密方需要暴露给外界的操作**（KEM 的 decapsulation oracle），者天
  然能发任意密文过去触发它，私钥固定不变——这是选择密文功耗分析的标准场景。
- KeyGen 只在产生密钥对时跑一次，者通常拿不到大量 trace。
- Encaps 不用私钥，没有可的点。

---

## 4. 硬件映射：BMUL 在 `mlkem_poly_alu.sv` 里的流水线

### 4.1 微码层：三次 BMUL 具体是哪几行

Decaps 微码（`hdl/core/mlkem_ucode_rom.sv`，由 `scripts/gen/gen_mlkem_ucode.py` 生成）：

```
ADDR 235-236: DECODE d=10 IN+2400 -> s6 ; NTT s6        // û₀ = NTT(Decompress₁₀(c₁[0]))
ADDR 237-238: DECODE d=12 IN+0    -> s7 ; BMUL s7*s6->s3        // ŝ₀∘û₀ → s3
ADDR 239-240: DECODE d=10 IN+2720 -> s6 ; NTT s6        // û₁
ADDR 241-242: DECODE d=12 IN+384  -> s7 ; BMUL s7*s6->s3 (acc)  // += ŝ₁∘û₁
ADDR 243-244: DECODE d=10 IN+3040 -> s6 ; NTT s6        // û₂
ADDR 245-246: DECODE d=12 IN+768  -> s7 ; BMUL s7*s6->s3 (acc)  // += ŝ₂∘û₂
ADDR 247:     INTT s3                                    // INTT(Σ ŝᵢ∘ûᵢ)
ADDR 249:     SUB s6 - s3 -> s3                           // v − (…)，这是 P3，不是本工具的目标
```

`s7` 每次都是 `DECODE d=12 IN+{0,384,768} -> s7`——**没有 CHECK 标志**（对照同一份
ROM 里 Encaps 路径的 `DECODE d=12 chk IN+0 -> s6`，第 252 行），因为 `dk` 是可信输
入，不需要像 `ek` 那样做模数范围检查（FIPS 203 §7.2）。`s6` 则是密文解出来再做一次
NTT 得到的 `û_i`——这正是 `mlkem_ref.py: u_hat_from_ct()` 算的那个量，和硬件里进
BMUL 之前的 `s6` 是同一个值。

三次 `BMUL s7*s6->s3` 对应 `models/base.py: Guess` 里的 `poly = 0, 1, 2`。

### 4.2 BMUL 一条流水线的时序（`hdl/core/mlkem_poly_alu.sv`）

BaseMul 算的是 `(a0 + a1·X)(b0 + b1·X) mod (X² − γ)`，Karatsuba 分解成 3 次标量乘法：

```
c0 = a0·b0 + γ·(a1·b1)
c1 = a0·b1 + a1·b0 = (a0+a1)(b0+b1) − a0·b0 − a1·b1
```

硬件用两个 7 级流水线 Barrett 乘法器（`mlkem_modmul`，`u_mul0`/`u_mul1`）轮流算这 3
个乘积，`mlkem_poly_alu.sv` 第 20-22 行注释给出了官方时序表，下面是逐寄存器展开
（T 是这个字的读地址发出的那一拍，源文件行号都给出来了）：

| 时刻 | 信号 | 源文件行 | 含义 | CPA 目标？ |
| --- | --- | --- | --- | --- |
| T+3 | `la`, `lb` | 251-252 | RAM 读出的 `{a1,a0}` / `{b1,b0}` 字（`a` = û 字，`b` = ŝ 字） | 否，输入不含猜测 |
| T+3 | `m0_a/b = la[11:0], lb[11:0]` | 230 | 乘法器 0 的输入：`a0, b0` | — |
| T+3 | `m0_r` (经 7 拍后 = T+10) | u_mul0 内部 | `a0·b0 mod q` | — |
| T+4 | `ksa, ksb` | 259-260 | `a0+a1`, `b0+b1`（Karatsuba 和项） | 否，和项本身不是乘积 |
| T+10 | `m1_a = m1_r` (上一次 `a1·b1` 的结果) | 232-233 | 乘法器 1 复用算 `(a1·b1)·γ` | — |
| **T+11** | **`p00`** | 269 | **`a0·b0 mod q`（锁存）** | **✔ `p_self`（`hw_scalar` / `hd_scalar`）** |
| **T+11** | **`p11`** | 270 | **`a1·b1 mod q`（锁存）** | **✔ `p_self`（`hw_scalar` / `hd_scalar`）** |
| T+11 | `s1 = msub(m0_r, p00)` | 277 | `(a0+a1)(b0+b1) − a0·b0` | 否（中间差，未单独建模） |
| T+12 | `h1 = msub(s1, p11d)` | 279 | `= c1`（减完两次，Karatsuba 交叉项） | 内部中间量，c1 要到 T+13 才稳定 |
| **T+13…T+17** | **`p00d[0..5]`** | 280-282 | **`a0·b0` 的 6 级延迟副本**（陪着 `h1` 走流水线） | 真实硅片上这些延迟寄存器也在翻转，本工具**没有单独建模**（见第 9 节局限） |
| **T+13…T+18** | **`h1d[0..4]`** | 283-285 | **`c1` 的 5 级延迟副本** | 同上，未单独建模 |
| T+17 | `h0 = madd(p00d[5], m1_r)` | 286 | `a0·b0 + a1·b1·γ = c0` | — |
| T+18 | `cd[0..13]` | 287-289 | `C`（累加目标的旧值）的 14 级延迟副本，陪着乘积走完流水线 | 对应 `--rprev acc` 的来源 |
| **T+18** | **`wb = {madd(cd[13][23:12],h1d[4]), madd(cd[13][11:0],h0)}`** | 290 | **`{c1,c0}` 加上累加值 `C_old`，24 位字，还没写进 RAM** | **✔ `word`（`hd_basemul_word`，配合 R_prev `acc`，`cd[13]` 就是真实的 `C_old`）** |
| **T+19** | **`wbd = wb`** | 291 | **`wb` 再锁存一拍** | **✔ `word`（`hd_basemul_word`）** |
| **T+21** | **`rb_din = wbd`（写回 RAM）** | 301, 189 | **真正写进多项式 RAM 的那一拍，`rb_din` 连续被下一个字覆写** | **✔ `hd_basemul_word` + R_prev `prev-word`：`rb_din` 这个寄存器是流水线里连续写入的，物理上最贴近真实探头能测到的信号** |

`c0`/`c1` 单独（不拼成 24 位字）对应上表里 `h0`/`h1d[4]` 这两个时刻，各有一个
模型文件（`models/hd_basemul_c0.py` / `hd_basemul_c1.py`），方便和 `word` 对比谁的
信号更强。

### 4.3 三条窗口在核心周期里的具体位置

每条 BMUL 处理 128 个字（256 个系数两两一对），每 2 个周期发一个字的读请求，再加上流水线排空，数据通路连续活动 276 个周期。这三条窗口不是猜的，是跑一次 Decaps 的周期精确 Verilator 仿真（`testbench/core_sim`，`+trace=` 指令时间线和 `+bmul=` 数据通路转储）量出来的，用 `scripts/sim/bmul_timing.py` 解析，两次不同 Decaps 的结果逐周期一致（核心是常数时间）：

```
  BMUL s7*s6->s3        (ŝ₀∘û₀, poly=0)   数据通路活动 1289-1564，第一个字的读地址在周期 1290
  BMUL s7*s6->s3 (acc)  (ŝ₁∘û₁, poly=1)   数据通路活动 2855-3130，第一个字的读地址在周期 2856
  BMUL s7*s6->s3 (acc)  (ŝ₂∘û₂, poly=2)   数据通路活动 4421-4696，第一个字的读地址在周期 4422
```

周期从 0 数起：周期 0 就是 `trig_out`（= STATUS.busy）升起的那个核心时钟周期，和 CYCLE_COUNT 数的是同一批周期。抄进了 `config.py: BMUL_WINDOWS`（窗口比活动多留了几个周期给写回的尾巴），核心时钟 206.25 MHz。同一次测量还给出：

```
  NTT û₀ 146-1143, NTT û₁ 1712-2709, NTT û₂ 3278-4275   （config.NTT_WINDOWS_CYCLES）
  INTT   4701-5841                                         （config.INTT_WINDOW_CYCLES）
  G(m′‖h) 6519-6623, J(z‖c) 6623-7375, 重加密 7375-25795, 比较/选择 25795-26367   （config.DECAPS_PHASES）
```

这些不是 CPA 的目标（û 是公开的，ŝ 不是 NTT/INTT 的操作数），记下来是为了在 trace 上**给任何一个特征命名**：例如 TVLA 的最强峰几乎一定在 J(z‖c)（整条密文被 Keccak 吸收）或重加密里，不在解密的 BMUL 里（见 [TRACE_ACQUISITION.md](TRACE_ACQUISITION.md) 1.3 节）。RTL 或微码改了之后要重新跑一次 `scripts/sim/bmul_timing.py`，把它打印的 config.py 块抄进去（这几个数字和 `mlkem_ucode_rom.sv` 的内容是绑定的）。

---

## 5. 假设模型完整设计

### 5.1 为什么是 Hamming Distance，不是 Hamming Weight

CMOS 同步时序电路的动态功耗主要来自寄存器翻转——对负载电容充放电的能量正比于**跳
变的比特数**，即 `HD(旧值, 新值) = HW(旧值 ⊕ 新值)`，不是新值本身的 1 的个数。软件
实现里常用 HW 模型是因为总线有预充电机制，近似等于"旧值恒为 0"；**这里是寄存器
RTL，没有这个假设的物理基础**——上表里任何一个寄存器，上一拍存的都是别的系数的真
实数据，不是 0。所以必须显式建模"旧值是什么"，这就是 `R_prev` 存在的原因，也是
这个工具和一般教科书 CPA 教程最大的设计差异。

`models/base.py: hw()` / `hd()` 实现：

```python
def hd(a, b, bits):
    return hw(a ^ b, bits)      # hw: 12 位查表，或两次 12 位查表相加（word，24 位）
```

查表（`_HW12`）预先算好 0..4095 每个值的比特数，4096 项的 numpy 数组，避免逐 trace
现算 `bin(x).count('1')`——这是把假设计算矢量化到 `(N, 3329)` 候选矩阵之后唯一的
性能考虑，不影响模型本身。

### 5.2 `X`：四种寄存器（`models/base.py: REGISTERS`）

| `register` | 公式 | 对应寄存器（见第 4.2 节表格） | 位宽 |
| --- | --- | --- | --- |
| `p_self` | `a_j · g mod q`，`g` 是猜测，`a_j` 是已知的 û 系数 | `p00` / `p11`（T+11） | 12 |
| `c0` | `(a0·b0 + γ·a1·b1) [+ C_old] mod q` | `h0`（T+17），`g` 替换 `b0` 或 `b1`（取决于猜的是哪个系数） | 12 |
| `c1` | `(a0·b1 + a1·b0) [+ C_old] mod q` | `h1d[4]`（T+18） | 12 |
| `word` | `c0 ‖ c1`（`c0 \| (c1<<12)`） | `wb`/`wbd`/`rb_din`（T+18 起） | 24 |

`p_self` 是最"干净"的点——只有一次标量乘法，没有 Karatsuba 交叉项、没有累加，理
论上信噪比最高，但对应的寄存器在硬件里只存在 1 拍（T+11 这一瞬间），真实探头采样
率不够高的话容易漏过。`word` 是最贴近"写进 RAM 的最终值"的点，也是默认选项。

> 上面这四个寄存器是 `hw_*` / `hd_*` 系列的目标。它们有一个共同的前提——"寄存器上一拍存的是上一个字
> 的同一个值"——和这颗核心的实际发射时序对不上（第 5.5 节），所以另有一组按时序建模的 `pipe_*`
> 模型。

**模型文件（`cpa_toolkit/models/`，一个文件一个模型）**：每个模型选定一个寄存器、
它接受的 R_prev，以及泄漏函数 `leak(x, r_prev)`。`hw_scalar`（`p_self`，无 R_prev）
和 `hd_scalar`（`p_self`，R_prev 为 `prev-word` 或 `zero`）分别对应任务书里点名要做的
`HW((û × s_guess) mod q)` 和 `HD(R_prev, (û × s_guess) mod q)`——数学上
`HD(0, X) = HW(X)` 恒成立（异或 0 不改变比特、popcount 就是汉明权重），所以
`hd_scalar` 配 `zero` 与 `hw_scalar` 的假设完全相同。另外三个是 `hd_basemul_c0`、
`hd_basemul_c1`、`hd_basemul_word`（默认）。CLI 用 `--model <文件名> --rprev <前态>`，
GUI 在 Analysis 页的模型下拉框里选；旧的预设名（`HW`、`HD`、`basemul-*`）仍然可用。
加新模型的方法见 `cpa_toolkit/README.md` 第 6 节。

### 5.3 `R_prev`：三种前态假设（`models/base.py: RPREVS`）

| R_prev | 取值 | 对应物理行为 | 代码位置 |
| --- | --- | --- | --- |
| `zero` | 恒为 0 | 寄存器被同步复位清零，或者是"保守默认，不知道前态就假设是 0"——对 `p_self` 这种只活 1 拍的寄存器比较合理，因为它清晰的"前态"是上一次被该乘法器算的完全不同的东西 | `Guess.previous()` |
| `prev-word` | 同一条 BMUL 遍里，**上一个**字的 `word` 值 | `rb_din`/`wb`/`wbd` 是流水线寄存器，连续写入下一个字——第 w 个字进来时，寄存器里还留着第 w-1 个字 | `Guess.previous()`，递归调用 `self.register(..., w-1)` |
| `acc` | `C_old`，前面已经处理过的多项式的累加和 | RAM 字读-改-写：第 2/3 个多项式的 BMUL 要把新乘积加到已有的部分和上，`cin`/`cd[13]`（见第 4.2 节表格 T+18 行）就是这个旧值 | `Guess.previous()` |

`zero` 和 `word` 的差异本质是：**要不要显式建模流水线寄存器连续被覆写这个事实**。
用 `zero` 相当于假设每次写入都是从一片空白电路写入，用 `prev-word`/`acc` 相当于承
认这是一条真实存在的、数据连续流过的流水线——后者更贴近物理现实，这也是为什么
正对照（见 README 第 3.3 节）必须配 `--rprev prev-word` 才能把注入的泄漏打穿（配
`zero` 只有 0.8% rank-1，配 `prev-word` 是 92.8%，同一组 trace、同一个寄存器
`word`，差距就在 R_prev 这一个假设上——第 6 节有完整对照）。

### 5.4 `Guess`：猜测怎么构造（`models/base.py: Guess`）

BaseMul 的 Karatsuba 分解里，一次乘法同时涉及**一对**系数（`b0=ŝ[2w]`,
`b1=ŝ[2w+1]`），而 CPA 一次只猜一个系数 `j`。`Guess` 的设计：

- `e = j & 1` 决定猜的是这对里的偶数下标（`b0`）还是奇数下标（`b1`）；
- **搭档系数**（没在猜的那一个）和**累加值 `C_old`**，在"已知密钥评估"模式下直接
  用真实密钥的值（`--dk-ref` 给的那个 `dk`）——这是标准的"已知密钥"CPA 评估方法：
  衡量的是"泄漏距离破解有多近"，不是"搜索算法有多聪明"；
- 在**真正不知道密钥**的模式（`cpa.py` 不给 `--dk-ref`）下，`recovered`
  数组会被原地修改（不是拷贝），所以同一对里**后猜的那个系数能用上先猜的那个的
  结果**（见 `cpa.py: run_cpa` 里的注释）——但同一对里**先猜的那个**，
  它的搭档系数永远只能按 0 处理，这是"逐系数"相对"联合枚举一对系数"的固有
  弱点，`README.md` 第 6 节第 4 条和本文档第 9 节都提到了，这里不重复展开。

### 5.5 按发射时序建模（`pipe_*`）

第 5.3 节的 `prev-word` 假设"第 w 个字进来时寄存器里留着第 w−1 个字的同一个值"。看
`mlkem_poly_alu.sv` 第 225-247 行的操作数多路选择：两个 Barrett 乘法器**每一拍都接一个新运算**，
而且两种运算交替发射——

```
mul0:  ... A0(w−1) , K(w−1) , A0(w) , K(w) , A0(w+1) ...     A0 = s0·u0 ,  K = (s0+s1)·(u0+u1)
mul1:  ... A1(w−1) , G(w−4) , A1(w) , G(w−3) , A1(w+1) ...   A1 = s1·u1 ,  G = (a1·b1)·γ
```

——所以 `p00` 相邻两拍之间的翻转是 `A0(w)` 对 **`K(w)`**，中间根本不存在"上一个字的 A0"。
把两种假设拿到周期精确仿真里和真实翻转数比（`scripts/sim/model_vs_rtl.py`）：

| 假设 | 与仿真总翻转的相关系数 |
| --- | --- |
| `hd_basemul_word` / `hd_scalar` 等（任意 R_prev） | ≈ 0.01 |
| 按真实发射时序，每拍一个假设 | ≈ 0.3（峰值拍） |

`cpa_toolkit/mlkem_pipeline.py` 把两个乘法器的每一级流水线寄存器和复制其输出的寄存器逐位复刻，
`scripts/sim/pipeline_check.py` 证明它和 RTL 的每周期翻转数**完全一致**（全部 35 个寄存器 ×
三次解密 BMUL × 每一拍；把任一延迟改错一拍即报错）。由此得到 `models/pipe_{all,dsp,barrett,output}.py`：
一个系数在流水线里停留约 15–17 拍，每一拍都是一条独立证据，求和后等效相关系数约提升 √K。

侧在 `cpa_toolkit/aligned_cpa.py`：trace 先按核心时钟折成每周期一个值
（`cycle_features.py`：从跨 trace 方差估时钟相位，再用 PCA / energy / abs 压缩周期内的采样点），
每个假设只和**它自己那一拍**做相关；trigger 到采样点 0 之间的整数周期偏移用一次小范围搜索定出来
（`config.BMUL_READ_CYCLES` 给出每条 BMUL 第一个字的读周期）。用法和局限见
[LEAKAGE_MODELS.md](LEAKAGE_MODELS.md) 第 7 节。

---

## 6. CPA 引擎：Pearson 相关系数（`cpa.py: pearson_matrix`）

标准化后做一次矩阵乘法，是唯一在 3329 候选 × 几千采样点规模下能接受的算法复杂度：

```python
def pearson_matrix(hyp, traces):      # hyp: (N, 3329)   traces: (N, T)
    h = (hyp - hyp.mean(0)) / hyp.std(0)
    t = (traces - traces.mean(0)) / traces.std(0)
    return (h.T @ t) / N              # (3329, T)
```

每个候选对每个采样点的相关系数一次算完；候选的最终得分是 `max_t |ρ(k,t)|`，768
个系数各自独立重复这个流程（见 `run_cpa` 里对 `poly in range(3)` × `j in
range(256)` 的双重循环）。

---

## 7. 规模

- 候选空间：每个系数 3329（`Q`，不是寄存器位宽对应的 `2^12` 或 `2^24`——寄存器位
  宽只决定 Hamming 距离怎么算，猜测空间永远是系数的合法取值范围）。
- 系数总数：768（3 个多项式 × 256 个系数）。
- 窗口裁剪：每条 trace 先按 `config.py: BMUL_WINDOWS` 切出对应那条 BMUL 的
  ~281+2×`TRIGGER_SLACK_CYCLES` 个采样点再做相关，不用整条 trace——省计算量，也
  避免窗口外其他运算（NTT、Keccak）的翻转稀释相关系数。

### 7.1 结构化选择密文（`--strategy structured`，已删除）

> 2026-10-23：这个策略已从 `testvectors` 删除（原因见 9.6 节）。本节保留原始思路作记录。

CPA 的信噪比可以靠**选择密文**本身提升，不只靠多采 trace——经典的"模板"思路：
让除了目标 BaseMul 对以外的一切都不贡献能量。`mlkem_ref.py: structured_ciphertext
(poly, pair, rng)` 构造一个密文，使得它的 `û` 只在 `poly` 多项式的第 `pair` 对
（系数 `2*pair`、`2*pair+1`）非零，其余 767 个系数全部为 0：

- 其他两个多项式的 `û` 全零 → 它们的 BaseMul 乘积恒为 0 → 对 `--rprev acc` 用到的
  `C_old` 没有任何贡献，累加器在目标那一对之前保持精确为 0。
  （这同时意味着这个策略下 `C_old` 变得可预测，相当于把 `--rprev acc` 的不确定性
  也消掉了，如果目标寄存器选的是 `c0`/`c1`/`word` 配 `acc`，信噪比会进一步提升。）
- 同一个多项式里其他 127 对的 `û` 也是 0 → 它们的乘积同样是 0，不会在窗口外或窗口
  内叠加噪声能量。

构造方法：选定的 `û` 做一次 `INTT`（`mlkem_ref.py` 已有的 `intt()`）得到多项式域的
`u`，`Compress₁₀` 再 `ByteEncode₁₀`（新增的 `byte_encode()`，`byte_decode()` 的精确
数学逆）得到 `c1`；`c2` 全零填充。**这不是合法密文**：

- 没有用到 `ek`、没有消息 `m`，核心的隐式拒绝路径会照常跑完并返回某个 K，但那个
  K 没有密码学意义——只是用来把目标 BaseMul 对的信号从其余 767 个系数的噪声里分
  离出来。
- `c2`（压缩后的 `v`）可以是任意字节：Decaps 微码里三次 BMUL（ADDR 237-246）在
  `c2` 被读取（ADDR 248 的 `DECODE d=4 IN+3360 -> s6`）**之前**就已经跑完——见第
  4.1 节的完整微码表。
- 10 位压缩是有损的，所以硬件实际算出来的 `û` 不会和构造时选的值逐比特相同，只
  会接近；真正进假设模型的 `û`，永远是从返回的真实密文字节重新算一遍
  `u_hat_from_ct()`，不是构造时的"意图值"。

这是一个合法的研究策略：KEM 的解封装预言机本来就允许者发任意密文（这正是
"选择密文"场景本身），只是这里构造的密文**不追求被接受**，只追求让目标点的
信号尽量干净。

---

## 8. 这个设计已经验证到什么程度

完整的自检方法、正负对照数据、12 种 target×R_prev 组合的判别力网格，见
[`README.md`](../cpa_toolkit/README.md) 第 3 节和第 7 节，这里不重复，只给结论索引：

- **数学对不对**：`mlkem_ref.py` 的软件解密对照 Go 参考实现——已过。
- **流程对不对**：负对照（纯噪声）排名接近随机，正对照（注入已知泄漏）100%
  rank-1——已过。
- **模型是否在分辨正确的寄存器+前态组合**：12 种组合里只有真正匹配注入模型的那
  一种（`word`/`prev-word`）打穿，其余保持低位——已过。
- **真实硬件上是否真的这样泄漏**：未知，需要示波器——这正是这份模型设计要去验证
  的东西，不是已经验证过的东西。
- **Unknown-key（真实）和 oracle-assisted（事后评估）的边界**：`--dk-ref`/
  GUI 的 dk-ref 字段只用来给已经算出来的猜测打分，从不参与假设构造（第 5.4 节）；
  `cpa.py` 的 JSON/CLI 输出会明确标注 `oracle_assisted: true` 并打印警告
  横幅，不会把两种模式的结果混着呈现——见 `README.md` 第 8 节。

---

## 9. 已知局限（设计层面，补充 `cpa_toolkit/README.md` 第 7 节）

1. **寄存器模型的延迟链副本没有单独建模。** 第 4.2 节表格里 `p00d[0..5]`、`h1d[0..4]` 这些延
   迟寄存器副本，真实硅片上每一级都在独立翻转、独立消耗功耗，`hw_*` / `hd_*` 只建模
   了"当前字"（`word`/`c0`/`c1`）和"上一个字"（`prev-word`）两个时间点。第 5.5 节的 `pipe_*`
   模型把 `p00d[0..5]`、`mr`、`mr2` 这些副本都纳入了（并且和 RTL 逐周期比对过），但仍然
   **没有**覆盖 Karatsuba 的两次减法（`s1`、`h1`、`h1d`）、累加器读回（`cd[0..13]`）和写回
   （`wb`、`wbd`、`rb_din`）。这些寄存器携带同一批数据，只是晚几拍，所以现有模型给出的仍然是
   泄漏强度的下界，不是精确预测。
2. **逐系数的"先鸡后蛋"问题。** 见第 5.4 节，一对系数里先猜的那个搭档永远未知。
3. **只覆盖了 Decrypt 的 BaseMul（P1/P2），没覆盖 P3（减法）、KeyGen 的 CBD/NTT、
   Encaps 重加密路径。** 早期仿真级 TVLA 的结果显示 P2 泄漏最强，
   但那是未加防护核心的结论，加了防护之后哪个点最强需要重新定位。
4. **R_prev 的"真实前态"本身依赖假设。** 比如 `zero` 假设第一次写入前寄存器是 0，
   但真实硬件上一次运算（甚至上一次开机）留下的电平未必是 0——只有示波器实测才
   能确认某个具体寄存器在某个具体时刻的真实前态是什么。

---

## 9.5 另一条路：明文检查预言机（PC-oracle）—— 实测能恢复整条密钥

CPA 把问题设成"一个 NTT 域系数的 3329 选 1"，还要在每拍 ~1400 个翻转里把它找出来。
如果 TVLA 最强峰落在 J(z‖c) / 重加密（本仓库实测 \|t\|=9，见 [TRACE_ACQUISITION.md](TRACE_ACQUISITION.md) 1.3 节），
说明**消息 m′ 的泄漏又强又好测**。那就不绕 BaseMul，直接利用它。代码在
[`pc_oracle.py`](../cpa_toolkit/pc_oracle.py)，这是公开的类别（Ravi 等，TCHES 2020；Ueno 等，TCHES 2022）。

**构造。** Decaps 接受任意字节当密文。令 `u` 只在某个多项式槽是常数 `k`、其余为 0，则
`INTT(ŝᵀ∘û) = k·s_poly`，于是 `m′[t] = Compress₁( v[t] − k·s_poly[t] )`。把 `v` 只在位置 `t` 非零、
`k = Decompress₁₀(64) ≈ q/16`，就能保证其余 255 位无论密钥是什么都压成 0 —— **m′ 要么全 0，要么只有 1 位，
取决于 s_poly[t]**。ML-KEM-768 的私钥系数很小（CBD η=2，取值 −2..2），把 `v[t]` 扫四档就得到该系数的
温度计码：`s = 2 − (1 的个数)`（`pc_oracle.plan`，每个系数 4 次查询）。

**判读不需要密钥。** `z` 取一档使 m′ 恒为 0、另一档使 m′ 恒为 1（对所有可能的密钥都成立，
`pc_oracle.REF_ZERO/REF_ONE`），这两组在同一块板子上采作模板；每个系数的扫描查询拿自己的功耗特征和这两个
模板比，近哪个就是哪个 bit（`classify` / `decode_campaign`）。

**为什么它能成而 CPA 在你那组数据上是 0。** 它读的正是 m′→G(m′‖h)→重加密这一段（`config.DECAPS_PHASES`
里 6519 周期往后），也就是 \|t\| 峰所在、功耗最大的那一段；每条 trace 只做一个**二元**判决，可以任意多次
平均（同一条查询的每条 trace 完全一样）。软件端对完整 768 系数**逐一恢复无误**
（`python cpa_toolkit/pc_oracle.py verify`，3072 次查询）；合成 EM trace（每周期特征 SNR 0.5、每条查询平均约
20–100 次）即可拿满覆盖的系数。而 `--strategy structured` 的那组数据，`û` 对多项式 1、2 **恒等于 0**，
512/768 个系数对任何模型都没有信息——rank 从一开始就被钉在随机线上（见 9.6 节）。

**代价。** 整条密钥 768×(4 扫描 + 2 参考) = 4608 次查询，每次重复 R 条。它不是比 CPA"更省 trace"，而是把
"难测的 3329 路相关"换成"好测的二元判决"，并且**覆盖整条密钥**。不是合法密文，核心走隐式拒绝返回一个无用的 K
——测的是消息相关功耗，不是那个 K。

## 9.6 已修正：`--strategy structured` 不隔离，且会废掉 2/3 密钥

`mlkem_ref.structured_ciphertext` 想让 `û` 只在一对系数非零，做法是对时域多项式做 10 位压缩。问题是
压缩的 ±2 舍入误差经过核心的 NTT 后变成**全域 O(q) 的误差**（乘以单位根再 mod q，小量荡然无存），于是被
多项式的其余 254 个系数回来都近似随机；更要命的是另外两个多项式的 `û` **精确为 0**。结果：512/768 个系数对
**任何**模型都没有可利用的泄漏，rank-1 上限 33%。现在：

- `cpa.py` 会检测"这个系数的假设根本不随猜测/trace 变化"（`informative`），把它们单独计数并从统计里剔除，
  CLI/报告明确写出"本次采集有 N 个系数不含密钥信息——是密文的问题，不是"（而不是打印一个看起来像失败的随机 rank）；
- `pipe_*` 的周期偏移搜索窗口放宽到 `−12..12`，并在最优偏移落在边界时告警（你那次 `−6` 正好railed 在旧窗口边缘）；
- `testvectors` 的 `structured` 已于 2026-10-23 删除（旧文件仍能读取）。CPA 请用 `valid`（覆盖整条密钥），密钥恢复用 `pc-oracle`。

## 10. 文件索引

| 内容 | 文件 |
| --- | --- |
| 本文档引用的 FIPS 203 软件参考实现 | [`mlkem_ref.py`](../cpa_toolkit/mlkem_ref.py) |
| ~~泄漏模型（一个文件一个模型）~~（已移除，见 [`LEAKAGE_MODELS.md`](LEAKAGE_MODELS.md)） | ~~`models/`~~ |
| 乘法器流水线的软件孪生（`pipe_*` 的基础） | [`mlkem_pipeline.py`](../cpa_toolkit/mlkem_pipeline.py) |
| ~~每周期一个特征 / 逐拍对齐的 CPA 引擎~~（已移除） | ~~`cycle_features.py`~~、~~`aligned_cpa.py`~~ |
| 仿真侧测量脚本（窗口、寄存器排序、孪生比对） | [`scripts/sim/`](../scripts/sim/) |
| 明文检查预言机（PC-oracle）密钥恢复 | [`pc_oracle.py`](../cpa_toolkit/pc_oracle.py) |
| BMUL 流水线 RTL（第 4.2 节表格的全部行号出处） | [`../hdl/core/mlkem_poly_alu.sv`](../hdl/core/mlkem_poly_alu.sv) |
| Decaps 微码（第 4.1 节的 ROM 地址） | [`../hdl/core/mlkem_ucode_rom.sv`](../hdl/core/mlkem_ucode_rom.sv) |
| 微码生成器（slot/操作码定义） | [`../scripts/gen/gen_mlkem_ucode.py`](../scripts/gen/gen_mlkem_ucode.py) |
| 简单逐系数 CPA：两个假设、相关系数、top-10 与稳定性、oracle-assisted 排名 | [`cpa.py`](../cpa_toolkit/cpa.py) |
| TVLA 实测分析引擎（Welch's t） | [`tvla_analysis.py`](../cpa_toolkit/tvla_analysis.py) |
| CPA/TVLA 采集、三种密文策略、结构化密文生成调用方 | [`acquire_cpa.py`](../cpa_toolkit/acquire_cpa.py) |
| 用法、正负对照、TVLA 流程、泄漏模型与新增模型、常见问题 | [`README.md`](../cpa_toolkit/README.md) |
