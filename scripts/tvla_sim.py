#!/usr/bin/env python3
"""Simulated TVLA (pre-silicon leakage assessment) of the ML-KEM-768 RTL.

Runs a fixed-vs-random Welch t-test on simulated power traces of the core
and writes plots and a report:

  1. builds the Verilator model (testbench/tvla_sim) and the Go engine
     (`pqc-testkit sca sim`);
  2. main test: half the traces with one fixed input, half with random
     inputs, shuffled. Every trace is one RTL simulation; its VCD becomes a
     power trace = number of signal bits toggling in each clock cycle
     (Hamming-distance model), for the whole core and per module, plus
     Gaussian noise (--noise). Welch's t is computed for every cycle;
  3. sanity check (fixed-vs-fixed): both classes get the same input, so no
     cycle may show real leakage; this validates the flow itself;
  4. figures (PNG) and report.md with tables per module and per microcode
     instruction.

The core is unmasked, so leakage IS expected: the result shows where and
when the unprotected baseline leaks, not that it is secure.

Usage:
  uv run python scripts/tvla_sim.py                          # Decaps, 200 traces + sanity
  uv run python scripts/tvla_sim.py -n 1000 --noise 2        # more traces, more noise
  uv run python scripts/tvla_sim.py --op keygen
  uv run python scripts/tvla_sim.py --invalid-random         # random class: random ciphertexts
  uv run python scripts/tvla_sim.py --replot sim_result/tvla/<run>   # redo figures only

Results go to sim_result/tvla/<YYYYmmdd_HHMMSS>_<op>/:
  report.md        the report (open in VS Code / any Markdown viewer)
  figures/*.png    t-traces, per-module t, mean power, leakage per instruction, sanity
  raw/main/        engine output: tvla_t.csv, tvla_mean.csv, vectors.csv, summary.txt, vcd/
  raw/sanity/      the same for the fixed-vs-fixed run
  analysis.json    numbers behind the report

Guide: docs/TVLA_SIM.md
"""

import argparse
import csv
import datetime
import json
import math
import os
import re
import subprocess
import sys
import time

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
DEFAULT_OUT = os.path.join(ROOT, "sim_result", "tvla")
ENGINE = os.path.join(ROOT, "build", "pqc-testkit")
MODEL = os.path.join(ROOT, "testbench", "tvla_sim", "obj_dir", "Vtb_tvla")

THRESHOLD = 4.5                  # TVLA threshold on |t|
CORE_MHZ = 206.0                 # Core clock of the board build (cycles -> time in the report)

# Per-module columns of tvla_t.csv (t_<name>), in plot order.
MODULES = ["u_alu", "u_sponge", "u_unpack", "u_pack", "u_polyram", "u_dbuf", "u_ctrl", "top", "u_csr"]
MODULE_TEXT = {
    "total": "整个核心",
    "u_alu": "多项式 ALU（NTT/INTT、BMUL、加减）",
    "u_sponge": "Keccak sponge",
    "u_unpack": "SampleNTT、CBD、Decode / Decompress",
    "u_pack": "Compress、Encode",
    "u_polyram": "多项式 RAM 端口",
    "u_dbuf": "数据缓冲端口",
    "u_ctrl": "微码控制器",
    "top": "顶层连线",
    "u_csr": "AXI 寄存器",
}

# Instruction classes in program order of appearance, with fixed colours.
CLASS_COLORS = {
    "HINIT": "#c7c7c7", "HABS": "#1f77b4", "HABI": "#aec7e8", "HFIN": "#9edae5", "HSQZ": "#17becf",
    "SAMPLE": "#ff7f0e", "CBD": "#ffbb78", "DECODE": "#2ca02c", "ENCODE": "#98df8a",
    "NTT": "#d62728", "INTT": "#ff9896", "BMUL": "#9467bd", "ADD": "#c5b0d5", "SUB": "#8c564b",
    "CMP": "#e377c2", "CSEL": "#f7b6d2", "COPY": "#7f7f7f", "END": "#000000", "(start)": "#ffffff",
}


def log(msg=""):
    print(msg, flush=True)


def show(path):
    rel = os.path.relpath(path, ROOT)
    return path if rel.startswith("..") else rel


# ===========================================================================
# Running the engine
# ===========================================================================

