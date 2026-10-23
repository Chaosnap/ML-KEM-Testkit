# CPA Toolkit GUI 使用指南

本指南说明 Web GUI 的操作。GUI 控制 FPGA 板和示波器，生成测试向量，采集 trace，并做 CPA / TVLA 分析。

## 0. 本指南的写法（ASD-STE100）

本指南按 ASD-STE100（简化技术英语）的规则写成中文：

- 一句只写一个指令。指令用祈使句："点击……"、"选择……"。
- 一个术语只有一个意思（见第 1 节）。
- 先写条件，再写动作："如果……，则……"。
- 步骤有编号。按编号顺序做。
- **警告**：可能丢失数据。**注意**：可能使操作失败。
- GUI 上的文字用粗体英文原样写出，例如 **Connect**。

GUI 本身的提示也按 ASD-STE100 写成短句英文。

## 1. 术语

| 术语 | 意思 |
| ---- | ---- |
| 选中 | 点击表格中的一行。右侧显示这一行的详细数据。一次只选中一行。 |
| 勾选 | 点击一行左侧的复选框。勾选的行用于批量操作。可以勾选多行。 |
| 向量集 | 一组测试向量：一个密钥对、多个密文和每个密文的期望 K。同一个种子生成相同的向量集。 |
| Campaign | 一次采集的结果：一个 `.hdf5` 文件，包含 trace、密文和期望 K。 |
| 移到回收站 | 把文件移到 `.trash` 目录。可以恢复。 |
| 永久删除 | 从磁盘删除文件。不能恢复。 |
| 撤销 | 点击提示框中的 **Undo**，取消上一次"移到回收站"。 |

## 2. 启动 GUI

1. 在项目目录中执行：

   ```bash
   make gui
   ```

2. 浏览器打开 `http://127.0.0.1:8765`。
3. 如果页面没有更新，按 Ctrl+Shift+R（macOS：⌘+Shift+R）刷新页面。

左侧导航按实验顺序排列：**Connection** → **Test Vectors** → **Acquisition** → **Trace Library** → **Analysis**。**Advanced** 用于单步调试。

顶栏的指示灯显示板子、示波器和后台任务的状态。点击指示灯打开 **Connection** 页面。

底部的 **Console** 显示完整日志。点击 **Console** 展开或收起日志。

## 3. Connection：连接板子和示波器

### 3.1 连接 SAKURA-X

开始前，做以下准备（详细步骤见 [SAKURA_X.md](SAKURA_X.md)）：

- FT2232H 的 EEPROM 设置正确：A 通道 = 245 FIFO，B 通道 = UART（见第 10 节）。
- Spartan-6（CN10）已烧录 `sakura-x-shell-ctrl.bit`。
- Kintex-7（CN5）已烧录 `sakura_x_top.bit`。

**注意：** 断电会清除两片 FPGA 的配置。每次上电后，按顺序重新烧录 Spartan-6 和 Kintex-7。

1. 在 **Board** 中选择 **SAKURA-X — USB FIFO**。
2. 点击 **Refresh**。
3. 在 **SAKURA-X USB device** 中选择板子。

   - 列表用 USB 序列号区分板子，例如 `FT9A4QV0`。
   - 如果只有一块 SAKURA-X，GUI 自动选择它。
   - 不要按 `/dev/ttyUSB0`、`/dev/ttyUSB1` 的编号判断 A/B 通道。GUI 自动配对 A/B 通道。

4. 点击 **Connect**。
5. 查看 **Connection checks** 表。每一步显示 PASS 或 FAIL。

| 步骤 | PASS 的意思 |
| ---- | ----------- |
| USB channel pairing | A、B 通道属于同一个 USB 设备 |
| Open reset channel B / Open FIFO channel A | 两个通道已打开 |
| Channel B reset | RTS 复位已发送，Spartan-6 控制器状态已清除 |
| Spartan controller | Spartan-6 上的 shell 控制器回应 |
| Kintex shell identity | Kintex-7 回应，两片 FPGA 之间的总线正常 |
| ML-KEM core | ML-KEM 核心回应，可以运行 Decaps |

