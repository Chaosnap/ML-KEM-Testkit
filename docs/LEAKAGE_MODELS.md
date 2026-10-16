# 泄漏模型速查：寄存器里之前是什么、之后是什么、泄漏怎么算

对应代码：`cpa_toolkit/models/`（每个文件一个模型），共用部分在 `models/base.py`。RTL 位置和流水线拍见 [ATTACK_MODEL.md](ATTACK_MODEL.md) 第 4.2 节。

---

## 1. 统一结构

每个模型都在算

```
leak(trace i, 猜测 g) = f( R_prev , X )
```

- **X（之后的值）**：这一拍写进寄存器的新内容，由猜测 g 决定；
- **R_prev（之前的值）**：写入之前寄存器里的内容；
- **f**：HD 模型是 `popcount(R_prev ⊕ X)`，HW 模型是 `popcount(X)`。

CMOS 寄存器的动态功耗正比于翻转的位数，所以用 R_prev ⊕ X。HW 相当于假设 R_prev = 0。

CPA 对每个候选 g（0…3328）算出所有 trace 的 leak，与实测功耗逐采样点做 Pearson 相关，取 max |ρ| 排名。

---

## 2. 五个模型一览

| 模型（文件） | 寄存器（RTL） | 位宽 | X：之后的值 | R_prev：之前的值（可选） | 泄漏 |
| ------------ | ------------- | ---- | ----------- | ------------------------ | ---- |
| `hw_scalar` | `p00` / `p11`（T+11） | 12 | `a_j · g mod q` | 无（视为 0） | `HW(X)` |
| `hd_scalar` | `p00` / `p11`（T+11） | 12 | `a_j · g mod q` | `prev-word`：上一个字（w−1）的同一乘积；`zero`：0 | `HD(R_prev, X)` |
| `hd_basemul_c0` | `h0`（T+17） | 12 | `c0`（偶输出系数，含 C_old） | `prev-word`：上一个字的 c0；`zero`；`acc`：C_old 的偶系数 | `HD(R_prev, X)` |
| `hd_basemul_c1` | `h1d[4]`（T+18） | 12 | `c1`（奇输出系数，含 C_old） | `prev-word`：上一个字的 c1；`zero`；`acc`：C_old 的奇系数 | `HD(R_prev, X)` |
| `hd_basemul_word`（默认） | `wb` / `wbd` / `rb_din`（T+18…21） | 24 | `{c1, c0}` = `c0 \| (c1 << 12)` | `prev-word`：上一个字的 `{c1, c0}`；`zero`；`acc`：`{C_old[2w+1], C_old[2w]}` | `HD(R_prev, X)` |

第 1 个多项式（poly = 0）没有 C_old，`acc` 取 0；第 1 个字（w = 0）没有上一个字，`prev-word` 取 0。

---

## 3. X 是怎么算出来的

对第 w 对系数（w = 0…127），被猜的系数 `j`，`e = j & 1`：

```
a0 = û[poly][2w]        a1 = û[poly][2w+1]        (公开)
b0 = ŝ[poly][2w]        b1 = ŝ[poly][2w+1]        (其中第 e 个被替换成猜测 g)

p_self = a_e · g mod q                              ← hw_scalar / hd_scalar 的 X
c0 = (a0·b0 + γ_w · (a1·b1 mod q)) mod q            γ_w = ζ^(2·BitRev7(w)+1)
c1 = (a0·b1 + a1·b0) mod q
poly ≥ 1:  c0 += C_old[2w],  c1 += C_old[2w+1]   (mod q)
X_word = c0 | (c1 << 12)
```

---

## 4. 需要用到的中间值

| 中间值 | 含义 | 来源 | 公开？ | 用在哪 |
| ------ | ---- | ---- | ------ | ------ |
| `û` (3×256) | `NTT(Decompress₁₀(c1))`，密文第一部分 | 从每条 trace 的密文 `ct` 算出 | **公开** | 所有模型的 a0、a1 |
| `ŝ` 的搭档系数 | 同一对里没被猜的那个（b0 或 b1） | 打分模式：真密钥 `dk` 解码；盲攻击：已恢复的猜测（没恢复的为 0） | 密钥相关 | c0、c1、word |
| `γ_w` | BaseMul 的常数 `ζ^(2·BitRev7(w)+1)` | FIPS 203 常数表 | 公开 | c0 |
| `C_old` (poly×256) | 前面多项式的累加和 `Σ û_i ∘ ŝ_i`（i < poly） | 由 û 和 ŝ（真密钥或已恢复）算出 | 密钥相关 | c0、c1、word 的 X；R_prev = `acc` |
| 上一个字的 X（w−1） | 同一遍 BMUL 的前一个字，用同样公式算 | 同上，w−1 | 密钥相关 | R_prev = `prev-word` |
| 窗口采样点 | BMUL 的 ~281 个周期（±40）对应的采样 | `config.BMUL_WINDOWS` + 采样率 | — | 与 leak 做相关 |

**对猜测 g 来说：** 只有 X 随 g 变化。`prev-word` 的上一个字属于另一对系数，不含这个猜测，所以对候选值是常数项，只改变 HD 的基线。`acc` 的 C_old 也不含当前多项式的猜测。

**盲攻击与打分模式：** 搭档系数、C_old、上一个字都依赖 ŝ。打分模式用真密钥（只用来评估，不是真实攻击）；盲攻击用前面已恢复的猜测，一对里先猜的那个系数的搭档只能按 0。

---

## 5. 示例（默认模型，poly = 1，猜 ŝ[1][5]，w = 2，e = 1）

```
b1 = g（猜测）            b0 = ŝ[1][4]（搭档）
X       = { c1, c0 } ，c0 = a0·b0 + γ_2·a1·g + C_old[4] ，c1 = a0·g + a1·b0 + C_old[5]   (均 mod q)
R_prev  = 上一个字 w=1 的 { c1, c0 }（用 ŝ[1][2]、ŝ[1][3] 和 C_old[2]、C_old[3] 算）
leak    = popcount( R_prev ⊕ X )    （24 位）
```

---

## 6. 已知局限

- 这些模型是项目根据 RTL 推出的设计假设，没有 ML-KEM 专用的文献出处；通用的 CPA / 汉明距离思路见 Brier 等，CHES 2004。
- 6+5 级延迟副本（`p00d`、`h1d`）没有建模，只建了"当前字"和"上一个字"。
- 真实硬件上是否这样泄漏尚未用示波器验证；用 TVLA 和 GUI 的 *Compare models* 对比。
