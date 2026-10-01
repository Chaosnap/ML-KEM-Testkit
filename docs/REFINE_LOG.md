# ML-KEM-768 baseline 精简记录（BRAM / DSP / 周期数）

目标板 Arty A7-100T（xc7a100tcsg324-1），100 MHz。只支持 ML-KEM-768。
所有资源数字均为 `u_mlkem`（`pqc_mlkem_top`，不含 UART 桥），布线后。

- ENS = Slice + 100 × DSP + 200 × BRAM（BRAM 以 RAMB36 计，RAMB18 = 0.5）
- ATP = ENS × Decaps 周期 / f，单位 ENS·ms（f = 100 MHz）
- Decaps 周期 = 10 个 ACVP decapsulation 向量的 CYCLE_COUNT 平均值（SAMPLE 的拒绝采样使其随数据变化约 ±150）
- Fmax = 1 / (5 − WNS@5 ns)，5 ns 约束只用于测量，不提交

## 结果表

| Step | LUT | FF | Slice | DSP | BRAM | ENS | Decaps cycles | WNS @100 MHz | ACVP | ATP (ENS·ms) | Fmax (MHz) |
| ---- | --: | -: | ----: | --: | ---: | --: | ------------: | -----------: | ---- | -----------: | ---------: |
| HEAD e2ba16b（参考，9/26 构建） | 10,212 | 4,205 | 2,897 | 8 | 9 | 5,497 | 110,560 | +0.028 ns | 60/60 | 6,077 | — |
| 0 | 10,455 | 4,259 | 3,013 | 8 | 6 | 5,013 | 110,560 | +0.132 ns | 60/60 | 5,542 | 104.9 |
| 1a | 未跑 | | | 8 | 5.5（推断） | | 110,560 | 未跑 | 60/60 | | |
| 1b | 未跑 | | | 8 | 5（推断） | | 110,560 | 未跑 | 60/60 | | |
| 1c | 未跑 | | | 8 | 3（推断） | | 110,560 | 未跑 | 60/60 | | |
| 2 | 未跑 | | | 2（推断） | 3（推断） | | 110,560 | 未跑 | 60/60 | | |

第 0 步的 RTL 是 commit bf2ce6d（此前未提交的 `FIXED_LEVEL` / 768-only ROM 改动），
见下文"与任务描述的差异"。HEAD 一行来自 `build/vivado/arty-a7-100t_mlkem/`
里 9/26 的项目模式报告（副本在 `reports/step0/head_e2ba16b/`）；它的 Slice
是任务描述给出的值，ACVP 和周期数是本次在仿真中对 HEAD 重新测得的（与 bf2ce6d 逐周期相同）。

从第 1a 步起按你的要求不再运行 Vivado，只在仿真中验证（ACVP 60/60、全部回归、
周期统计）。资源列只写由 RTL 推断出的 DSP / BRAM 数（标"推断"），等你自己跑
Vivado 后再补 LUT / FF / Slice / WNS。

## 测量方法（命令）

所有命令在仓库根目录运行。

| 用途 | 命令 | 说明 |
| ---- | ---- | ---- |
| 全部仿真回归 | `make regress`（= `scripts/regress.sh`） | Go 单测、Icarus（UART、NTT、UART-link）、cocotb（若 `cocotb-config` 在 PATH）、ACVP |
| ACVP（仿真） | `make sim-acvp`（= `python3 scripts/sim_acvp.py`） | keyGen 25 + encaps 25 + decaps 10 = 60 必须全过；另测 encapsulationKeyCheck 10、SEC_LEVEL 512/1024/999 和 OP_MODE 3 → 错误码 1 |
| 按指令统计周期 | `python3 scripts/profile_ucode.py --op all --per-pc --md reports/stepN/profile_ucode.md --json reports/stepN/profile_ucode.json` | 每个忙周期计一次：C_NEXT/C_FETCH/C_DECODE 记为 overhead，其余状态记为该 pc 指令的 exec；总和与 CYCLE_COUNT 逐周期核对 |
| Vivado 实现 + 报告 | `vivado -mode batch -nolog -nojournal -source scripts/vivado_reports.tcl -tclargs reports/stepN 10` | 非项目模式，默认 directive，不生成 bitstream；输出 `utilization_hier.rpt`、`utilization_u_mlkem.rpt`、`timing_summary.rpt`、`summary.txt` |
| Fmax 测量 | 同上，`-tclargs reports/stepN/fmax_5ns 5` | 布线前用 `create_clock -period 5` 覆盖 XDC 的时钟周期 |
| 表格行 | `python3 scripts/refine_row.py reports/stepN --step N` | 从上面的报告计算 ENS / ATP / Fmax |
| 硬件 ACVP | `python3 scripts/uart_test.py -p <port> acvp acvp/ML-KEM-keyGen-FIPS203/internalProjection.json acvp/ML-KEM-encapDecap-FIPS203/internalProjection.json` | 已有工具，上板验证用 |