def build():
    log("building the Go engine (build/pqc-testkit) ...")
    r = subprocess.run(["go", "build", "-o", ENGINE, "./cmd/pqc-testkit"], cwd=ROOT,
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("go build failed:\n" + r.stderr)
    log("building the Verilator model (make -C testbench/tvla_sim) ...")
    r = subprocess.run(["make", "-C", os.path.join(ROOT, "testbench", "tvla_sim")],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("Verilator build failed:\n" + r.stdout + r.stderr)


def run_engine(out_dir, args, sanity):
    """`pqc-testkit sca sim` into out_dir; returns its console output."""
    os.makedirs(out_dir, exist_ok=True)
    cmd = [ENGINE, "sca", "sim", "--op", args.op, "-n", str(args.traces),
           "--noise", "%g" % args.noise, "--noise-seed", str(args.noise_seed + (1 if sanity else 0)),
           "--keep-vcd", str(0 if sanity else args.keep_vcd), "-o", out_dir,
           "--sim", MODEL, "--rom", os.path.join(ROOT, "hdl", "core", "mlkem_ucode_rom.sv")]
    if args.jobs:
        cmd += ["-j", str(args.jobs)]
    if args.seed:
        cmd += ["--seed", args.seed]
    if args.invalid_random and args.op == "decaps":
        cmd.append("--invalid-random")
    if sanity:
        cmd.append("--fixed-vs-fixed")
    log("$ " + " ".join(show(c) if os.path.isabs(c) else c for c in cmd))
    t0 = time.time()
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    log("  %d traces simulated in %.0f s" % (args.traces, time.time() - t0))
    with open(os.path.join(out_dir, "engine.log"), "w") as f:
        f.write(r.stdout + r.stderr)
    if r.returncode != 0:
        raise SystemExit("pqc-testkit sca sim failed:\n" + r.stdout + r.stderr)
    return r.stdout


# ===========================================================================
# Analysis
# ===========================================================================

def instr_class(text):
    if not text:
        return "(start)"            # Start-up cycles before the first instruction.
    tok = text.split()[0]
    return re.sub(r"\d+$", "", tok) or "(start)"


def load_run(d):
    """Columns of tvla_t.csv / tvla_mean.csv as numpy arrays + meta data."""
    import numpy as np
    with open(os.path.join(d, "tvla_t.csv")) as f:
        rows = list(csv.reader(f))
    head, body = rows[0], rows[1:]
    cyc = np.array([int(r[0]) for r in body])
    pc = np.array([int(r[1]) for r in body])
    instr = [r[2] for r in body]
    t = {}
    for j, name in enumerate(head):
        if name.startswith("t_"):
            t[name[2:]] = np.array([float(r[j]) for r in body])
    mean = None
    mp = os.path.join(d, "tvla_mean.csv")
    if os.path.exists(mp):
        with open(mp) as f:
            mr = list(csv.reader(f))[1:]
        mean = (np.array([float(r[1]) for r in mr]), np.array([float(r[2]) for r in mr]))
    meta = {}
    sp = os.path.join(d, "summary.txt")
    if os.path.exists(sp):
        with open(sp) as f:
            txt = f.read()
        m = re.search(r"seed: ([0-9a-f]+)", txt)
        meta["seed"] = m.group(1) if m else ""
        m = re.search(r"traces: (\d+) \((\d+) fixed, (\d+) random\), cycles per trace: (\d+)\.\.(\d+), "
                      r"noise sigma: ([\d.]+)", txt)
        if m:
            meta.update(traces=int(m.group(1)), fixed=int(m.group(2)), random=int(m.group(3)),
                        cycles_min=int(m.group(4)), cycles_max=int(m.group(5)), noise=float(m.group(6)))
        meta["title"] = txt.splitlines()[0] if txt else ""
    return {"cycle": cyc, "pc": pc, "instr": instr, "cls": [instr_class(s) for s in instr],
            "t": t, "mean": mean, "meta": meta}


def runs_of(seq):
    """[(start, end_exclusive, value)] of consecutive equal values."""
    out, start = [], 0
    for i in range(1, len(seq) + 1):
        if i == len(seq) or seq[i] != seq[start]:
            out.append((start, i, seq[start]))
            start = i
    return out


def analyse(run):
    import numpy as np
    n = len(run["cycle"])
    floor = math.sqrt(2 * math.log(max(n, 2)))   # expected max|t| of pure noise over n cycles
    mods = {}
    for name, t in run["t"].items():
        a = np.abs(t)
        k = int(np.argmax(a))
        leaky = a > THRESHOLD
        first = int(np.argmax(leaky)) if leaky.any() else -1
        mods[name] = {"max_t": float(a[k]), "cycle": int(run["cycle"][k]), "leaky_pct": 100.0 * float(leaky.mean()),
                      "leaky_cycles": int(leaky.sum()), "first": first, "instruction": run["instr"][k]}
    tt = np.abs(run["t"]["total"])
    classes = {}
    for i, c in enumerate(run["cls"]):
        e = classes.setdefault(c, {"cycles": 0, "leaky": 0, "max_t": 0.0})
        e["cycles"] += 1
        e["leaky"] += int(tt[i] > THRESHOLD)
        e["max_t"] = max(e["max_t"], float(tt[i]))
    for e in classes.values():
        e["leaky_pct"] = 100.0 * e["leaky"] / e["cycles"]
    # Instruction instances: consecutive cycles with the same pc.
    inst = []
    for s, e, p in runs_of(list(run["pc"])):
        seg = tt[s:e]
        k = s + int(np.argmax(seg))
        inst.append({"pc": int(p), "instruction": run["instr"][s], "first": int(run["cycle"][s]),
                     "last": int(run["cycle"][e - 1]), "max_t": float(seg.max()),
                     "peak_cycle": int(run["cycle"][k]), "peak_t": float(run["t"]["total"][k]),
                     "leaky_pct": 100.0 * float((seg > THRESHOLD).mean())})
    top = sorted(inst, key=lambda x: -x["max_t"])[:15]
    return {"cycles": n, "noise_floor": floor, "modules": mods, "classes": classes, "top": top}


# ===========================================================================
# Figures
# ===========================================================================

def class_band(ax, run, ymin=0, ymax=1):
    """Colour band showing which microcode instruction class runs in each cycle."""
    used = []
    for s, e, c in runs_of(run["cls"]):
        ax.axvspan(run["cycle"][s], run["cycle"][e - 1] + 1, ymin=ymin, ymax=ymax,
                   color=CLASS_COLORS.get(c, "#ffffff"), lw=0)
        if c not in used:
            used.append(c)
    return used


def make_figures(run, sanity, an, an_s, fig_dir, title):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch
    import numpy as np

    os.makedirs(fig_dir, exist_ok=True)
    files = []
    cyc = run["cycle"]

    def save(fig, name):
        p = os.path.join(fig_dir, name)
        fig.savefig(p, dpi=150, bbox_inches="tight")
        plt.close(fig)
        files.append(name)

    def thresholds(ax, floor=None):
        ax.axhline(THRESHOLD, color="red", ls="--", lw=0.8)
        ax.axhline(-THRESHOLD, color="red", ls="--", lw=0.8)
        if floor:
            ax.axhline(floor, color="gray", ls=":", lw=0.8)
            ax.axhline(-floor, color="gray", ls=":", lw=0.8)

    # 1. t of the whole core, with the instruction band.
    fig, (ax, band) = plt.subplots(2, 1, figsize=(14, 6), sharex=True,
                                   gridspec_kw={"height_ratios": [5, 1]})
    ax.plot(cyc, run["t"]["total"], lw=0.4, color="#1f77b4")
    thresholds(ax)
    ax.set_ylabel("Welch t (total)")
    ax.set_title("%s — t per cycle, whole core (red: ±%.1f)" % (title, THRESHOLD))
    for x in an["top"][:5]:                     # Label the five strongest instructions.
        ax.annotate(x["instruction"], xy=(x["peak_cycle"], x["peak_t"]), fontsize=7, color="#444444",
                    xytext=(4, 4 if x["peak_t"] >= 0 else -10), textcoords="offset points")
    used = class_band(band, run)
    band.set_yticks([])
    band.set_xlabel("clock cycle (core clock %.0f MHz)" % CORE_MHZ)
    band.legend(handles=[Patch(color=CLASS_COLORS.get(c, "#fff"), label=c) for c in used],
                ncol=min(len(used), 10), fontsize=7, loc="upper center", bbox_to_anchor=(0.5, -0.9))
    save(fig, "01_t_total.png")

    # 2. t per module.
    mods = [m for m in MODULES if m in run["t"]]
    fig, axes = plt.subplots(len(mods), 1, figsize=(14, 1.6 * len(mods) + 1), sharex=True)
    for ax, m in zip(axes, mods):
        ax.plot(cyc, run["t"][m], lw=0.4)
        thresholds(ax)
        ax.set_ylabel(m, rotation=0, ha="right", va="center", fontsize=8)
        ax.text(0.995, 0.9, "max|t| %.1f, %.1f%% cycles > %.1f" % (an["modules"][m]["max_t"],
                an["modules"][m]["leaky_pct"], THRESHOLD), transform=ax.transAxes,
                ha="right", va="top", fontsize=7, bbox=dict(fc="white", alpha=0.8, lw=0))
    axes[0].set_title("%s — t per cycle and module" % title)
    axes[-1].set_xlabel("clock cycle")
    save(fig, "02_t_per_module.png")

    # 3. Mean simulated power.
    if run["mean"] is not None:
        mf, mr = run["mean"]
        fig, (a1, a2) = plt.subplots(2, 1, figsize=(14, 6), sharex=True)
        a1.plot(cyc[:len(mf)], mf, lw=0.4, label="fixed")
        a1.plot(cyc[:len(mr)], mr, lw=0.4, label="random", alpha=0.7)
        a1.set_ylabel("mean toggles / cycle")
        a1.legend(fontsize=8)
        a1.set_title("%s — simulated power (Hamming distance, whole core)" % title)
        a2.plot(cyc[:len(mf)], mr - mf, lw=0.4, color="#d62728")
        a2.set_ylabel("random − fixed")
        a2.set_xlabel("clock cycle")
        save(fig, "03_power_mean.png")

    # 4. Leakage per instruction class.
    cls = sorted(an["classes"].items(), key=lambda kv: -kv[1]["max_t"])
    names = [k for k, _ in cls]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(13, 0.35 * len(names) + 1.5))
    a1.barh(names, [v["max_t"] for _, v in cls], color=[CLASS_COLORS.get(k, "#999") for k in names])
    a1.axvline(THRESHOLD, color="red", ls="--", lw=0.8)
    a1.invert_yaxis()
    a1.set_xlabel("max |t| (whole core)")
    a2.barh(names, [v["leaky_pct"] for _, v in cls], color=[CLASS_COLORS.get(k, "#999") for k in names])
    a2.invert_yaxis()
    a2.set_xlabel("%% of the instruction's cycles with |t| > %.1f" % THRESHOLD)
    fig.suptitle("%s — leakage per microcode instruction class" % title)
    save(fig, "04_leakage_by_instruction.png")

    # 5. Leakage per module.
    ms = ["total"] + mods
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(13, 4))
    a1.barh(ms, [an["modules"][m]["max_t"] for m in ms])
    a1.axvline(THRESHOLD, color="red", ls="--", lw=0.8)
    a1.invert_yaxis()
    a1.set_xlabel("max |t|")
    a2.barh(ms, [an["modules"][m]["leaky_pct"] for m in ms])
    a2.invert_yaxis()
    a2.set_xlabel("%% of cycles with |t| > %.1f" % THRESHOLD)
    fig.suptitle("%s — leakage per module" % title)
    save(fig, "05_leakage_by_module.png")

    if sanity is not None:
        # 6. Sanity: fixed vs fixed.
        fig, ax = plt.subplots(figsize=(14, 4))
        ax.plot(sanity["cycle"], sanity["t"]["total"], lw=0.4, color="#2ca02c")
        thresholds(ax, an_s["noise_floor"])
        ax.set_ylim(-max(8, an_s["modules"]["total"]["max_t"] * 1.2), max(8, an_s["modules"]["total"]["max_t"] * 1.2))
        ax.set_title("Sanity check (fixed vs fixed) — red: ±%.1f, grey: expected noise maximum ±%.1f"
                     % (THRESHOLD, an_s["noise_floor"]))
        ax.set_xlabel("clock cycle")
        ax.set_ylabel("Welch t (total)")
        save(fig, "06_sanity_fixed_vs_fixed.png")

        # 7. Distribution of t: main vs sanity.
        fig, ax = plt.subplots(figsize=(9, 4.5))
        lim = max(10, float(np.abs(run["t"]["total"]).max()))
        bins = np.linspace(-lim, lim, 200)
        ax.hist(sanity["t"]["total"], bins=bins, alpha=0.6, label="fixed vs fixed (sanity)", color="#2ca02c")
        ax.hist(run["t"]["total"], bins=bins, alpha=0.6, label="fixed vs random", color="#1f77b4")
        ax.axvline(THRESHOLD, color="red", ls="--", lw=0.8)
        ax.axvline(-THRESHOLD, color="red", ls="--", lw=0.8)
        ax.set_yscale("log")
        ax.set_xlabel("Welch t (total), one value per cycle")
        ax.set_ylabel("cycles")
        ax.legend(fontsize=8)
        ax.set_title("Distribution of t over all cycles")
        save(fig, "07_t_distribution.png")
    return files


