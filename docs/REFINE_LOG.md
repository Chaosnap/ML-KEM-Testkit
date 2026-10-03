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
| 3a | 未跑 | | | 2（推断） | 3（推断） | | 100,242 | 未跑 | 60/60 | | |
| 3b（你跑的 Vivado，含 UART 桥） | 10,287 | 5,459 | | 2 | 3 | | 86,494 | **−2.061 ns** | 60/60 | | |
| 3b（`u_mlkem`） | 8,654 | 2,921 | | 2 | 3 | | 86,494 | −2.061 ns | 60/60 | | |
| 3d | 未跑 | | | 2（推断） | 3（推断） | | 29,363 | 未跑 | 60/60 | | |
| 时序修复 | 未跑 | | | 2（推断） | 3（推断） | | 29,826 | 未跑 | 60/60 | | |
| 3e | 未跑 | | | 2（推断） | 3（推断） | | **24,886** | 未跑 | 60/60 | | |

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

## 第 3a 步：缓冲区按字流水访问（≥ 1 字节/周期）

- 生成器断言 HABS / HSQZ / COPY / CMP / CSEL / DECODE 的缓冲区地址和长度都是 4 的倍数
  （当前程序全部满足），DECODE 的 len 字段填入 32·d 字节。
- `mlkem_ctrl` 按 32 位字访问端口 B：
  - HABS、DECODE：2 项字 FIFO 预取，每周期发一次读（BRAM 读延迟 1 周期）；只要已缓存加在途的字
    留有空位就继续发读，消费者反压（置换期间 `absorb_ready = 0`）时自动停住。字节仍以 1 字节/周期
    交给 sponge / unpack（3b 再加宽）。
  - CMP：a、b 两路读交错，每字 2 周期（0.5 周期/字节）；CSEL、COPY：读一字、写一字，每字 2 周期。
  - 删除旧的 `C_RD / C_RD_WAIT / C_ABS / C_CMP_B` 和 DECODE 单字节保持寄存器。
- 新增 `seq_overhead` 信号（只供 `tb_core` 统计用，综合时被删除），profiler 不再依赖状态编码。
- DATA_IN_ADDR / DATA_OUT_ADDR 现在必须是 4 的倍数（已写进 CSR 注释和 UART 指南）。

Decaps-768 按指令（第 2 步 → 第 3a 步）：

| 指令 | 第 2 步 | 第 3a 步 | 周期/字节 |
| ---- | ------: | -------: | --------- |
| HABS | 5,340 | 2,004 | 3.11 → 1.15（剩余部分是 sponge 1 字节/周期和置换） |
| CMP | 3,267 | 548 | 3.00 → 0.50 |
| CSEL | 67 | 19 | 2.00 → 0.50 |
| DECODE | 10,532 | 6,317 | 3.01 → 1.80（现在受 unpack 每周期只取 1 字节或出 1 个字段限制） |
| 合计 | 110,560 | **100,242** | |

KeyGen 65,821 → 60,607，Encaps 78,484 → 73,437（见 `reports/step3a/profile_ucode.md`）。
ACVP 60/60（decaps 的 10 个向量里 5 个有效、5 个修改过的密文，两条路径都覆盖），全部回归通过。

## 第 3b 步：Keccak 按 64 位 lane（8 字节/周期），单份状态

- 新增 `keccak_round.sv`（一轮置换，纯组合，函数实现）；`keccak_f1600` 改为调用它，接口和
  24 周期时序不变（cocotb 4/4 仍通过）。核心不再例化 `keccak_f1600`。
- `keccak_sponge` 重写：
  - 只保留一份 1600 位状态，置换时直接在 `st` 上每周期迭代一轮，去掉了原来 sponge 与
    f1600 之间的两份状态和 1600 位拷贝，也省掉了启动/回写的 3 个周期。
  - 吸收：每次最多 8 字节（`absorb_data` / `absorb_n`），要求不跨 lane；挤出：给出当前 lane 剩余字节
    （`squeeze_data` / `squeeze_avail`），消费者取 `squeeze_n` 个。
  - 一个 SHAKE128 块整 lane 吸收：21 + 24 = **45 周期**（原来 168 + 24 + 启动开销）。
