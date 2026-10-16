# ACVP 功能测试：上板（UART）与 RTL 仿真

`scripts/test/acvp_test.py` 用 NIST ACVP-Server 的 ML-KEM 官方向量（`acvp/` 下的 `internalProjection.json`，带有 NIST 给出的正确答案）检查 ML-KEM-768 核心是否符合 FIPS 203。

同一套向量、同一套判定规则可以跑在两个目标上：

| 目标 | 命令 | 用途 |
| ---- | ---- | ---- |
| `uart` | `uv run python scripts/test/acvp_test.py uart -p <串口>` | **上板测试**：通过 Arty A7 的 USB-UART 驱动 FPGA 上的核心 |
| `sim` | `uv run python scripts/test/acvp_test.py sim` | RTL 仿真（Verilator），不需要板子；改 RTL 后先在这里确认 |

结果写到 `data/acvp/<日期_时间>_<目标>/`，主要看其中的 `summary.md`。

---

## 1. 测试内容

| 组（`--groups`） | 数量 | 输入 → 输出 | 判定 |
| ---------------- | ---: | ----------- | ---- |
| `keygen` keyGen AFT | 25 | d ‖ z → ek ‖ dk | 必须与 NIST 的 (ek, dk) 逐字节相同 |
| `encaps` encapsulation AFT | 25 | ek ‖ m → c ‖ K | 必须与 NIST 的 (c, k) 逐字节相同 |
| `decaps` decapsulation VAL | 10 | dk ‖ c → K | 必须与 NIST 的 k 相同；5 个是合法密文，5 个是被修改过的密文（隐式拒绝） |
| `ekcheck` encapsulationKeyCheck VAL | 10 | Encaps(ek) | NIST 判定 ek 非法时，核心必须报 ERROR_CODE 2（模数检查失败）；合法时必须成功 |
| `errors` 参数检查 | 10 | SEC_LEVEL = 512 / 1024 / 999，OP_MODE = 3 | 必须报 ERROR_CODE 1 |

- **必测向量**是 keygen + encaps + decaps，共 60 个，对应 FIPS 140-3 实验室用的 ACVP 测试。
- 不测的部分：ML-KEM-512 / 1024 的向量（核心只实现 768），以及 decapsulationKeyCheck（核心不做 dk 的哈希检查）。报告里会列出跳过的数量。
- 不加 `--groups` 时五组全跑，共 80 个用例。

---

## 2. 准备