Vivado 在 Docker 容器 `vivado-container`（Vivado 2024.1，宿主目录
`~/Downloads/Vivado` 挂载到 `/home/vivadouser/project`）中运行：

```bash
docker start vivado-container
docker exec -w /home/vivadouser/project/ML_KEM_Test/ML-KEM-Testkit vivado-container bash -lc \
  'source /home/vivadouser/Vivado/2024.1/settings64.sh && R=$PWD && mkdir -p build/vivado_stepN && cd build/vivado_stepN && \
   vivado -mode batch -nolog -nojournal -source $R/scripts/vivado_reports.tcl -tclargs $R/reports/stepN 10'
```

10 ns 和 5 ns 两次实现不要同时跑：容器内存（11 GB）不够，第二个进程会被无声杀掉。

新增的仿真基础设施：

- `testbench/core_sim/tb_core.sv`：Verilator 全核批处理 testbench，经 AXI-Lite
  按主机流程运行任意多个操作；输入 / 输出偏移取自 DATA_IN_ADDR / DATA_OUT_ADDR
  的复位值，所以第 1c 步改布局后无需修改。60 个 ACVP 向量约 2 秒。
- `scripts/coresim.py`：构建并驱动上述模型的 Python 模块。

## 第 0 步：Decaps-768 按指令周期分布

10 个 ACVP decapsulation 向量平均；完整表（含 KeyGen / Encaps 和每条指令）见
`reports/step0/profile_ucode.md`。

| 指令 | 条数 | Exec | Overhead | 合计 | 占比 | 字节 | 周期/字节 |
| ---- | ---: | ---: | -------: | ---: | ---: | ---: | --------: |
| HINIT | 18 | 18 | 54 | 72 | 0.1% | | |
| HABS | 20 | 5,280 | 60 | 5,340 | 4.8% | 1,696 | 3.11 |
| HABI | 16 | 25 | 48 | 73 | 0.1% | | |
| HFIN | 18 | 18 | 54 | 72 | 0.1% | | |
| HSQZ | 2 | 142 | 6 | 148 | 0.1% | 96 | 1.48 |
| COPY | 0 | — | — | — | — | | |
| CMP | 1 | 3,264 | 3 | 3,267 | 3.0% | 1,088 | 3.00 |
| CSEL | 1 | 64 | 3 | 67 | 0.1% | 32 | 2.00 |
| SAMPLE | 9 | 7,794 | 27 | 7,821 | 7.1% | | |
| CBD | 7 | 2,863 | 21 | 2,884 | 2.6% | | |
| DECODE | 11 | 10,499 | 33 | 10,532 | 9.5% | 3,424 | 3.07 |
| ENCODE | 5 | 6,250 | 15 | 6,265 | 5.7% | | |
| NTT | 6 | 21,516 | 18 | 21,534 | 19.5% | | |
| INTT | 5 | 23,050 | 15 | 23,065 | 20.9% | | |
| BMUL | 15 | 26,270 | 45 | 26,315 | 23.8% | | |
| ADD | 5 | 2,570 | 15 | 2,585 | 2.3% | | |
| SUB | 1 | 514 | 3 | 517 | 0.5% | | |
| END + 启动 | 2 | 0 | 3 | 3 | 0.0% | | |
| **合计** | 142 | 110,137 | 423 | **110,560** | 100% | | |