6. 如果有一步 FAIL，读这一行的 **detail**，然后按第 9 节处理。
7. 如果要请别人帮忙，点击 **Copy report**，把内容粘贴给对方。报告只有 USB 设备和检查结果，不包含密钥和 trace。

每次点击 **Connect**，GUI 先通过 B 通道复位 Spartan-6 控制器。复位后板上的 DK/CT 无效。采集程序自动重新加载 DK/CT。

### 3.2 连接 Arty A7 / Z7

1. 在 **Board** 中选择 **Arty A7 / Z7 — UART**。
2. 在 **Serial port** 中选择标为 **board UART (recommended)** 的端口。不要选择 JTAG 通道。
3. 确认 **Baud rate** 为 115200。
4. 如果要在掉线后自动重连，勾选 **Reconnect automatically**。
5. 点击 **Connect**。

**注意：** Arty Z7 PS 版每次上电后，先执行 `uv run scripts/board/boot_arty_z7_ps.py`，再连接。

### 3.3 检查链路

1. 连接板子。
2. 点击 **Run link self-test**。
3. 确认每一行都是 PASS。

### 3.4 连接示波器

1. 在 **IP address** 中输入示波器的 IP 地址。
2. 选择 **Driver**：Teledyne LeCroy 选第一项。其他品牌选对应的一项。
3. 选择 **Transport**。它必须与示波器的远程控制设置相同（LeCroy：Utilities › Remote）。
4. 在 **Channel** 中输入测量信号的通道，例如 `C1`。不要输入触发通道。
5. 点击 **Connect**。
6. 如果不知道 IP 地址，点击 **Find instruments**，然后点击列表中的一行。GUI 自动填入 IP 地址。
7. 如果要保存这些设置，点击 **Save as default**。

## 4. Test Vectors：测试向量

### 4.1 生成向量集

1. 在 **Purpose** 中选择用途：
   - **CPA · key recovery**：CPA 密钥恢复。
   - **TVLA · leakage test**：TVLA 泄漏检测。
2. 在 **Vectors** 中输入向量数量。
3. 在 **Seed (hex)** 中输入种子。如果要随机种子，点击骰子按钮，或保持为空。
4. 只用于 CPA：在 **Ciphertexts** 中选择密文类型。默认选择 **Valid encapsulations**。
5. 点击 **Generate vector set**。

生成时，软件用 FIPS 203 计算每个密文的期望 K。

### 4.2 检查向量集

1. 在 **Vector sets** 中选中一个向量集。
2. 点击 **Verify in software**。软件重新计算所有 K。
3. 连接板子。
4. 在 **Verify on the board** 中输入数量，例如 20。
5. 点击 **Run on board**。
6. 确认所有行为 **✓ match**，并且 CYCLE_COUNT 不变。

### 4.3 用于采集

1. 选中向量集。
2. 点击 **Use for acquisition →**。GUI 打开 **Acquisition** 页面，并选择这个向量集。

### 4.4 名称和标签

1. 选中向量集。
2. 在名称框中输入名称。点击 **Save name**，或按 Enter。
3. 在标签框中输入标签。按 Enter 或逗号确认。
4. 如果要删除最后一个标签，在空的标签框中按 Backspace。

批量操作见第 6.3 节。向量集和 Campaign 的操作方法相同。

## 5. Acquisition：采集

### 5.1 设置示波器

1. 连接板子和示波器。
2. 在 **Setup capture** 中点击 **Capture**。GUI 用一个新向量运行一次 Decaps，并显示波形。
3. 调整示波器的垂直刻度和时基。
4. 点击 **Check trigger**。确认每一行都是 PASS。
5. 如果要看接线和触发设置，展开 **Trigger wiring and scope settings**。

