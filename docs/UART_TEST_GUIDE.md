# Arty A7 ML-KEM 加速器 UART 测试指南

本文说明 bitstream 烧写到 Arty A7-100T 之后，如何通过板载 USB-UART 分层验证 ML-KEM 核心。验证从 LED 心跳一直做到与软件参考实现逐字节比对的 KAT。

测试工具有两个：

| 工具 | 用途 | 依赖 |
|---|---|---|
| `pqc-testkit fpga`（Go） | 一键跑完 Level 1–6，含与软件参考逐字节比对的 KAT | Go 1.25 |
| `scripts/uart_test.py`（Python） | 手动逐层调试：读写寄存器、buffer 回环、单独跑某个运算 | Python 3 + pyserial |

两个工具使用同一套帧协议（见第 6 节），都已经在 RTL 仿真中对 `arty_a7_top` 验证通过。验证方法是用 Verilator 模型挂在虚拟串口上，工具像访问真实板子一样访问它。

---

## 1. 准备

### 1.1 硬件
- 用 micro-USB 线把 Arty 的 J10（PROG/UART）接到电脑。同一根线既用于 JTAG 烧写，也用于 UART。
- 串口参数：**115200 波特，8N1，无流控**。

### 1.2 找到串口
Arty 的 FTDI 芯片会枚举出**两个**端口：一个是 JTAG，一个是 UART。UART 通常是编号较大的那个。

| 系统 | 命令 | UART 通常是 |
|---|---|---|
| macOS | `ls /dev/cu.usbserial-*` | 结尾为 `1` 的那个，如 `/dev/cu.usbserial-210319B0A1B61` |
| Linux | `ls /dev/ttyUSB*` | `/dev/ttyUSB1` |
| Windows | 设备管理器 → 端口 (COM 和 LPT) | 编号较大的 `COMx` |

不确定是哪个时，两个都试一下 `info` 命令（见 3.1），能读回 `ALG_ID = 1` 的就是 UART。

Linux 下如果提示权限不足：`sudo usermod -aG dialout $USER`，然后重新登录。

测试前请关掉 `screen`、`minicom`、PuTTY、串口助手等占用串口的程序。一个串口同一时间只能被一个程序打开。Vivado Hardware Manager 占用的是 JTAG 通道，不影响测试。

### 1.3 编译工具
```bash
cd ML-KEM-Testkit
go build -o pqc-testkit ./cmd/pqc-testkit
python3 -m pip install pyserial        # 只有用 Python 工具时才需要
```

下文用 `$PORT` 代表你的串口，例如：
```bash
export PORT=/dev/cu.usbserial-XXXXXXXX1     # macOS
export PORT=/dev/ttyUSB1                    # Linux
```

---

## 2. Level 1：上电与心跳（只看板子）

| LED | 丝印 | 含义 | 正常现象 |
|---|---|---|---|
| led[0] | LD4 | busy，运算中 | 平时灭；运算约 1 ms，肉眼几乎看不到 |
| led[1] | LD5 | done，运算完成 | 第一次运算完成后常亮，下次 start 或 reset 时清除 |
| led[2] | LD6 | error | 平时灭 |
| led[3] | LD7 | 心跳 | **约 1.5 Hz 闪烁** |

检查步骤：
1. 烧写完成后，LD7 应该开始闪烁。LD7 不闪说明时钟或复位有问题，参见第 8 节。
2. **按住 BTN0**：LD7 停止闪烁并熄灭，因为 BTN0 是高有效复位，按下即复位。
3. 松开 BTN0：LD7 恢复闪烁，复位经过同步后释放。

---

## 3. 一键测试（推荐）：Level 1–6 全部

```bash
./pqc-testkit fpga -T uart -d $PORT -b 115200
```

这条命令依次完成：连接串口 → 读取核心标识 → 寄存器读写 → buffer 回环 → 运算 start/done → **512/768/1024 三个级别的 KeyGen、Encaps、Decaps 和隐式拒绝**。KAT 每次使用新的随机种子，把 FPGA 的输出与软件参考实现（cloudflare/circl）逐字节比对。