# ===========================================================================
# Report
# ===========================================================================

def write_report(run_dir, args_dict, run, sanity, an, an_s, figs, started):
    meta = run["meta"]
    L = []
    w = L.append
    leak = an["modules"]["total"]["max_t"] > THRESHOLD
    w("# 仿真级 TVLA 报告：ML-KEM-768 %s" % args_dict["op"].capitalize())
    w("")
    w("**结论：%s**" % ("检测到一阶泄漏（核心未做掩码，属于预期）" if leak else "未检测到超过阈值的泄漏"))
    if sanity is not None:
        ok = sanity_ok(an_s)
        w("")
        w("**流程自检（fixed vs fixed）：%s**　max|t| = %.2f，纯噪声下的预期最大值约 %.2f。"
          % ("通过" if ok else "未通过，结果不可信", an_s["modules"]["total"]["max_t"], an_s["noise_floor"]))
    w("")
    w("| 项目 | 值 |")
    w("| ---- | -- |")
    w("| 时间 | %s |" % started)
    w("| 运算 | %s%s |" % (args_dict["op"], "（random 组：随机密文，走隐式拒绝）" if args_dict.get("invalid_random") else ""))
    w("| trace 数 | %s（fixed %s，random %s） |" % (meta.get("traces"), meta.get("fixed"), meta.get("random")))
    w("| 每条 trace 周期数 | %s–%s（%.1f µs @ %.0f MHz） |" % (meta.get("cycles_min"), meta.get("cycles_max"),
                                                       (meta.get("cycles_max") or 0) / CORE_MHZ, CORE_MHZ))
    w("| 噪声 σ | %s 次翻转 |" % meta.get("noise"))
    w("| 功耗模型 | 每周期翻转比特数（汉明距离），核心内全部信号，按模块分别统计 |")
    w("| 阈值 | \\|t\\| > %.1f |" % THRESHOLD)
    w("| fixed 组 seed | `%s` |" % (meta.get("seed", "")[:32] + "…" if meta.get("seed") else ""))
    w("| 源码版本 | git %s |" % git_head())
    w("")
    w("## 1. 整个核心的 t 曲线")
    w("")
    w("![t total](figures/01_t_total.png)")
    w("")
    w("上图：每个周期的 Welch t 值，红色虚线为 ±%.1f。下方色带表示该周期正在执行的微码指令类别。" % THRESHOLD)
    w("")
    w("## 2. 各模块")
    w("")
    w("| 模块 | 说明 | max\\|t\\| | 出现周期 | 超阈值周期占比 | 首个超阈值周期 | max 处的指令 |")
    w("| ---- | ---- | -------: | -------: | -------------: | -------------: | ------------ |")
    for m in ["total"] + [x for x in MODULES if x in an["modules"]]:
        v = an["modules"][m]
        w("| %s | %s | %.1f | %d | %.2f%% | %s | %s |" % (m, MODULE_TEXT.get(m, ""), v["max_t"], v["cycle"],
                                                       v["leaky_pct"], v["first"] if v["first"] >= 0 else "—",
                                                       v["instruction"]))
    w("")
    w("![t per module](figures/02_t_per_module.png)")
    w("")
    w("![leakage per module](figures/05_leakage_by_module.png)")
    w("")
    w("同一根信号如果连接两个模块，会同时计入这两个模块，所以各模块之和不等于 total。")
    w("")
    w("## 3. 按微码指令")
    w("")
    w("| 指令类别 | 周期数 | max\\|t\\| | 超阈值周期占比 |")
    w("| -------- | -----: | -------: | -------------: |")
    for k, v in sorted(an["classes"].items(), key=lambda kv: -kv[1]["max_t"]):
        w("| %s | %d | %.1f | %.1f%% |" % (k, v["cycles"], v["max_t"], v["leaky_pct"]))
    w("")
    w("![leakage per instruction](figures/04_leakage_by_instruction.png)")
    w("")
    w("泄漏最强的 15 条指令（按 max|t|，total）：")
    w("")
    w("| max\\|t\\| | 周期范围 | pc | 指令 | 超阈值占比 |")
    w("| -------: | -------- | -: | ---- | ---------: |")
    for x in an["top"]:
        w("| %.1f | %d–%d | %d | `%s` | %.0f%% |" % (x["max_t"], x["first"], x["last"], x["pc"],
                                                 x["instruction"], x["leaky_pct"]))
    w("")
    if run["mean"] is not None:
        w("## 4. 平均功耗曲线")
        w("")
        w("![mean power](figures/03_power_mean.png)")
        w("")
        w("上：两组的平均翻转数（功耗形状，可以看出各运算阶段）；下：两组之差。")
        w("")
    if sanity is not None:
        w("## 5. 流程自检：fixed vs fixed")
        w("")
        w("两组用完全相同的输入，理论上没有任何泄漏。由于每条 trace 有 %d 个采样点（多重检验），"
          "纯噪声下 max|t| 的预期值约为 √(2 ln N) = %.2f，偶尔超过 4.5 是正常的；"
          "判定为通过的条件是 max|t| < 7 且超阈值周期占比 < 0.1%%。" % (an_s["cycles"], an_s["noise_floor"]))
        w("")
        w("| 模块 | max\\|t\\| | 超阈值周期数 |")
        w("| ---- | -------: | -----------: |")
        for m in ["total"] + [x for x in MODULES if x in an_s["modules"]]:
            v = an_s["modules"][m]
            w("| %s | %.2f | %d |" % (m, v["max_t"], v["leaky_cycles"]))
        w("")
        w("![sanity](figures/06_sanity_fixed_vs_fixed.png)")
        w("")
        w("![t distribution](figures/07_t_distribution.png)")
        w("")
    w("## 文件")
    w("")
    w("- `figures/`：上面的图（PNG）")
    w("- `raw/main/tvla_t.csv`：每个周期一行：cycle, pc, instruction, t_total, t_<模块>…")
    w("- `raw/main/tvla_mean.csv`：每个周期两组的平均翻转数")
    w("- `raw/main/vectors.csv`：本次使用的全部输入（index, class, input_hex）")
    w("- `raw/main/vcd/`：每组保留的 VCD 波形（用 GTKWave / Surfer 打开）")
    w("- `raw/main/summary.txt`、`raw/main/engine.log`：引擎原始输出")
    w("- `raw/sanity/`：fixed-vs-fixed 的同样文件")
    w("- `analysis.json`：本报告用到的统计数据")
    w("")
    w("说明见 `docs/TVLA_SIM.md`。仿真的汉明距离模型不包含布线电容、毛刺和耦合，"
      "真实芯片上的泄漏只会更多。这个核心没有做掩码，检测到泄漏是预期结果。")
    with open(os.path.join(run_dir, "report.md"), "w") as f:
        f.write("\n".join(L) + "\n")