触发信号 `trig_out` 在 Decaps 运行时为高电平：

| 板子 | 触发引脚 | 触发电平 |
| ---- | -------- | -------- |
| Arty A7 | Pmod JA1 | 1.6 V |
| Arty Z7 | Pmod JA3 | 1.6 V |
| SAKURA-X | CN8 第 3 脚（地：CN8 第 13 脚） | 1.25 V |

### 5.2 开始采集

1. 查看 **Campaign** 中的检查项。每一项必须有 ✓。如果某一项没有 ✓，点击它右侧的链接。
2. 在 **Test-vector set** 中选择向量集。
3. 保持 **Samples per trace** 为 0。GUI 使用示波器的记录长度。
4. 点击 **Start acquisition**。
5. 在 **Live preview** 中查看最新 trace 和均值。

每条 trace 都检查 K 和 CYCLE_COUNT。如果 K 不正确，这条 trace 被标记。

**注意：** 采集时不要改变示波器的时基和触发设置。如果改变了设置，停止采集，重新连接示波器。

### 5.3 停止和继续

1. 点击 **Stop**。采集在当前 trace 完成后停止。文件保留。
2. 如果要继续，展开 **Resume a stopped campaign**，选择文件，然后点击 **Start acquisition**。

## 6. Trace Library：管理 Campaign

### 6.1 查看 Campaign

1. 在表格中选中一个 Campaign。右侧显示 K 正确率、CYCLE_COUNT、BMUL 窗口覆盖、来源信息。
2. 点击 **View traces** 打开波形查看器。查看器替换左侧的列表。
3. 在查看器中设置 **from #** 和 **count**，然后点击 **Plot**。
4. 如果要逐条播放，点击 **▶ Play**，或按空格键。
5. 点击 **‹ Campaigns** 回到列表。

### 6.2 导出

1. 选中一个 Campaign。
2. 点击 **Export ▾**。
3. 选择 **Full campaign (.hdf5)** 或 **Slice of traces (.npz)…**。

### 6.3 多选和批量操作

勾选行的方法：

| 操作 | 结果 |
| ---- | ---- |
| 点击行左侧的复选框（或复选框所在的格子） | 勾选或取消这一行 |
| 按住 Shift，点击另一行的复选框 | 勾选两行之间的所有行 |
| 按住 Ctrl（macOS：⌘），点击一行 | 勾选这一行，不打开详细数据 |
| 按住 Shift，点击一行 | 勾选从上次勾选的行到这一行之间的所有行 |
| 点击表头的复选框 | 勾选当前显示的所有行 |
| 按 Ctrl+A（macOS：⌘+A） | 勾选当前显示的所有行 |
| 按 Esc | 取消所有勾选 |

勾选后，表格上方显示操作栏：

- **n selected**：已勾选的行数。
- **Select all N**：勾选当前显示的所有行。
- **Tags…**：给所有勾选的行增加或删除标签。
- **Move to trash**：把所有勾选的行移到回收站。
- **Clear**：取消所有勾选。

### 6.4 移到回收站和撤销

1. 勾选一行或多行。如果没有勾选，操作作用于选中的行。
2. 点击 **Move to trash**，或按 Delete（macOS：Backspace）。
3. 提示框显示"moved to the trash"。
4. 如果要撤销，在提示框消失前点击 **Undo**。

"移到回收站"可以恢复，所以 GUI 不再询问确认。

### 6.5 恢复和永久删除

1. 点击 **Trash**。表格显示回收站中的文件。
2. 勾选一行或多行。
3. 点击 **Restore** 恢复文件。
4. 如果要永久删除，点击 **Delete permanently**，或按 Delete。
5. 在确认框中点击 **Delete permanently**。
6. 点击 **← Back to library**（向量集页面：**← Back to sets**）回到列表。

**警告：** 永久删除不能恢复。永久删除 Campaign 时，由它生成的分析报告也被删除。