- `mlkem_unpack` 加宽：源接口按块（≤ 8 字节）给出，每周期最多取 2 个字段、每周期写 1 个 RAM 字；
  SAMPLE 拒绝采样时奇数个系数在 `lo` 中等待配对；`range_err` 改为 2 位（每个字段一位）。
  SAMPLE / DECODE d=12 最多 3 字节/周期，CBD η=2 1 字节/周期（受每周期写 1 字限制）。
- `mlkem_ctrl`：HABI 一次吸收 1–2 个立即数字节；HABS、DECODE 每周期吃一个 FIFO 字（4 字节）；
  HSQZ 每周期写一个字；SAMPLE / CBD 直接从 sponge 按 lane 取块。
- 生成器静态跟踪每次哈希的吸收 / 挤出位置，断言：HABS、HSQZ 起点 4 字节对齐，HABI 不跨 lane，
  SAMPLE / CBD 从新挤出的位置 0 开始。当前程序全部满足，ROM 内容未变。
- 新测试：
  - `testbench/sponge`（Icarus，已加入 `make regress`）：SHA3-256/512、SHAKE128/256 共 104 个向量，
    消息长度覆盖 0、1、7、8、刚好一块、块长 ±1、跨多块和非 8 字节对齐，输出长度覆盖 1 到 3 块以上；
    吸收和挤出都用随机块大小和随机停顿，与 Python `hashlib` 逐字节比对（11,793 字节全对），并检查
    一个 SHAKE128 块正好 45 周期。变异测试（SHAKE128 用错域后缀）报 4,940 处错误。
  - `testbench/units/tb_unpack` 改为块接口（随机 1–8 字节、随机停顿），新增 SAMPLE（FIPS 203 Alg. 7，
    8 个随机串）和 CBD η = 2、3（Alg. 8），共 12,800 个系数全对；DECODE / CBD 还检查源字节被恰好用完。
    变异测试（配对逻辑选错系数）报 1,773 处错误。

Decaps-768 按指令（第 3a 步 → 第 3b 步）：

| 指令 | 第 0 步 | 第 3a 步 | 第 3b 步 | 说明 |
| ---- | ------: | -------: | -------: | ---- |
| HABS | 5,340 | 2,004 | 716 | 0.39 周期/字节（缓冲区 4 字节/周期 + 置换） |
| HABI | 73 | 73 | 64 | 每条 1 周期 |
| HSQZ | 148 | 148 | 72 | |
| CMP | 3,267 | 548 | 548 | |
| SAMPLE | 7,821 | 7,821 | 2,066 | 每条约 866 → 227 |
| CBD | 2,884 | 2,884 | 1,085 | 每条 409 → 152 |
| DECODE | 10,532 | 6,317 | 1,496 | d=12（384 B）1,155 → 133 |
| ENCODE | 6,265 | 6,265 | 6,265 | 未改 |
| NTT + INTT + BMUL | 70,914 | 70,914 | 70,914 | 未改，现占 82% |
| **合计** | **110,560** | **100,242** | **86,494** | −21.8% |

KeyGen 65,821 → 51,952，Encaps 78,484 → 62,783。ACVP 60/60，全部回归通过。

## 第 3c 步（只评估，未实现）：Keccak 与 ALU 并行

当前 Decaps 中 Keccak 相关指令（HINIT/HABS/HABI/HFIN/HSQZ/SAMPLE/CBD）合计约 **4.1k 周期（4.8%）**，
所以在现有 ALU 下，即使与 ALU 完全重叠，最多也只能省约 4k 周期。主要瓶颈已经是 ALU：