### 2.1 Python 环境
项目用 [uv](https://docs.astral.sh/uv/) 管理 Python 依赖（`pyproject.toml`：pyserial、matplotlib、numpy）。第一次使用时：

```bash
cd ML-KEM-Testkit
uv sync                      # 创建 .venv 并安装依赖
```

之后都用 `uv run python scripts/...` 运行脚本，它会自动使用 `.venv`。不用 uv 的话，也可以 `python3 -m pip install pyserial` 后直接用 `python3` 运行。

### 2.2 仿真需要
Verilator ≥ 5.0（`verilator --version`）。`sim` 模式会自动执行 `make -C testbench/core_sim` 编译模型。

### 2.3 上板需要
- Digilent **Arty A7-100T**，一根 micro-USB 线（同时提供供电、JTAG 和 UART）。
- Vivado 2024.1（生成 bitstream、烧录）。
- 核心时钟 206.00 MHz 由板上 MMCM 产生；UART 和桥工作在 100 MHz，波特率 115200。

---

## 3. 上板测试步骤

### 3.1 先在仿真里确认 RTL
```bash
uv run python scripts/test/acvp_test.py sim
```
应看到 `required ACVP vectors: 60/60` 和 `ACVP PASS`。仿真不通过就不要上板，问题一定在 RTL。

### 3.2 生成 bitstream
在 Vivado 环境里（Docker 容器的终端或 Linux 主机），从仓库根目录运行：
```bash
vivado -mode batch -source scripts/vivado/build_arty_a7.tcl -tclargs arty-a7-100t mlkem
```
- 也可以在 Vivado GUI 的 Tcl Console 里执行 `source -notrace <仓库路径>/scripts/vivado/build_arty_a7.tcl`，不带参数时默认就是 `arty-a7-100t mlkem`。
- 输出：`build/vivado/arty-a7-100t_mlkem/pqc_mlkem.runs/impl_1/arty_a7_top.bit`
- **先确认时序收敛**：打开 `build/vivado/arty-a7-100t_mlkem/timing_impl.rpt`，`Design Timing Summary` 里的 WNS 和 WHS 都必须 ≥ 0。时序不收敛的 bitstream 在 206 MHz 下可能出现随机错误，ACVP 结果没有意义。

### 3.3 烧录
任选一种：
- Vivado GUI：Open Hardware Manager → Open Target → Auto Connect → Program Device，选上面的 `.bit`。
- 命令行：
  ```bash
  vivado -mode batch -source scripts/vivado/program_bitstream.tcl \
      -tclargs build/vivado/arty-a7-100t_mlkem/pqc_mlkem.runs/impl_1/arty_a7_top.bit
  ```

烧录后看板子上的 LED（LD4–LD7）：

| LED | 含义 | 正常状态 |
| --- | ---- | -------- |
| LD7 | 心跳（100 MHz 计数器，约 1.5 Hz 闪烁） | **一直在闪**：bitstream 在运行 |
| LD4 | busy：核心正在运算 | 平时灭，测试时一闪而过 |
| LD5 | done：上一次运算完成 | 测试开始后常亮 |
| LD6 | error：上一次运算报错 | 跑到 `ekcheck` / `errors` 组时会亮，这是预期的 |

BTN0 是复位键：按下会复位 UART 桥和核心，MMCM 也会重新锁定。

### 3.4 找到串口
```bash
uv run python scripts/test/acvp_test.py ports
```
Arty 上的 FTDI 芯片有两个接口：第一个是 JTAG，第二个才是 UART。
- macOS：`/dev/cu.usbserial-XXXXXXXX1`，名字以 **1** 结尾的那个
- Linux：通常是 `/dev/ttyUSB1`；需要当前用户在 `dialout` 组里
- Windows：设备管理器里编号较大的那个 COM 口

在 Docker 容器里跑的 Vivado 不占用串口，可以直接在 Mac 上运行本脚本。但要先关掉其他占用这个串口的程序（串口终端、旧的测试脚本等）。

### 3.5 运行
```bash
uv run python scripts/test/acvp_test.py uart -p /dev/cu.usbserial-XXXXXXXX1
```
脚本先做 7 项上板自检（见第 5 节），通过后依次运行 80 个用例。每个用例要通过 UART 传几 KB 数据：Decaps 写入 3488 字节，KeyGen 读出 3584 字节。115200 波特率下每个用例约 0.3–0.6 秒，全部大约 1 分钟。

只跑部分组：
```bash
uv run python scripts/test/acvp_test.py uart -p /dev/cu.usbserial-XXXXXXXX1 --groups keygen decaps
```

### 3.6 看结果
终端最后一行是结论和报告路径，例如：
```
required ACVP vectors: 60/60
ACVP PASS   report: data/acvp/20261006_150212_uart/summary.md
```
在 VS Code 里打开 `summary.md`（Markdown 预览：⇧⌘V）。退出码：0 = 全部通过，1 = 有用例失败，2 = 自检或环境出错。

---

## 4. 输出文件

`data/acvp/<YYYYmmdd_HHMMSS>_<uart|sim>/`

| 文件 | 内容 |
| ---- | ---- |
| `summary.md` | 报告：结论、测试环境（串口、git 版本、向量集 vsId）、上板自检结果、各组通过数、KeyGen / Encaps / Decaps 的周期统计（最少 / 平均 / 最多，以及 206 MHz 下的时间）、全部用例列表 |
| `results.csv` | 每个用例一行：group、tcId、结果、STATUS、ERROR_CODE、周期数、核心时间（µs）、UART 往返时间（s）、说明、ACVP 给出的 reason |
| `summary.json` | 同样的内容，便于用脚本比较多次运行 |
| `console.log` | 终端输出 |
| `failures/` | 只在有失败时生成：每个失败用例一个文件，包含输入、NIST 期望值和核心实际输出（十六进制） |

报告里的周期数来自核心的 `CYCLE_COUNT` 寄存器，按 206.00 MHz 核心时钟计数，与 UART 速度无关。仿真和上板的周期数应当完全一致；如果不一致，说明板上的 bitstream 和当前源码不是同一个版本。

---

## 5. 上板自检

`uart` 模式先按"从底层到上层"的顺序做 7 项检查。任何一项失败都会立即停止，并在 `summary.md` 里记录。

| 检查 | 验证的内容 | 失败时的常见原因 |
| ---- | ---------- | ---------------- |
| 1. link：读 ALG_ID | 串口、UART 收发、帧格式和 CRC 都通，读回 1（ML-KEM） | 串口选错（选了 JTAG 那个口）、板子没烧录、串口被别的程序占用、波特率不对 |
| 2. 核心 VERSION | CSR 能读 | 一般不会单独失败 |
| 3. CTRL.reset 清零 STATUS | 写寄存器和核心复位都有效 | 核心时钟 / MMCM 没锁定（看 LD7 是否在闪，试一下按 BTN0） |
| 4. 寄存器写 / 读 | SEC_LEVEL 写 999 再读回，然后恢复 768 | 跨时钟域（`axil_cdc`）异常 |
| 5. 错误 CRC 的帧被拒绝 | 桥会丢弃坏帧并回复状态 1，之后链路仍然同步 | 桥的版本和协议不匹配 |
| 6. 数据缓冲回环 | 1024 字节对齐写读 + 101 字节非对齐写读，并给出实测吞吐 | 数据缓冲或字节写使能有问题 |
| 7. 缓冲区偏移 | 打印 DATA_IN_ADDR / DATA_OUT_ADDR 的复位值（测试时会写成 0x0000 / 0x0E00） | 仅提示 |

`--no-preflight` 可以跳过自检，但不建议这样做。

---

## 6. 结果怎么读

- **全部通过**：bitstream 的功能符合 FIPS 203。核心输出与 NIST 逐字节一致，包括隐式拒绝路径、模数检查和参数检查。
- **某个用例 `output differs from NIST`**：说明里会写出第一个不同字节的位置，以及它落在哪一部分（ek / dk / c / K）。完整数据在 `failures/` 里。
  - 先跑 `sim` 模式。仿真也失败 → RTL 问题；仿真通过、上板失败 → 时序（检查 WNS / WHS）、bitstream 版本不对，或者跨时钟域问题。
  - 只在上板时偶发、每次失败的用例不同 → 几乎可以肯定是时序余量不足。
- **`core reported ERROR_CODE 1`**：SEC_LEVEL 或 OP_MODE 不对。本脚本每次都会写成 768，出现这个错误通常说明 bitstream 不是 768 版本。
- **`UART: timeout`**：见第 7 节。发生链路错误后，脚本会等待 0.2 秒并重新同步；如果仍然连不上，剩下的用例会标记为 `not run: link lost`。
- **Decaps 周期数在几十个周期范围内变化**是正常的：重加密时 SampleNTT 的拒绝采样次数取决于密文。

---

## 7. 常见问题

| 现象 | 处理 |
| ---- | ---- |
| `cannot open /dev/...` | 端口名写错，或者端口被占用。用 `ports` 子命令确认；关掉 screen / minicom / 其他脚本 |
| 自检第 1 项 `timeout: got 0 of 5 bytes` | 选成了 JTAG 那个口；或者 FPGA 没有烧录（LD7 不闪）；或者刚烧录完还没复位，按一下 BTN0 再试 |
| `response CRC mismatch` / `link out of sync` | 线路上有残留数据。脚本会自动重新同步；反复出现时，按 BTN0 并重新运行 |
| `operation did not finish within 5.0 s` | 核心卡住了（时钟 / 复位问题）。按 BTN0，确认 LD7 在闪，再运行一次；需要时用 `--op-timeout` 调整超时 |
| Linux 下 `Permission denied` | `sudo usermod -aG dialout $USER`，然后重新登录 |
| `pyserial is missing` | 用 `uv run python ...` 运行，或者 `python3 -m pip install pyserial` |

---

## 8. 命令行参数

```
uv run python scripts/test/acvp_test.py ports
uv run python scripts/test/acvp_test.py uart -p PORT [-b 115200] [--timeout 2] [--op-timeout 5]
                                        [--no-preflight] [--groups ...] [--vectors DIR] [--out DIR] [-v]
uv run python scripts/test/acvp_test.py sim  [--groups ...] [--vectors DIR] [--out DIR] [-v]
```

| 参数 | 默认 | 说明 |
| ---- | ---- | ---- |
| `-p, --port` | — | 串口（uart 必填） |
| `-b, --baud` | 115200 | 波特率，必须与 bitstream 一致（`arty_a7_top` 的 `BAUD_RATE`） |
| `--timeout` | 2 s | UART 单次读超时 |
| `--op-timeout` | 5 s | 单次 ML-KEM 运算等待 done 的最长时间 |
| `--no-preflight` | 关 | 跳过上板自检 |
| `--groups` | 全部 | `keygen encaps decaps ekcheck errors` 中的任意几个 |
| `--vectors` | `acvp/` | 包含 `ML-KEM-keyGen-FIPS203/` 和 `ML-KEM-encapDecap-FIPS203/` 的目录。从 ACVP-Server 下载新的 `internalProjection.json` 放进去即可换一套向量 |
| `--out` | `data/acvp` | 结果目录 |
| `-v` | 关 | 通过的用例也打印说明 |

---

## 9. 参考：主机协议

脚本内置的协议与 `hdl/core/uart_axi_bridge.sv` 一致（完整说明见 [`UART_PROTOCOL.md`](UART_PROTOCOL.md)），字段都是小端：

```
主机 → FPGA  [CMD:1][ADDR:4][LEN:4][DATA:LEN][CRC32:4]
FPGA → 主机  [STATUS:1][LEN:4][DATA:LEN][CRC32:4]
CMD: 0x01 读寄存器  0x02 写寄存器  0x03 写 buffer（每帧最多 256 字节）  0x04 读 buffer（DATA = 4 字节长度）
```

| 寄存器 | 偏移 | 说明 |
| ------ | ---- | ---- |
| CTRL | 0x00 | bit0 start，bit1 reset |
| STATUS | 0x04 | bit0 busy，bit1 done，bit2 error |
| ALG_ID | 0x08 | 1 = ML-KEM |
| SEC_LEVEL | 0x0C | 必须为 768 |
| OP_MODE | 0x10 | 0 KeyGen，1 Encaps，2 Decaps |
| CYCLE_COUNT | 0x14 | 上次运算的核心周期数（206 MHz） |
| VERSION | 0x18 | 核心版本 |
| ERROR_CODE | 0x1C | 1 参数错误，2 ek 模数检查失败，3 非法微码 |
| DATA_IN_ADDR / DATA_IN_LEN | 0x20 / 0x24 | 输入在 buffer 中的偏移（0x0000）/ 长度 |
| DATA_OUT_ADDR / DATA_OUT_LEN | 0x28 / 0x2C | 输出偏移（0x0E00）/ 长度 |

数据缓冲共 8 KB：输入区 0x0000–0x0DFF，输出区 0x0E00–0x1BFF，核心暂存区 0x1C00–0x1CFF。

| 运算 | 输入（写到 DATA_IN_ADDR） | 输出（从 DATA_OUT_ADDR 读） |
| ---- | ------------------------- | --------------------------- |
| KeyGen | d ‖ z（64 B） | ek ‖ dk（1184 + 2400 B） |
| Encaps | ek ‖ m（1184 + 32 B） | c ‖ K（1088 + 32 B） |
| Decaps | dk ‖ c（2400 + 1088 B） | K（32 B） |
