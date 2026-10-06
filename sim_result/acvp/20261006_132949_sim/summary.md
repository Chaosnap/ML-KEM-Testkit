# ACVP 测试报告：ML-KEM-768（RTL 仿真，Verilator）

**结论：PASS**　必测向量 60/60 通过（keyGen 25 + encapsulation 25 + decapsulation 10）。

| 项目 | 值 |
| ---- | -- |
| 时间 | 2026-10-06 13:29:49 |
| 目标 | RTL 仿真（testbench/core_sim，Verilator） |
| 源码版本 | git d86c824 |
| 向量 | ML-KEM-keyGen-FIPS203 vsId 42；ML-KEM-encapDecap-FIPS203 vsId 42 |
| 核心时钟 | 206.00 MHz（CYCLE_COUNT 按核心时钟计数） |
| 主机 | macOS-15.8-arm64-arm-64bit，Python 3.9.6 |

## 各组结果

| 组 | 通过 / 总数 | 结果 |
| -- | ----------: | ---- |
| keyGen (AFT) | 25 / 25 | PASS |
| encapsulation (AFT) | 25 / 25 | PASS |
| decapsulation (VAL, incl. implicit rejection) | 10 / 10 | PASS |
| encapsulationKeyCheck (VAL) | 10 / 10 | PASS |
| parameter checks (ERROR_CODE 1) | 10 / 10 | PASS |
| 跳过：other parameter sets | 160 | — |
| 跳过：decapsulationKeyCheck (not implemented by the core) | 10 | — |

## 运算周期（必测向量中通过的用例）

| 运算 | 用例数 | 最少周期 | 平均周期 | 最多周期 | 平均时间 @ 206.00 MHz |
| ---- | -----: | -------: | -------: | -------: | ------------------: |
| KeyGen | 25 | 16631 | 16653 | 16703 | 80.8 µs |
| Encaps | 25 | 19205 | 19245 | 19321 | 93.4 µs |
| Decaps | 10 | 26270 | 26304 | 26363 | 127.7 µs |

Decaps 的周期数随密文略有变化：重加密时 SampleNTT 的拒绝采样次数取决于输入。

## 全部用例