| 项目 | 现在 | 估计（流水化后） |
| ---- | ---: | ---------------: |
| NTT ×6 | 21,534 | 每层 128 周期（每周期 1 个蝶形）+ 流水填充 ≈ 6 × 920 ≈ 5.5k |
| INTT ×5 | 23,065 | 同上，加最后一遍 ×128⁻¹ ≈ 5 × 1,050 ≈ 5.3k |
| BMUL ×15 | 26,315 | 每字 4 次乘法 / 2 个乘法器，累加时 3 次读 / 2 个端口 ≈ 15 × 300 ≈ 4.5k |
| ADD/SUB ×6 | 3,102 | 每字 1 周期 ≈ 6 × 135 ≈ 0.8k |
| ENCODE ×5 | 6,265 | pack 改为每周期 1 个系数 ≈ 5 × 270 ≈ 1.4k |

估计 Decaps：86.5k − (73.0k − 16.1k) − (6.3k − 1.4k) ≈ **25k**，DSP 仍为 2（只用两个 `a*b`）。
建议把它作为下一步（3d：ALU 流水化），收益约为 3c 的 10 倍以上。

在此基础上再做 3c 需要的改动：
1. **微码并发**：增加"后台启动"（SAMPLE / CBD 发出后不等待）和"等待单元完成"的指令，或在控制器里
   加一个记分板，使 Keccak/unpack 与 ALU 同时运行，并保证对同一槽位的读写顺序。
2. **多项式 RAM 端口**：现在 unpack 只用端口 A，ALU 同时占用 A、B 两个端口。并行时需要第三个端口，
   可选做法是把 S_A（以及 S_E）放到单独的小存储体里（128×24 分布式 RAM，约 50–100 LUT，不增加
   BRAM），或者给 A 做双缓冲（S_A0 / S_A1：SAMPLE 写一个槽，BMUL 读另一个槽）。
3. **生成器调度**：按 Xing & Li 的"A 元素即时生成、即时使用"把 SAMPLE(i, j+1) 与 BMUL(i, j)
   交错排布，PRF/CBD 与 NTT 交错排布。
4. 预计节省：在 ALU 流水化之后，Decaps 中 SAMPLE（≈ 2.1k）、CBD（≈ 1.1k）以及大部分 HABS 可以被
   ALU 时间掩盖，约 3–4k 周期，Decaps ≈ 21–22k。要接近 Xing & Li 的约 10k，还需要每周期 2 个蝶形
   （即 2 个并行蝶形单元、4 个乘法器，DSP 会超过 2），或者更激进的 NTT/BMUL 融合。

## 第 3b 步的 Vivado 结果（你运行的）

资源：`u_mlkem` LUT 8,654、FF 2,921、BRAM 3、DSP 2（第 0 步为 10,212 / 4,205 / 9 / 8），
其中 `u_sponge` 5,630 LUT / 1,644 FF（原 8,578 / 3,305）。
时序：WNS −2.061 ns、TNS −156.9 ns（482 个端点）。报告里每组只有最差的一条路径：
- setup：`u_ctrl/unit_src_sponge` → `u_unpack/bitbuf[78]`，12 级逻辑（7 个 CARRY4），12.0 ns。
  sponge 的 lane 选择和字节移位、unpack 拼接用的可变移位、字段提取的移位和比较串在一条组合路径上。
- recovery（异步复位）：`rst_sync` → `u_sponge/st_reg[808]/CLR`，−0.262 ns，布线 9.3 ns，
  原因是复位扇出到 1,600 个状态寄存器。

## 第 3d 步：ALU 流水化

- `mlkem_modmul` 改为 5 级（a·b 用 DSP 的 M、P 两级寄存器；×5039 拆成正、负两组 CSD 项放在第 3 级，
  相减和 ×3329 放在第 4 级，第 5 级做条件减），同时为下面的调度提供奇数延迟。单元测试改为 5 级延迟，
  q² 对输入仍然全部正确。