def sanity_ok(an_s):
    return all(v["max_t"] < 7.0 and v["leaky_pct"] < 0.1 for v in an_s["modules"].values())


def git_head():
    try:
        return subprocess.run(["git", "-C", ROOT, "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True).stdout.strip() or "unknown"
    except OSError:
        return "unknown"


# ===========================================================================
# Main
# ===========================================================================

def analyse_and_report(run_dir, args_dict, started):
    main_dir = os.path.join(run_dir, "raw", "main")
    san_dir = os.path.join(run_dir, "raw", "sanity")
    run = load_run(main_dir)
    sanity = load_run(san_dir) if os.path.exists(os.path.join(san_dir, "tvla_t.csv")) else None
    an = analyse(run)
    an_s = analyse(sanity) if sanity is not None else None
    title = "ML-KEM-768 %s, %s traces" % (args_dict["op"].capitalize(), run["meta"].get("traces", "?"))
    log("plotting ...")
    figs = make_figures(run, sanity, an, an_s, os.path.join(run_dir, "figures"), title)
    with open(os.path.join(run_dir, "analysis.json"), "w") as f:
        json.dump({"params": args_dict, "meta": run["meta"], "main": an, "sanity": an_s}, f, indent=1)
    write_report(run_dir, args_dict, run, sanity, an, an_s, figs, started)

    log("")
    log("%-10s %8s %8s %10s  %s" % ("module", "max|t|", "cycle", "leaky", "instruction at max"))
    for m in ["total"] + [x for x in MODULES if x in an["modules"]]:
        v = an["modules"][m]
        log("%-10s %8.1f %8d %9.2f%%  %s" % (m, v["max_t"], v["cycle"], v["leaky_pct"], v["instruction"]))
    if an_s is not None:
        log("")
        log("sanity (fixed vs fixed): max|t| %.2f (noise maximum about %.2f): %s"
            % (an_s["modules"]["total"]["max_t"], an_s["noise_floor"], "PASS" if sanity_ok(an_s) else "FAIL"))
    log("")
    log("report:  %s" % show(os.path.join(run_dir, "report.md")))
    log("figures: %s/ (%s)" % (show(os.path.join(run_dir, "figures")), ", ".join(figs)))
    return 0 if (an_s is None or sanity_ok(an_s)) else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--op", choices=["decaps", "keygen"], default="decaps", help="operation (default decaps)")
    ap.add_argument("-n", "--traces", type=int, default=200,
                    help="traces per run, split equally between the classes (default 200)")
    ap.add_argument("--noise", type=float, default=1.0,
                    help="std. dev. of Gaussian noise per sample, in toggles (default 1.0)")
    ap.add_argument("--noise-seed", type=int, default=1, help="seed of the noise generator (default 1)")
    ap.add_argument("--seed", default="",
                    help="64-byte hex seed of the fixed input (decaps: key pair + ciphertext; keygen: d||z); "
                         "random if empty")
    ap.add_argument("--invalid-random", action="store_true",
                    help="decaps: random class uses random (invalid) ciphertexts -> implicit rejection path")
    ap.add_argument("--keep-vcd", type=int, default=1, help="VCDs kept per class in the main run (default 1)")
    ap.add_argument("-j", "--jobs", type=int, default=0, help="parallel simulations (default: all CPUs)")
    ap.add_argument("--no-sanity", action="store_true", help="skip the fixed-vs-fixed check")
    ap.add_argument("--out", default=DEFAULT_OUT, help="results directory (default sim_result/tvla)")
    ap.add_argument("--replot", metavar="RUN_DIR", help="only redo figures and report of an existing run")
    args = ap.parse_args()

    try:
        import matplotlib  # noqa: F401
        import numpy  # noqa: F401
    except ImportError:
        raise SystemExit("matplotlib / numpy missing: run with `uv run python scripts/tvla_sim.py` "
                         "(they are in pyproject.toml), or pip install matplotlib numpy")

    if args.replot:
        with open(os.path.join(args.replot, "analysis.json")) as f:
            params = json.load(f)["params"]
        return analyse_and_report(args.replot, params,
                                  datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "（重新绘图）")

    started = datetime.datetime.now()
    name = "%s_%s%s" % (started.strftime("%Y%m%d_%H%M%S"), args.op,
                        "_invalid" if args.invalid_random and args.op == "decaps" else "")
    run_dir = os.path.join(args.out, name)
    log("simulated TVLA, ML-KEM-768 %s, %d traces, noise %.2f -> %s"
        % (args.op, args.traces, args.noise, show(run_dir)))
    build()
    log("")
    log("main test: fixed vs random")
    run_engine(os.path.join(run_dir, "raw", "main"), args, sanity=False)
    if not args.no_sanity:
        log("")
        log("sanity check: fixed vs fixed")
        run_engine(os.path.join(run_dir, "raw", "sanity"), args, sanity=True)
    params = {"op": args.op, "traces": args.traces, "noise": args.noise, "noise_seed": args.noise_seed,
              "seed": args.seed, "invalid_random": args.invalid_random, "keep_vcd": args.keep_vcd}
    return analyse_and_report(run_dir, params, started.strftime("%Y-%m-%d %H:%M:%S"))


if __name__ == "__main__":
    sys.exit(main())