单条指令：NTT 3,586、INTT 4,610、BMUL 1,666（覆盖）/ 1,794（累加）、
SAMPLE ≈ 866、CBD2 409、DECODE d=12（384 B）1,155、d=10（320 B）963、
ENCODE d=10 1,346、ENCODE d=1（只出 32 B）1,058、CMP（1,088 B）3,264。
DECODE 的字节数由 d 推出（32·d 字节）。

### 观察（影响第 3 步的优先级）

1. **NTT / INTT / BMUL 占 64%（70,914 周期）**，远大于数据搬运。`mlkem_poly_alu`
   不是流水的：每对字走 S_ISSUE → S_LATCH → S_EXEC（4 级乘法器）→ S_WRITE，
   约 8 周期处理 2 个蝶形，所以一层 NTT 需要 64 × 8 = 512 周期，7 层 3,584。
   双口 RAM 每周期 2 次访问时的下限是每层 128 周期（每周期 1 个蝶形），
   即 NTT ≈ 900、INTT ≈ 1,000、BMUL ≈ 300。仅这一项就能省约 55k 周期，
   而且只需要 2 个 `a*b` 乘法器，和第 2 步（2 DSP）兼容。
2. 3a（缓冲区流水读）能动的是 HABS 5,280 + CMP 3,264 + DECODE 10,499 ≈ 19k，
   降到约 1 周期/字节后约省 12k（≈ 11%）。DECODE 实测约 3.0 周期/字节（d=12、d=10 两种都是 3.01），与预期一致。
3. 3b（sponge 按 lane）主要收益在 SAMPLE（7.8k，每次约 866 周期，目前每周期挤出 1 字节）。
   CBD 受多项式 RAM 每周期写 1 字所限，收益很小；HSQZ 只有 148。预计省约 5k。
4. ENCODE（6,250）不在原计划中：pack 每系数约 4 周期，与 d 无关
   （d=1 只出 32 B 也要 1,058 周期）。
5. 指令取指开销（每条 3 周期）共 423，可忽略。

粗略估计：只做 3a + 3b，Decaps ≈ 93k；再把 ALU 改成流水（建议增加的第 3d 步）≈ 38k；
加上 pack 提速 ≈ 33k。要接近 Xing & Li 的约 10k，还需要 3c 的 Keccak/ALU 并行。

## 与任务描述的差异（第 0 步发现）

1. **工作区已经有一部分第 1a 步**（9/28 修改，原未提交，现为 bf2ce6d）：`gen_mlkem_ucode.py --levels`
   （默认只生成 768）、376 条指令的 ROM（`ADDR_W` 默认 9）、`mlkem_ctrl` 和
   `pqc_mlkem_top` 的 `FIXED_LEVEL = 768` 参数（非 768 返回错误码 1）。
   你给的基线（BRAM 9、LUT 10,212、WNS +0.028）来自 9/26 的构建，当时 ROM 还是
   1,164 条（512/768/1024），综合为 `2048x51` Block RAM。
2. 在 bf2ce6d 上，**Vivado 已经自动把 ROM 放进 LUT**（`u_rom` 387 LUT + 46 FF，
   0 BRAM，尚未加 `rom_style`），所以第 0 步 BRAM = 6，不是 9。`mlkem_ctrl` 仍以
   `ADDR_W = 11` 例化 ROM，`pc` 也是 11 位。
3. **第 1 步遗漏了一块 BRAM**：`u_alu` 里的 zeta ROM（`zeta_r_reg`，128×12，
   寄存输出）被推断为 1 个 RAMB18。当前 6 = dbuf 4 + polyram 1.5 + zeta 0.5。
   若不处理，1b、1c 之后是 3.5，而不是 3。需要对它加 `rom_style = "distributed"`
   （约 20 LUT）。
4. `mlkem_pack` 的 `RECIP` 在源码中是 161271，与描述一致；Vivado 把它拆成 DSP
   部分（`0x75f7`）加 fabric 加法器。
5. 5 ns 下的关键路径是 `u_pack`（`half_reg` → DSP 乘 RECIP → `val_reg`，9.48 ns，
   9 级逻辑）；10 ns 下的关键路径是 `u_alu b1_reg` → `msub` → `u_mul1` 的 DSP A 端口
   （6.09 ns）。第 2 步把 pack 的常数乘法改成移位加法时，这条路径可能需要插一级寄存器。
