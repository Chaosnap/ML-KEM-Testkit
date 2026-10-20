# SAKURA-X：GitHub clone 后在 Linux 烧录

适用硬件：SAKURA-X / SASEBO-GIII，Spartan-6 XC6SLX45 + Kintex-7 XC7K160T，一根 Xilinx Platform Cable USB II（DLC10）。

仓库已包含所需的两个预编译 `.bit`。**只烧录不需要安装 Vivado 或 ISE**；DLC10 自身的固件和 USB 权限仍须配置好。只有重新构建才需要这两个工具。

## 1. 本机提交到 GitHub

新增文件必须一起提交，尤其是 `firmware/`、`third_party/` 和烧录脚本。`build/`、`.venv/`、`data/` 继续忽略，无需上传整个 Vivado 工程缓存。

在项目根目录检查并加入文件：

```bash
git status --short
git add .gitattributes README.md CLAUDE.md THIRD_PARTY_NOTICES.md \
  firmware/sakura_x third_party/aist_sasebo_giii \
  docs/SAKURA_X.md docs/SAKURA_X_LINUX.md \
  scripts/board/program_sakura_dual.tcl scripts/board/sakura_link_check.py \
  scripts/ise/build_sakura_spartan6.tcl \
  testbench/sakura/Makefile tests/test_sakura_programming.py
```

当前工作区还有通信层的修改；如果准备把当前 GUI 的通信修复一起带过去，也将下面这些文件纳入同一批审核和提交：

```bash
git add cpa_toolkit/sakura_lbus.py cpa_toolkit/uart_target.py tests/test_sakura_lbus.py
git diff --cached --stat
git diff --cached --check
git commit -m "Package SAKURA-X dual-FPGA programming for Linux"
git push
```

两个 `.bit` 合计约 3.1 MiB，使用普通 Git 保存，无需 Git LFS。AIST 文件保留原许可和版权说明，见 `THIRD_PARTY_NOTICES.md`。

## 2. Linux 准备与 clone

Debian / Ubuntu 示例：

```bash
sudo apt update
sudo apt install git tcl openfpgaloader python3-venv python3-pip usbutils
git clone https://github.com/Chaosnap/ML-KEM-Testkit.git
cd ML-KEM-Testkit
```

私有仓库需先配置自己的 GitHub 认证；如果推送到非默认分支，clone 后切换到该分支。

检查工具及固件：

```bash
openFPGALoader --version
openFPGALoader --help
(cd firmware/sakura_x && sha256sum -c SHA256SUMS)
tclsh scripts/board/program_sakura_dual.tcl --dry-run
```