- `mlkem_poly_alu` 重写为固定调度的流水线（接口不变），T 为一个字（或字对）的发出周期：
  - NTT / INTT：每 2 周期一个字对，T（偶数周期）两个端口读，T+2 进乘法器，T+9（奇数周期）两个端口写；
    即每周期一个蝶形。层与层之间排空流水线（每层约 9 周期）。INTT 的 ×128⁻¹ 一遍为每周期一个字
    （端口 A 读、端口 B 写）。
  - BMUL：每 2 周期一个字，Karatsuba 4 次乘法：T+2 a0·b0、a1·b1，T+3 (a0+a1)(b0+b1)，
    T+7 (a1·b1)·γ；累加时 T+1 用端口 A 读 C，T+13 用端口 B 写 C。
  - ADD / SUB：每 2 周期一个字，T+3 写。
  - 指令开始时清空发出流水线，避免上一条指令的残留有效位造成误写。
- 生成器新增断言：BMUL 的目的槽不能与源槽相同。
- 单条指令周期：NTT 3,586 → 953，INTT 4,610 → 1,090，BMUL 1,666/1,794 → 270，ADD/SUB 514 → 260。

Decaps-768 按指令（第 3b 步 → 第 3d 步）：

| 指令 | 第 3b 步 | 第 3d 步 |
| ---- | -------: | -------: |
| NTT ×6 | 21,534 | 5,742 |
| INTT ×5 | 23,065 | 5,470 |
| BMUL ×15 | 26,315 | 4,095 |
| ADD/SUB ×6 | 3,102 | 1,578 |
| ENCODE ×5 | 6,265 | 6,265（现占 21%） |
| 其余 | 6,213 | 6,213 |
| **合计** | **86,494** | **29,363** |

KeyGen 51,952 → 22,066，Encaps 62,783 → 21,765。ACVP 60/60，全部回归通过。

## 时序修复（针对第 3b 步的 −2.061 ns）

- `mlkem_unpack` 全部寄存化，分为 S0 源块寄存器 → S1 位缓冲 → S2 q·字段 → S3 系数 → S4 配对 → S5 写 RAM：
  - 源块先打一拍（`cr_*`），sponge 的 lane 选择和字节移位不再与 unpack 的移位串在一起；
  - 是否拼接新块只看寄存器 `nbits <= 32`（位缓冲 96 位），不再依赖本周期取出字段后的减法；
  - 位缓冲的 `>>w`、`>>2w` 只依赖寄存器 `w`，与选择信号 `k` 并行，`k` 只做最后的三选一；
  - Decompress 拆成两级：S2 算 3329·y（移位加），S3 做舍入加和可变右移；
  - RAM 写口（`wr_en/wr_addr/wr_data`）改为寄存器输出。
- `keccak_sponge`：1,600 位状态 `st` 移出异步复位（HINIT 在每次使用前清零），消除复位网络的
  recovery 违例，也减少复位布线。控制寄存器仍带复位。
- 代价：Decaps 29,363 → 29,826 周期（每条 SAMPLE/CBD/DECODE 多几个周期的流水深度）。
- `vivado_build.tcl` / `vivado_reports.tcl` 额外输出 30 条端点不同的最差路径
  （`timing_paths_impl.rpt` / `timing_paths.rpt`），下次可以看到不止一条违例路径。
- 没有 Vivado，不能确认修复后的 WNS。按逻辑级数逐条检查过本轮新增的路径（详见 commit 说明），
  请你再跑一次。

## 第 3e 步：pack 每周期一个系数、按字输出

- `mlkem_pack` 改为固定流水：每周期发出一个系数（偶数系数时读一个 RAM 字），t+1 先把 RAM 字打一拍
  （原来 BRAM 输出直接进 ×161271 的 6 项加法树，是一条有风险的路径），t+2 算 x·161271，
  t+3 Compress，t+4 拼位；凑满 32 位就输出一个字（256·d 是 32 的倍数，最后正好对齐）。