6. ACVP 之前只能上板通过 UART 运行；第 0 步新增了仿真中的 ACVP 运行器。
   cocotb 不在系统 Python 里（我在临时 venv 中装了 cocotb 2.1.0 来跑）：
   `testbench/ntt`（Barrett）在 Icarus 下 2/2 通过，`testbench/keccak` 在 Verilator 下 4/4 通过；
   keccak 测试在 Icarus 下编译失败（原本如此）。

## 第 1a 步：微码 ROM 只保留 768、放进 LUT；zeta ROM 放进 LUT

- `gen_mlkem_ucode.py`：只保留 768 的参数和程序（376 条），`--levels` 只接受 768；
  ROM 去掉 `level_idx` / `level_mask` 端口，入口只按 op 选择，`ADDR_W = 9`，
  输出寄存器加 `(* rom_style = "distributed" *)`。生成器断言指令数 < 511
  （pc 全 1 是控制器的"装入入口"标记）。
- `mlkem_ctrl`：`pc` 11 → 9 位，去掉 `FIXED_LEVEL` 参数和等级译码，
  `lvl_ok = (sec_level == 768)`，其余等级仍返回错误码 1。
- `pqc_mlkem_top` / `arty_a7_top`：去掉 `FIXED_LEVEL`，输出长度改为 768 常数。
- `mlkem_poly_alu`：`zeta_r` 加 `rom_style = "distributed"`（第 0 步里它是 1 个 RAMB18）。
- 结果：ACVP 60/60，全部回归通过；周期数不变（KeyGen 65,821 / Encaps 78,484 /
  Decaps 110,560）。推断 BRAM：dbuf 4 + polyram 1.5 = 5.5。

## 第 1b 步：多项式 RAM 2048 → 1024 字

- 槽位改为紧凑编号 `S_VEC, S_ACC, S_A, S_E, S_T, S_S = 0, 3, 4, 5, 6, 7`（共 8 个槽）；
  生成器对每条多项式指令断言槽号 < 8，并断言 k 向量不覆盖 S_ACC。
  确认过：除生成器外没有任何模块写死槽号 ≥ 8（`tb_ntt` 只用槽 0、1、4、5、6）。
- `mlkem_polyram`：`mem [0:1023]`，地址 10 位（1K×24，一个 RAMB36 的 1K×36 配置）。
- 槽号 `[3:0]` → `[2:0]`、多项式 RAM 地址 `[10:0]` → `[9:0]`：`mlkem_ctrl`、
  `mlkem_poly_alu`、`mlkem_unpack`、`mlkem_pack`、`pqc_mlkem_top`、`testbench/iverilog/tb_ntt.sv`。
- 结果：ACVP 60/60，全部回归通过；周期数不变。推断 BRAM：dbuf 4 + polyram 1 = 5。

## 第 1c 步：数据缓冲 16 KB → 8 KB

新布局（采用了建议的布局，生成器核对后定稿）：

| 区域 | 偏移 | 大小 | 实际最大使用 |
| ---- | ---- | ---: | ----------: |
| 输入 IN | 0x0000–0x0DFF | 3,584 B | 3,488 B（Decaps：dk ‖ c，到 0xD9F） |
| 输出 OUT | 0x0E00–0x1BFF | 3,584 B | 3,584 B（KeyGen：ek ‖ dk，正好用满）；Decaps 的 c′ 在 OUT+0x100（1,088 B） |
| 暂存 SCR | 0x1C00–0x1CFF | 256 B | SEEDS 0x1C00（64）、HEK 0x1C40、MPRIME 0x1C60、KBAR 0x1C80（各 32），到 0x1C9F |
| 未用 | 0x1D00–0x1FFF | 768 B | |

- `gen_mlkem_ucode.py`：`IN_BASE/OUT_BASE/SCR` 及区域大小常量，`CPRIME` → `OUT(0x100)`。
  每条访问缓冲区的指令（HABS/HSQZ/COPY/CMP/CSEL/DECODE/ENCODE）都检查访问范围是否在
  自己的区域（IN / OUT / 暂存）内，越界则生成失败。已用负向测试确认：把 OUT、IN、SCR
  各缩小一点，断言都会在对应指令处报错。