正常输出如下（在仿真中得到，实际板子上的周期数应基本相同）：
```
[1/6] Connecting to FPGA... OK (uart)
[2/6] Reading core identification... OK
       Algorithm:      ML-KEM (ID=1)
       Security Level: 768
       Core Version:   2.0.0
       Transport:      uart

[3/6] Register read/write test... OK
[4/6] Data buffer loopback test... OK
[5/6] Operation cycle test... OK (65744 hardware cycles)
[6/6] ML-KEM known-answer tests (FPGA vs software, byte-for-byte):
       ML-KEM-512  KeyGen                   PASS (2432 bytes, 38893 cycles)
       ML-KEM-512  Encaps                   PASS (800 bytes, 49638 cycles)
       ML-KEM-512  Decaps                   PASS (32 bytes, 73422 cycles)
       ML-KEM-512  Decaps (implicit reject) PASS (32 bytes, 73422 cycles)
       ML-KEM-768  KeyGen                   PASS (3584 bytes, 65878 cycles)
       ...
       ML-KEM-1024 Decaps (implicit reject) PASS (32 bytes, 155126 cycles)

=== FPGA Validation Summary ===
Registers:  PASS
Buffer:     PASS
Operation:  PASS (65744 cycles)
ML-KEM KAT: PASS
```

- 整个过程在 115200 波特下大约需要 10 秒，主要时间花在传输密钥上。
- 只想测试连通性、不跑 KAT：加 `--skip-kat`。
- 周期数每次会有几十个周期的差别，这是正常的。SampleNTT 是拒绝采样，消耗的 SHAKE128 字节数取决于公开的种子 ρ。
- 100 MHz 下运算时间：KeyGen-768 约 0.66 ms，Decaps-1024 约 1.55 ms。

出现 `MISMATCH` 时，会打印第一个不一致字节的位置。

---

## 4. 手动分层测试（Python 工具）

一键测试失败时，用 Python 工具逐层定位问题出在哪一层。

### 4.1 Level 2 + 4：读核心标识和寄存器
```bash
python3 scripts/uart_test.py -p $PORT info
```
```
  ALG_ID         0x00000001  1          ML-KEM
  VERSION        0x00020000  131072     v2.0.0
  SEC_LEVEL      0x00000300  768
  STATUS         0x00000000  0          busy=0 done=0 error=0
  DATA_IN_ADDR   0x00000000  0
  DATA_OUT_ADDR  0x00001800  6144
  DATA_OUT_LEN   0x00000e00  3584
  ...
```
单独读写某个寄存器：
```bash
python3 scripts/uart_test.py -p $PORT read  0x0C          # 读 SEC_LEVEL
python3 scripts/uart_test.py -p $PORT write 0x0C 1024     # 写 SEC_LEVEL
```

### 4.2 Level 3：data buffer 回环
```bash
python3 scripts/uart_test.py -p $PORT loopback --size 3000
# loopback 3000 bytes @0x1801 OK (0.55 s)
```
工具向 buffer 写入随机数据再读回比较。默认从非对齐地址 0x1801 开始，这样能同时覆盖按字节写使能的路径。

### 4.3 Level 2–5 自检
```bash
python3 scripts/uart_test.py -p $PORT selftest
```
```
Level 2: register access
  write/read SEC_LEVEL                                 PASS
  bad CRC frame rejected                               PASS
  link in sync after error                             PASS
Level 3: data buffer loopback
  1000-byte unaligned write/read                       PASS
Level 4: core identification
  ALG_ID == 1 (ML-KEM)                                 PASS ALG_ID=1
  VERSION                                              PASS v2.0.0
Level 5: operation start/done
  CTRL.reset clears STATUS                             PASS
  ML-KEM-512 KeyGen: dk holds ek, H(ek), z             PASS (2432 bytes, 38859 cycles)
  ML-KEM-768 KeyGen: dk holds ek, H(ek), z             PASS (3584 bytes, 65899 cycles)
  ML-KEM-1024 KeyGen: dk holds ek, H(ek), z            PASS (4736 bytes, 98775 cycles)
  unsupported SEC_LEVEL -> error code 1                PASS

SELFTEST PASS
```
KeyGen 这一项并不只是检查"跑完了"。它还用 Python `hashlib.sha3_256` 独立验证 dk 中的 H(ek)，并检查 dk 里嵌入的 ek 和 z 是否正确。