| 组 | tcId | 结果 | 周期 | 说明 | ACVP reason |
| -- | ---- | ---- | ---: | ---- | ----------- |
| keygen | 26 | PASS | 16655 | matches NIST |  |
| keygen | 27 | PASS | 16661 | matches NIST |  |
| keygen | 28 | PASS | 16645 | matches NIST |  |
| keygen | 29 | PASS | 16643 | matches NIST |  |
| keygen | 30 | PASS | 16633 | matches NIST |  |
| keygen | 31 | PASS | 16649 | matches NIST |  |
| keygen | 32 | PASS | 16654 | matches NIST |  |
| keygen | 33 | PASS | 16703 | matches NIST |  |
| keygen | 34 | PASS | 16669 | matches NIST |  |
| keygen | 35 | PASS | 16643 | matches NIST |  |
| keygen | 36 | PASS | 16659 | matches NIST |  |
| keygen | 37 | PASS | 16659 | matches NIST |  |
| keygen | 38 | PASS | 16653 | matches NIST |  |
| keygen | 39 | PASS | 16637 | matches NIST |  |
| keygen | 40 | PASS | 16672 | matches NIST |  |
| keygen | 41 | PASS | 16668 | matches NIST |  |
| keygen | 42 | PASS | 16637 | matches NIST |  |
| keygen | 43 | PASS | 16631 | matches NIST |  |
| keygen | 44 | PASS | 16663 | matches NIST |  |
| keygen | 45 | PASS | 16647 | matches NIST |  |
| keygen | 46 | PASS | 16659 | matches NIST |  |
| keygen | 47 | PASS | 16654 | matches NIST |  |
| keygen | 48 | PASS | 16643 | matches NIST |  |
| keygen | 49 | PASS | 16640 | matches NIST |  |
| keygen | 50 | PASS | 16650 | matches NIST |  |
| encaps | 26 | PASS | 19250 | matches NIST |  |
| encaps | 27 | PASS | 19245 | matches NIST |  |
| encaps | 28 | PASS | 19291 | matches NIST |  |
| encaps | 29 | PASS | 19235 | matches NIST |  |
| encaps | 30 | PASS | 19288 | matches NIST |  |
| encaps | 31 | PASS | 19242 | matches NIST |  |
| encaps | 32 | PASS | 19247 | matches NIST |  |
| encaps | 33 | PASS | 19209 | matches NIST |  |
| encaps | 34 | PASS | 19237 | matches NIST |  |
| encaps | 35 | PASS | 19244 | matches NIST |  |
| encaps | 36 | PASS | 19238 | matches NIST |  |
| encaps | 37 | PASS | 19299 | matches NIST |  |
| encaps | 38 | PASS | 19231 | matches NIST |  |
| encaps | 39 | PASS | 19205 | matches NIST |  |
| encaps | 40 | PASS | 19249 | matches NIST |  |
| encaps | 41 | PASS | 19226 | matches NIST |  |
| encaps | 42 | PASS | 19253 | matches NIST |  |
| encaps | 43 | PASS | 19215 | matches NIST |  |
| encaps | 44 | PASS | 19234 | matches NIST |  |
| encaps | 45 | PASS | 19218 | matches NIST |  |
| encaps | 46 | PASS | 19321 | matches NIST |  |
| encaps | 47 | PASS | 19239 | matches NIST |  |
| encaps | 48 | PASS | 19251 | matches NIST |  |
| encaps | 49 | PASS | 19228 | matches NIST |  |
| encaps | 50 | PASS | 19242 | matches NIST |  |
| decaps | 86 | PASS | 26363 | matches NIST | valid decapsulation |
| decaps | 87 | PASS | 26303 | matches NIST | valid decapsulation |
| decaps | 88 | PASS | 26278 | matches NIST | modified ciphertext |
| decaps | 89 | PASS | 26322 | matches NIST | valid decapsulation |
| decaps | 90 | PASS | 26298 | matches NIST | valid decapsulation |
| decaps | 91 | PASS | 26314 | matches NIST | modified ciphertext |
| decaps | 92 | PASS | 26270 | matches NIST | modified ciphertext |
| decaps | 93 | PASS | 26308 | matches NIST | modified ciphertext |
| decaps | 94 | PASS | 26309 | matches NIST | valid decapsulation |
| decaps | 95 | PASS | 26278 | matches NIST | modified ciphertext |
| ekcheck | 136 | PASS | 19231 | accepted as expected | valid encapsulation key |
| ekcheck | 137 | PASS | 19217 | ERROR_CODE 2 as expected | noisy linear system values too large |
| ekcheck | 138 | PASS | 19249 | ERROR_CODE 2 as expected | noisy linear system values too large |
| ekcheck | 139 | PASS | 19253 | accepted as expected | valid encapsulation key |
| ekcheck | 140 | PASS | 19235 | accepted as expected | valid encapsulation key |
| ekcheck | 141 | PASS | 19295 | ERROR_CODE 2 as expected | noisy linear system values too large |
| ekcheck | 142 | PASS | 19251 | ERROR_CODE 2 as expected | noisy linear system values too large |
| ekcheck | 143 | PASS | 19244 | accepted as expected | valid encapsulation key |
| ekcheck | 144 | PASS | 19252 | accepted as expected | valid encapsulation key |
| ekcheck | 145 | PASS | 19222 | ERROR_CODE 2 as expected | noisy linear system values too large |
| errors | SEC_LEVEL=512 KeyGen | PASS |  | ERROR_CODE 1 as expected |  |
| errors | SEC_LEVEL=512 Encaps | PASS |  | ERROR_CODE 1 as expected |  |
| errors | SEC_LEVEL=512 Decaps | PASS |  | ERROR_CODE 1 as expected |  |
| errors | SEC_LEVEL=1024 KeyGen | PASS |  | ERROR_CODE 1 as expected |  |
| errors | SEC_LEVEL=1024 Encaps | PASS |  | ERROR_CODE 1 as expected |  |
| errors | SEC_LEVEL=1024 Decaps | PASS |  | ERROR_CODE 1 as expected |  |
| errors | SEC_LEVEL=999 KeyGen | PASS |  | ERROR_CODE 1 as expected |  |
| errors | SEC_LEVEL=999 Encaps | PASS |  | ERROR_CODE 1 as expected |  |
| errors | SEC_LEVEL=999 Decaps | PASS |  | ERROR_CODE 1 as expected |  |
| errors | OP_MODE=3 | PASS |  | ERROR_CODE 1 as expected |  |

文件：`results.csv`（每个用例一行）、`summary.json`、`console.log`。