**警告：** **Empty trash** 永久删除回收站中的所有文件。

确认框中，Enter 不确认危险操作。按 Esc 或点击确认框外部取消操作。

## 7. Analysis：分析

### 7.1 CPA

1. 在 **Campaign** 中选择一个 CPA Campaign。
2. 点击 **New analysis**。
3. 在 **1 Hypothesis** 中选择假设：`hw_product_mod` = HW(u·g mod q)，`hw_product_raw` = HW(u·g)。
4. 在 **2 What to attack** 中设置：
   - **Polynomials**：例如 `0-2`。
   - **Coefficients per polynomial**：8 = 前 8 个（数秒）；0 = 全部 256 个（数分钟）。
   - **Where in the trace**：先选 **Whole BMUL window**。
5. 在 **3 Scoring** 中选择：
   - **Blind attack**：不使用真实密钥。结果与真实攻击者相同。
   - **Rank the campaign key**：用真实密钥给猜测排名。假设仍然只用公开密文。
6. 点击 **Run analysis**。
7. 查看 **Overview** 标签中的 offset 列。如果 offset 集中在一个值，选择 **Around the predicted cycle**，在 **shift (cycles)** 中输入这个值，然后再运行一次。这样更快，假峰更少。

结果标签：

| 标签 | 内容 |
| ---- | ---- |
| Overview | 每个系数一行：最佳猜测、峰值 \|ρ\|、稳定性、与预测周期的偏差 |
| Coefficient | 一个系数的五张图。用 ← → ↑ ↓ 切换系数 |
| Compare | 同一个 Campaign 的所有 CPA 报告 |
| Details | 运行参数 |

### 7.2 TVLA

1. 在 **Campaign** 中选择一个 TVLA Campaign。
2. 点击 **New analysis**，然后点击 **Run analysis**。
3. 查看 \|t\| 曲线。如果 \|t\| 超过阈值（4.5），说明 fixed 和 random 两类 trace 可以区分。

### 7.3 报告

1. 在 **Report** 中选择已保存的报告。
2. 点击 **Report ▾**，可以复制摘要、重新运行、下载 JSON/CSV 或删除报告。

## 8. Advanced：单步操作

**注意：** 单步操作只用于调试。采集时不要使用这个页面。

SAKURA-X 的五条命令：

1. 点击 **Load selected test vector**，或选择 DK/CT 文件，或粘贴十六进制。
2. 点击 **RESET**。
3. 点击 **LOAD_DK**。
4. 点击 **LOAD_CT**。
5. 点击 **START**。Decaps 完成后才回应。
6. 点击 **READ_SS** 读取 K。
7. 如果要一次完成以上步骤并比较 K，点击 **Run full Decaps & check K**。

每次 START 后，CT 无效。下一次 START 前，再执行 LOAD_CT。RESET 后，DK 和 CT 都无效。

**Registers** 读取全部寄存器。**Data buffer** 读写数据缓冲区。

## 9. 故障排除

| 问题 | 原因 | 处理 |
| ---- | ---- | ---- |
| 设备列表为空 | USB 线没有连接，或板子没有电源 | 连接 USB 线。打开电源。点击 **Refresh** |
| USB channel pairing：FAIL | A、B 通道没有配对 | 断开 USB 线，再连接。点击 **Refresh** |
| Open … channel：FAIL | 其他程序打开了端口，或没有权限 | 关闭其他程序。Linux：把用户加入 `dialout` 组，然后重新登录 |
| Spartan controller：No Spartan-6 controller replies | Spartan-6 没有配置；或 A 通道不是 245 FIFO；或控制器锁死 | 1. 确认 Spartan-6 的 DONE LED 亮。2. 执行 `bash scripts/board/sakura_x_eeprom.sh check`。3. 断电，再上电，重新烧录两片 FPGA |
| Spartan controller：FACTORY controller | Spartan-6 运行出厂控制器（断电后的默认状态） | 烧录 `sakura-x-shell-ctrl.bit` 到 CN10，再烧录 `sakura_x_top.bit` 到 CN5。两步之间不要断电 |
| Kintex shell identity：FAIL | Kintex-7 没有烧录正确的 bit | 烧录 `sakura_x_top.bit` 到 CN5。确认 Kintex-7 的 LED10 亮 |
| 采集中显示 **Link lost** | 板子复位、断电或 USB 断开 | 点击 **Connect**。在 **Acquisition** 中继续采集（第 5.3 节） |
| **Check trigger** 失败 | 触发设置或时基不正确 | 按第 5.1 节检查接线和触发电平。把触发点放在记录的左边缘 |
| 页面显示旧内容 | 浏览器缓存 | 按 Ctrl+Shift+R 刷新页面 |