- `mlkem_ctrl`：ENCODE 每次写一个整字（`pk_out_word`，`ptr_b += 4`），生成器已断言 ENCODE 目的地址按字对齐。
- `tb_pack` 改为按字收集，仍与 Python 参考逐字节比对：19,264 字节全对。
- ENCODE：d=10 1,346 → 约 262 周期，d=1 1,058 → 约 262 周期；Decaps 中合计 6,265 → 1,325。

Decaps-768 29,826 → **24,886**，KeyGen 15,365，Encaps 17,919。ACVP 60/60，全部回归通过。
当前 Decaps 分布：NTT 23%、INTT 22%、BMUL 17%、SAMPLE 9%、DECODE 7%、ENCODE 5%、ADD 5%。

## 206 MHz（Vivado 由 `scripts/vivado_batch.sh` 在独立的临时容器中运行）

时钟结构：Arty 的 100 MHz 晶振经 MMCM（÷2 ×20.625 ÷5）产生 **206.25 MHz** 核心时钟；UART 和桥
仍在 100 MHz，两侧通过 `axil_cdc`（toggle 握手的 AXI-Lite 跨时钟域，与厂商无关）连接。
`testbench/cdc` 在两个异步时钟下跑寄存器读写、字节写使能以及完整的 ACVP KeyGen / Decaps。

| 运行 | 核心时钟 WNS | 违例端点 | 主要问题 |
| ---- | -----------: | -------: | -------- |
| f206_0（第 3e 步 + MMCM） | −4.993 ns | 5,094 | sponge ↔ 控制器 ↔ unpack 的组合握手；ALU 两级模加；异步复位扇出 |
| f206_1（ALU / pack / modmul 流水 + 同步复位） | −4.956 ns | 4,505 | sponge / unpack / 控制器未改；复位反相器扇出 |
| f206_2（全核流水化） | −0.622 ns | 318 | 串行的模加（加 → 比较 → 减）；unpack 队列判断；sponge 轮函数 |
| **f206_3**（并行模加 + 实现策略） | **+0.012 ns** | **0** | — |

f206_3：建立时间 WNS +0.012 ns，保持时间 WHS +0.034 ns，脉宽满足；100 MHz 一侧 WNS +1.175 ns。

主要改动（详见 commit 0e9e9df、9fbc95e）：
- 每级最多一个加法器或一次模加 / 模减；所有 RAM 端口命令提前一拍算好并寄存；多项式 RAM 和数据缓冲
  打开输出寄存器（读延迟 2）。
- modmul 7 级（DSP 的 A/B、M、P 寄存器 + 4 级 LUT）；ALU 重新排程（NTT/INTT 写在 T+15，BMUL 在
  T+21）；每种运算的结果各用一个寄存器，端口寄存器只做选择。
- 模加 / 模减改为两个候选并行计算（"−q" 的那个用进位保留的三输入加法），由一位选择。
- sponge：吸收命令先对齐、寄存再写入状态；挤出寄存输出，带两个预取 lane。
- unpack：64 位块进 3 槽队列，用位指针代替可变移位的位缓冲；队列判断用随指针一起寄存的比较标志。
- 控制器：端口 B 命令寄存，6 项读 FIFO，CMP / COPY / CSEL 流水化。
- 复位：核心内部全部改为同步复位，每个模块用自己寄存的高有效复位。
- 实现策略：opt Explore、place ExtraTimingOpt、phys_opt / route AggressiveExplore、布线后 phys_opt
  （`vivado_build.tcl` 已设置相同策略）。
- 余量只有 +0.012 ns：不同的工具版本、种子或工程模式与非工程模式可能得到略负的结果。如果你在 GUI
  里得到很小的负 slack，请把 `timing_paths_impl.rpt` 发给我。

### 与参考设计对比（ML-KEM-768，Artix-7）