### 4.4 Level 5/6：收发双方一致性（roundtrip）
```bash
python3 scripts/uart_test.py -p $PORT roundtrip
```
```
ML-KEM-512  KeyGen  38858 cyc | Encaps  49603 cyc | Decaps  73387 cyc | K match: True | tampered c rejected: True
ML-KEM-768  KeyGen  65791 cyc | Encaps  78448 cyc | Decaps 110510 cyc | K match: True | tampered c rejected: True
ML-KEM-1024 KeyGen  98709 cyc | Encaps 113260 cyc | Decaps 155040 cyc | K match: True | tampered c rejected: True
ROUNDTRIP PASS
```
流程是：FPGA 生成密钥 → 用这个 ek 做 Encaps → 用 dk 对密文做 Decaps，两边得到的共享密钥 K 必须相同。然后改掉密文的 1 位再做 Decaps，这时必须得到不同的 K（隐式拒绝）。

注意：roundtrip 只证明 FPGA 自身前后一致。**与标准实现的逐字节比对（Level 6）请用第 3 节的 Go 工具。**

### 4.5 单独跑一次运算，保存输出
```bash
# KeyGen：输入 64 字节 d||z（十六进制），输出 ek||dk
python3 scripts/uart_test.py -p $PORT run keygen --level 768 \
    --in-hex $(python3 -c "print('00'*64)") --out kg.bin

# Encaps：输入 ek||m，需要一个真实的 ek（从 kg.bin 截取）
python3 -c "
d=open('kg.bin','rb').read(); import os
open('en_in.bin','wb').write(d[:1184] + os.urandom(32))"
python3 scripts/uart_test.py -p $PORT run encaps --level 768 --in en_in.bin --out en.bin
```
用随机字节当 ek 做 Encaps 时，会得到 `core error 2 (ek failed modulus check)`。这是**正确行为**：FIPS 203 要求拒绝系数 ≥ q 的非法 ek。

---

## 5. 自己编写测试

### 5.1 Go（推荐：可以直接调用 circl 做比对）
```go
package main

import (
	"bytes"
	"crypto/rand"
	"fmt"
	"log"
	"time"

	"github.com/akhilesharora/pqc-testkit/pkg/fpga"
	"github.com/akhilesharora/pqc-testkit/pkg/fpga/uart"
	"github.com/cloudflare/circl/kem/mlkem/mlkem768"
)

func main() {
	dev, err := uart.Open("/dev/ttyUSB1", 115200)
	if err != nil {
		log.Fatal(err)
	}
	defer dev.Close()

	// 1. 输入：KeyGen 需要 64 字节种子 d||z，写到 buffer 偏移 0。
	seed := make([]byte, 64)
	rand.Read(seed)
	must(dev.WriteData(0x0000, seed))

	// 2. 配置并启动。
	must(dev.WriteReg(fpga.RegDataInAddr, 0x0000))
	must(dev.WriteReg(fpga.RegDataOutAddr, 0x1800))
	must(dev.WriteReg(fpga.RegSecLevel, 768))
	must(dev.WriteReg(fpga.RegOpMode, fpga.OpKeyGen))
	must(dev.WriteReg(fpga.RegCTRL, fpga.CtrlStart))

	// 3. 等待 done（遇到 error 会返回错误码）。
	cycles, err := fpga.WaitDone(dev, 5*time.Second)
	must(err)

	// 4. 读出结果：DATA_OUT_LEN 字节，从偏移 0x1800 开始。
	n, _ := dev.ReadReg(fpga.RegDataOutLen)
	out, err := dev.ReadData(0x1800, int(n))
	must(err)

	// 5. 与软件参考比对。
	pk, sk := mlkem768.NewKeyFromSeed(seed)
	ek, _ := pk.MarshalBinary()
	dk, _ := sk.MarshalBinary()
	fmt.Printf("cycles=%d  match=%v\n", cycles, bytes.Equal(out, append(ek, dk...)))
}

func must(err error) {
	if err != nil {
		log.Fatal(err)
	}
}
```
`cmd/pqc-testkit/cmd/fpga.go` 中的 `runMLKEMOp` 和 `testMLKEMKAT` 是完整示例，覆盖了 Encaps 和 Decaps 的输入拼接方式。