- `pqc_data_buffer`：`DEPTH 2048`、`ADDR_WIDTH 11`；`pqc_mlkem_top`：`BUF_AW 11`、
  `DEFAULT_OUT_ADDR = 0x0E00`；`mlkem_ctrl`：`buf_addr` 12 → 11 位（`ptr[12:2]`）。
- 协议不变：CSR 数据窗口仍是 0x4000–0x7FFF，8 KB 缓冲在 0x6000–0x7FFF 镜像；
  `uart_axi_bridge` 未改。
- 主机端同步：`cmd/pqc-testkit/cmd/fpga.go`（`mlkemOutOffset`，`sca` 命令也用它）、
  `scripts/uart_test.py`（`OUT_BASE`）、`docs/UART_TEST_GUIDE.md`（寄存器示例与布局表）、
  `docs/SIM_TVLA_GUIDE.md`、`arty_a7_top.sv` 注释。`tb_core` 从 CSR 复位值读取偏移，无需修改。
- 注意：和以前一样，整个缓冲区（包括暂存区、m′、K′、K̄ 和现在的 c′）都能被主机通过数据窗口读到。
  对未防护的研究基线没有影响，但它不是一个可以部署的 KEM 实现。
- 结果：ACVP 60/60，全部回归通过；周期数不变。推断 BRAM：dbuf 2 + polyram 1 = **3**。

## 第 2 步：DSP 8 → 2

只有 `u_mul0` / `u_mul1` 的 `a * b`（变量 × 变量）还用 DSP，常数乘法都改为 CSD 移位加减，
并加 `(* use_dsp = "no" *)`。没有 Vivado / yosys，DSP 数由 RTL 推断：除这两处外，
`u_mlkem` 用到的模块里已没有任何 `*` 运算。

| 位置 | 原来 | 现在 |
| ---- | ---- | ---- |
| `mlkem_modmul` ×2 | `p1 * 5039` | `(p1<<12) + (p1<<10) − (p1<<6) − (p1<<4) − p1`（只用 [36:24]） |
| `mlkem_modmul` ×2 | `qh2 * 3329` | `(qh<<11) + (qh<<10) + (qh<<8) + qh`，只算低 13 位 |
| `mlkem_pack` | `((x<<d) + 1664) * 161271` | `((x·161271) << d) + 1664·161271`；x·161271 = `(x<<17)+(x<<15)−(x<<11)−(x<<9)−(x<<3)−x`，在 S_LATCH 对一个字的两个系数同时算好并寄存（`xr0`/`xr1`） |
| `mlkem_unpack` | `field * 3329` | `(f<<11) + (f<<10) + (f<<8) + f` |

- `mlkem_modmul` 的流水延迟保持 4 级，ALU 的对齐逻辑不变；顺带把 `p2` 缩到实际用到的 13 位。
- pack 的 x·R 放到 S_LATCH 预先计算，使可变移位不在加法树里，状态机和周期数不变。
  第 0 步 5 ns 下的关键路径就是 pack 的这条乘法路径；现在它被拆成两个寄存器段，但没有 Vivado，
  100 MHz 时序未经验证。
- 新增单元测试 `testbench/units/`（Icarus，`make -C testbench/units`，已加入 `make regress`）：
  - `tb_modmul`：a, b ∈ [0, q) 穷举 11,082,241 对，与 `(a*b) % q` 比对，含 4 级延迟检查，全部正确（约 86 s）。
  - `tb_pack`：x ∈ [0, q) 全覆盖（14 个多项式），d ∈ {1, 4, 5, 10, 11, 12}，与 Python 按 FIPS 203 定义
    （精确有理数舍入，不依赖 161271 技巧）算出的 ByteEncode_d(Compress_d) 逐字节比对：19,264 字节全对。
  - `tb_unpack`：DECODE 模式，每个 d 覆盖全部 2^d 个字段值，字节源随机停顿；与
    Decompress_d(ByteDecode_d) 比对，d=12 还核对模数检查的 range_err 次数：8,704 个系数全对。
  - 变异测试：把 pack / unpack 的 CSD 去掉一项，测试分别报 43 / 2,031 处错误。
- 结果：ACVP 60/60，全部回归通过；周期数不变。推断 DSP = **2**。