| 设计 | LUT | FF | Slice | DSP | BRAM | ENS | Decaps 周期 | 频率 (MHz) | ATP (ENS·ms) |
| ---- | --: | -: | ----: | --: | ---: | --: | ----------: | ---------: | -----------: |
| Xing & Li，未防护 | 7,353 | 4,633 | 2,173 | 2 | 3 | 2,973 | 10.0k | 206 | 144.3 |
| Moraitis et al. | 14,341 | 9,190 | 4,734 | ≥2 | 6 | ≥6,134 | 10.0k | ≤206 | ≥297.8 |
| Xu et al. 2025，shuffling | 8,143 | 5,151 | 2,433 | 2 | 3 | 3,233 | 10.0k | 206 | 156.9 |
| 第 0 步基线（100 MHz） | 10,212 | 4,205 | 2,897 | 8 | 9 | 5,497 | 110.6k | 100 | 6,077 |
| **本设计（f206_3）** | **7,657** | **4,732** | **2,545** | **2** | **3** | **3,345** | **25.2k** | **206.25** | **409** |

- 资源（DSP 2、BRAM 3、LUT / FF / Slice）已与参考设计同一量级；ATP 的差距主要来自周期数（25.2k 对 10.0k）。
- 本设计用的是 xc7a100t**csg324-1**（−1 速度等级）。请核对参考论文所用的器件和速度等级，在表中注明。
- 周期数（核心时钟 206.25 MHz，ACVP 向量平均）：KeyGen 15,605，Encaps 18,165，Decaps 25,219。
  Decaps 分布见 `reports/f206_3/profile_ucode.md`。要进一步接近 10k，需要 3c 的 Keccak / ALU 并行
  和每周期 2 个蝶形（见前文评估）。


## 2026-10-03 — 核心 206.00 MHz RTL 优化（未运行实现）

本次以 49a144c 为基线拆分 sponge 的 Keccak 轮函数、复用 Theta 暂存寄存器、用 banked LUTRAM 改写控制器读 FIFO，并收窄压缩通路及共享地址加法器。UART RTL 保持原样，核心 MMCM 输出调整为精确 206 MHz。

Icarus/Verilator 功能回归通过，包括 ACVP 60/60、额外 20 项有效性/参数检查、sponge、所有压缩位宽、FIFO、独立 Keccak 和 CDC。Decaps 平均周期由 25218.9 增至 26277.3（+4.20%）。保留未加功耗侧信道防护的实现及标准隐式拒绝功能。

**以上 f206_3 的 +0.012 ns 和资源数字是历史结果，不代表本次 RTL。** 按用户要求未运行 Vivado、未安装工具，因此本次物理时序及净资源变化未测量。完整修改、权衡及复现方法见 [CORE_206MHZ_REVIEW.md](CORE_206MHZ_REVIEW.md)，验证日志见 `reports/core206_rtl/`。


## 2026-10-03 — Docker Vivado 第二轮实现交付

用户授权使用现有 vivado-container，Vivado 2024.1，未修改镜像；两轮实现串行运行，sys_clk 约束均为 10 ns，派生核心时钟 206 MHz。截图对应 WNS −0.068 ns / 9 个失败端点，路径位于解包器和 FIFO 读出的比较逻辑。

解包字段提取增加字节选择寄存级，密文比较改为四段寄存比较后归并；第一轮 WNS +0.002 ns、WHS +0.014 ns。第二轮将 sponge 预取索引改为寄存 one-hot，最终 WNS +0.093 ns、WHS +0.036 ns、WPWS +1.177 ns，所有失败端点为 0，路由错误为 0。核心 LUT 7248、FF 4623、RAMB36 3、DSP 2。

ACVP 60/60、额外 20 项检查、16 项隐式拒绝边界检查、解包 15872 个系数及已有 sponge/pack/FIFO/Keccak/CDC 测试通过。UART RTL 与最初基线一致；无新增侧信道防护。平均核心周期为 KeyGen 16653.08、Encaps 19245.48、Decaps 26304.30。按用户最新要求交付第二轮代码，不再继续优化。

详见 [CORE_206MHZ_ROUND2.md](CORE_206MHZ_ROUND2.md)，最终检查点和报告位于 `reports/core206_debug2/`。上述“未运行实现”的段落是较早阶段记录。