### 5.2 Python
`scripts/uart_test.py` 里的 `Link` 类可以直接复用：
```python
import sys; sys.path.insert(0, "scripts")
from uart_test import Link
import os

link = Link("/dev/ttyUSB1", 115200, timeout=2.0)
print(hex(link.rd(0x08)))                       # ALG_ID
out, cycles = link.run(1024, 0, os.urandom(64)) # KeyGen ML-KEM-1024
print(len(out), cycles)                         # 4736 字节
link.close()
```

### 5.3 通用测试流程
1. `WriteData(DATA_IN_ADDR, 输入)`：输入格式见 6.4。
2. 写 `SEC_LEVEL`（512/768/1024）和 `OP_MODE`（0/1/2）。
3. 写 `CTRL = 1`（start）。
4. 轮询 `STATUS`：bit1 = done 表示成功，bit2 = error 时读 `ERROR_CODE`。
5. 读 `DATA_OUT_LEN`，再 `ReadData(DATA_OUT_ADDR, DATA_OUT_LEN)`。

---

## 6. 协议与寄存器参考

### 6.1 帧格式（所有多字节字段均为小端）
```
主机 → FPGA:  [CMD:1][ADDR:4][LEN:4][DATA:LEN][CRC32:4]     CRC 覆盖 CMD..DATA
FPGA → 主机:  [STATUS:1][LEN:4][DATA:LEN][CRC32:4]          CRC 覆盖 STATUS..DATA
```
CRC 使用 CRC-32/IEEE，与 Go 的 `crc32.ChecksumIEEE` 和 Python 的 `zlib.crc32` 相同。STATUS 为 `0x00` 表示成功，`0x01` 表示错误（此时 LEN = 0）。

| CMD | 功能 | ADDR | LEN / DATA | 返回 |
|---|---|---|---|---|
| 0x01 | 读寄存器 | 寄存器地址 | LEN=0 | 4 字节 |
| 0x02 | 写寄存器 | 寄存器地址 | LEN=4，值 | 0 字节 |
| 0x03 | 写 buffer | buffer 字节偏移 | LEN=1..256，数据 | 0 字节 |
| 0x04 | 读 buffer | buffer 字节偏移 | LEN=4，要读的字节数（1..16384） | N 字节 |

- 0x03 单帧最多 256 字节，更长的数据由工具自动分帧。
- 以下情况 FPGA 返回错误帧，并且**链路保持同步**，下一帧照常处理：CRC 错误、未知命令、长度非法、越界访问。
- 如果一帧只发了一半就停下，100 ms 后 FPGA 会丢弃这半帧，重新等待新帧。

### 6.2 寄存器（CMD 0x01/0x02 的 ADDR）
| 地址 | 名称 | 读写 | 说明 |
|---|---|---|---|
| 0x00 | CTRL | 只写 | bit0 = start，bit1 = reset（均为脉冲，读回 0） |
| 0x04 | STATUS | 只读 | bit0 busy，bit1 done，bit2 error（done/error 保持到下次 start/reset） |
| 0x08 | ALG_ID | 只读 | 1 = ML-KEM |
| 0x0C | SEC_LEVEL | 读写 | 512 / 768 / 1024（复位值 768） |
| 0x10 | OP_MODE | 读写 | 0 KeyGen，1 Encaps，2 Decaps |
| 0x14 | CYCLE_COUNT | 只读 | 上次运算的时钟周期数 |
| 0x18 | VERSION | 只读 | 0x020000 = v2.0.0 |
| 0x1C | ERROR_CODE | 只读 | 1 = SEC_LEVEL/OP_MODE 非法，2 = ek 模数检查失败，3 = 非法微码 |
| 0x20 | DATA_IN_ADDR | 读写 | 输入在 buffer 中的字节偏移（复位值 0x0000） |
| 0x24 | DATA_IN_LEN | 读写 | 仅作记录，硬件不使用 |
| 0x28 | DATA_OUT_ADDR | 读写 | 输出在 buffer 中的字节偏移（复位值 0x1800） |
| 0x2C | DATA_OUT_LEN | 只读 | 由当前 SEC_LEVEL 和 OP_MODE 决定的输出长度 |