帮助中须支持 `--misc-device`、`--index-chain` 和 `--write-sram`；若发行版版本缺少这些选项，按 [openFPGALoader 官方安装说明](https://trabucayre.github.io/openFPGALoader/guide/install.html) 更新。脚本开发时使用 openFPGALoader 1.1.1 做命令接口核对。

`--dry-run` 检查文件和器件型号、展示计划，不访问板卡。脚本无参数时固定使用仓库里的固件快照，不会自动选择 `build/` 中的新文件。

## 3. DLC10 与权限

CN15 接板卡 USB，保持板卡供电；DLC10 的 USB 接电脑。关闭占用下载线的 Vivado Hardware Manager、iMPACT、hw_server、XVC 等进程。

```bash
lsusb
openFPGALoader --scan-usb
```

DLC10 完成固件加载后通常显示 `03fd:0008`；板卡 FT2232H 通常显示 `0403:6010`，两者用途不同。如果下载线仍处于未加载固件状态，可使用已有 Vivado 安装目录中的线缆驱动安装程序（需要管理员权限）：

```bash
cd /path/to/Vivado/data/xicom/cable_drivers/lin64/install_script/install_drivers
sudo ./install_drivers
```

之后重新插拔 **DLC10 的 USB**。如果没有 Vivado，需先为 DLC10 配置兼容的固件加载环境；仅安装 openFPGALoader 不保证所有发行版都自动准备好该固件。仓库不打包 Xilinx 专有驱动或线缆固件。

若报 USB permission denied，按上面的官方说明安装 openFPGALoader 的 udev 规则并加入规则指定的组。板载串口通常还需要：

```bash
sudo usermod -aG dialout "$USER"
```

修改组后注销并重新登录。避免使用 `sudo` 启动整个 GUI。

## 4. 一条命令依次烧录两片 FPGA

回到 clone 的仓库根目录：

```bash
tclsh scripts/board/program_sakura_dual.tcl
```

1. DLC10 排线接 **CN10**，在提示处输入 `CN10`：加载 `sasebo_giii_ctrl.bit`。
2. 等第一步结束、程序再次提示后，将排线换到 **CN5**，输入 `CN5`：加载 `sakura_x_top.bit`。
3. 两步之间保持板卡供电。切勿拔掉负责供电的 CN15 USB。

脚本使用 3 MHz JTAG，检查器件型号并识别 FPGA 在链上的位置；CN10 的 XCF32P PROM 被旁路。任何一步失败立即停止。这是 **SRAM 临时配置**，不会写 Flash 或 FTDI EEPROM；断电后需再运行。两条 JTAG 链不支持靠软件自动替代物理换线。

## 5. 验证通信与打开 GUI

在仓库根目录创建 Linux 自己的 Python 环境：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install uv
.venv/bin/uv sync
.venv/bin/python scripts/board/sakura_link_check.py
.venv/bin/python cpa_toolkit/web_gui.py --no-browser
```

浏览器打开 `http://127.0.0.1:8765`，在 GUI 中选择 `sakura:/dev/ttyUSB0` 或链路检查实际确认的通道 A 端口。与 GUI 连接互斥地运行链路检查，避免两者同时占用端口。

预期 ID 是 **0x4D4B**：

- 正确 ID：USB → Spartan 控制器 → Kintex 桥已响应，再做 GUI 的链路自检。
- 有响应但 ID 不符：检查 Kintex 是否加载了本项目 ML-KEM 固件。
- 无响应：检查 CN15、权限、Spartan 配置以及 FT2232H 通道 A 的 245 FIFO 模式。

`third_party/aist_sasebo_giii/sasebo_giii.xml` 是原版 FT_Prog 模板，供需要时人工恢复 EEPROM。产品描述字符串本身不能证明 FIFO 模式；先读出配置确认。重新插拔板卡 USB 可能断电，之后需要重烧 SRAM。

## 6. 可选：在 Linux 重新构建

加载各自工具的环境后分别运行：

```bash
# ISE 14.7 xtclsh；默认使用仓库内的 AIST 最小参考源码
xtclsh scripts/ise/build_sakura_spartan6.tcl

# Vivado；生成 Kintex ML-KEM
vivado -mode batch -source scripts/vivado/build_sakura_x.tcl
```

烧录刚构建的文件时显式传入路径：

```bash
tclsh scripts/board/program_sakura_dual.tcl \
  build/ise/sakura-x_ctrl/sasebo_giii_ctrl.bit \
  build/vivado/sakura-x_mlkem/pqc_mlkem_sakura_x.runs/impl_1/sakura_x_top.bit
```

若希望 GitHub 的固件也更新，复制构建产物到 `firmware/sakura_x/`，同时更新 `SHA256SUMS`、`manifest.json` 和时序报告，再一并提交。构建脚本不自动替换发布快照。

## 7. 本次打包验证范围

固件源自现有构建输出和原版 AIST 参考包；打包过程中没有重新综合，也没有实板烧录。通过隔离目录的默认路径检查、文件哈希核对、烧录流程自动测试及 AIST 控制器对接仿真来确认仓库可搬运性。最终 GUI 连通性以 Linux 实板检查结果为准。