Spartan-6 LED 判断：

| 控制器 | LED |
| ------ | --- |
| shell 控制器 | LED17 约每 1.3 s 闪一次。LED16 常亮：控制器停在一个事务中 |
| 出厂控制器 | LED11 每秒闪约 3 次 |
| 没有配置 | 没有 LED 闪烁 |

## 10. EEPROM 一键工具（Linux）

SAKURA-X 的 FT2232H 必须设置为：A 通道 = 245 FIFO，B 通道 = UART。如果 A 通道是 UART，PC 不能与 Spartan-6 通信。

**注意：** 执行工具前，关闭 GUI 和所有使用板子 USB 的程序。

修正设置：

```bash
bash scripts/board/sakura_x_eeprom.sh
```

1. 工具自动找到 SAKURA-X 板。工具不修改 Digilent（Arty）设备。
2. 工具先保存备份，然后显示要修改的内容。
3. 输入 `y`，按 Enter。工具写入 EEPROM，然后读回检查。
4. 断开板子的 USB 线，再连接。FT2232H 只在上电时读取 EEPROM。
5. 如果板子用 USB 供电（SW1 = USB），重新烧录两片 FPGA。
6. 在 GUI 中点击 **Refresh**，然后点击 **Connect**。

工具同时把 USB 产品名设为 `SAKURA-X Shell`。GUI 用这个名称自动选择板子。如果不要修改产品名，加 `--keep-name`。

恢复原来的设置：

```bash
bash scripts/board/sakura_x_eeprom.sh restore
```

工具写回第一次修改前的备份（`data/eeprom/<序列号>_original.bin`），然后逐字节检查。

只读检查：

```bash
bash scripts/board/sakura_x_eeprom.sh check
```

其他选项：

| 选项 | 作用 |
| ---- | ---- |
| `--serial FT9A4QV0` | 连接了多块板子时，选择一块 |
| `--file 备份.bin` | `restore` 使用指定的备份 |
| `--yes` | 不询问，直接写入 |

第一次执行时，工具把 pyftdi 安装到 `~/ftdi-venv`。工具需要 root 权限访问 USB 设备，所以会询问 sudo 密码。

## 11. 键盘快捷键

| 位置 | 按键 | 作用 |
| ---- | ---- | ---- |
| Test Vectors、Trace Library | Delete 或 Backspace | 把勾选的行（或选中的行）移到回收站；在回收站中永久删除（先确认） |
| Test Vectors、Trace Library | Esc | 取消所有勾选 |
| Test Vectors、Trace Library | Ctrl+A / ⌘+A | 勾选当前显示的所有行 |
| 表格 | Shift+点击 | 勾选一个范围 |
| 表格 | Ctrl+点击 / ⌘+点击 | 勾选一行，不打开详细数据 |
| 确认框 | Enter | 确认（危险操作除外） |
| 确认框 | Esc | 取消 |
| 波形查看器 | 空格 | 播放 / 暂停 |
| Analysis → Coefficient | ← → ↑ ↓ | 切换系数 |

输入框中的按键不触发快捷键。