### 6.3 data buffer 布局（16 KB）
| 偏移 | 用途 |
|---|---|
| 0x0000 – 0x17FF | 输入区（DATA_IN_ADDR） |
| 0x1800 – 0x2FFF | 输出区（DATA_OUT_ADDR） |
| 0x3000 – 0x3FFF | 核心内部 scratch，**主机不要在这里放输入或输出数据** |

### 6.4 各运算的输入与输出（字节）
| OP_MODE | 输入（写到 DATA_IN_ADDR） | 输出（从 DATA_OUT_ADDR 读） |
|---|---|---|
| 0 KeyGen | d(32) ‖ z(32) | ek ‖ dk |
| 1 Encaps | ek ‖ m(32) | c ‖ K(32) |
| 2 Decaps | dk ‖ c | K(32) |

| 级别 | ek | dk | c | KeyGen 输出 | Encaps 输出 |
|---|---|---|---|---|---|
| 512 | 800 | 1632 | 768 | 2432 | 800 |
| 768 | 1184 | 2400 | 1088 | 3584 | 1120 |
| 1024 | 1568 | 3168 | 1568 | 4736 | 1600 |

这些运算与 FIPS 203 的 `ML-KEM.KeyGen_internal(d,z)`、`Encaps_internal(ek,m)`、`Decaps_internal(dk,c)` 一致，与 circl 的 `NewKeyFromSeed(d‖z)`、`EncapsulateTo(ct, ss, m)` 逐字节相同。

---

## 7. 各层失败时说明什么

| 层 | 失败现象 | 问题所在 |
|---|---|---|
| L1 | LD7 不闪 | 时钟或复位。检查 bitstream 是否为本版本（端口名应为 `btn0`），以及 XDC 是否正确 |
| L2 | `timeout ... got 0 of 5 bytes` | FPGA 没有回应。检查串口选对没有、TX/RX 引脚，以及 BTN0 是否被按住 |
| L2 | `CRC mismatch` / `link out of sync` | 波特率不一致，或者有其他程序同时占用串口 |
| L3 | `loopback mismatch` | buffer 读写路径有问题 |
| L4 | `ALG_ID` 不是 1 | 地址译码有问题，或者烧的不是 ML-KEM bitstream |
| L5 | done 一直不置位，或 `error` | 看 `ERROR_CODE`：1 是参数非法，2 是 ek 非法 |
| L6 | `MISMATCH at byte N` | 运算数据通路有问题。按字节位置定位：KeyGen 输出前 384k 字节是 t̂，其后 32 字节是 ρ |

---

## 8. 常见问题

**`pqc-testkit` 或 `uart_test.py` 超时，LD7 在闪**
1. 换另一个串口试（FTDI 的两个端口）。
2. 确认没有其他程序打开了这个串口。
3. 按一下 BTN0 复位后重试。
4. 确认板子上烧的是新 bitstream：旧版本的响应 CRC 是 0，工具会报 `CRC mismatch`。

**LD7 不闪**
- 确认 Vivado 的 top 是 `arty_a7_top`，并且 `constraints.xdc` 中 `btn0`/D9、`clk_100mhz`/E3 都在。
- 旧版本把 BTN0 当作低有效复位，松开按钮时设计一直在复位。新版本端口名是 `btn0`。

**Mac 上的串口名**
- 请用 `/dev/cu.usbserial-*`，不要用 `/dev/tty.usbserial-*`：`tty.*` 设备在打开时会等待载波信号而卡住。

**测试中途停下，下一次命令报错**
- 工具每条命令发出前都会清空接收缓冲。FPGA 端 100 ms 内收不到新字节会自动丢弃半帧，所以直接重跑即可。仍然不行就按一下 BTN0。

**想换一个波特率**
- 修改 `hdl/xilinx/arty_a7/arty_a7_top.sv` 中的参数 `BAUD_RATE`，重新综合后，在工具中用 `-b` 指定同一个值。
