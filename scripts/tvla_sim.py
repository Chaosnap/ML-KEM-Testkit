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
     instruction;
  5. Decaps: the three intermediate expressions of the decryption
     m' = Compress_1(v - INTT(s^T o u)) are compared ("points"):
       P1  s^T o u          unreduced products (DSP registers)
       P2  (s^T o u) mod q  reduced products, base-case terms, accumulator
       P3  v - (s^T u mod q) the subtraction
     Each point is a group of registers (POINTS below). Its sample in a cycle
     is the sum of the Hamming distances of those registers; the cycles of
     the instructions that compute it (P1/P2: the three BMUL s7 * s6, P3: the
     SUB) are cut out and spliced in time order, and the t-test is evaluated
     on that spliced trace.

The core is unmasked, so leakage IS expected: the result shows where and
when the unprotected baseline leaks, not that it is secure.

Usage:
  uv run python scripts/tvla_sim.py                          # Decaps, 200 traces + sanity
  uv run python scripts/tvla_sim.py -n 1000 --noise 2        # more traces, more noise
  uv run python scripts/tvla_sim.py --op keygen
  uv run python scripts/tvla_sim.py --invalid-random         # random class: random ciphertexts
  uv run python scripts/tvla_sim.py --model hw               # Hamming-weight instead of -distance
  uv run python scripts/tvla_sim.py --replot sim_result/tvla/<run>   # redo figures only

Results go to sim_result/tvla/<YYYYmmdd_HHMMSS>_<op>/:
  report.md        the report (open in VS Code / any Markdown viewer)
  figures/*.png    t-traces, per-module t, mean power, leakage per instruction, sanity,
                   points P1/P2/P3 (windows, spliced t, comparison, power)
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

# Decaps decryption, m' = Compress_1(v - INTT(sum_i s_i o u_i)), microcode
# (gen_mlkem_ucode.py): for i in 0..2 { DECODE u_i -> s6; NTT s6;
# DECODE s_i -> s7; BMUL s7 * s6 -> s3 (acc if i > 0) }; INTT s3;
# DECODE v -> s6; SUB s6 - s3 -> s3. The three points of interest are the
# registers of mlkem_poly_alu / mlkem_modmul that hold each expression, and
# the windows are the instructions computing it (regex on the ROM text).
# Each part is passed to the engine as its own group (<point>_<part>); the
# point itself is the union of its parts.
POINTS = [
    {"key": "P1", "expr": "ŝᵀ∘û", "tex": r"$\hat{s}^T\circ\hat{u}$", "what": "未约减的乘积（模 q 之前）",
     "window": r"^BMUL s7 \* s6", "window_text": "解密的 3 条 BMUL s7 * s6（i = 0, 1, 2）",
     "parts": [
         ("p1", ["u_alu.u_mul0.p1", "u_alu.u_mul1.p1"],
          "两个乘法器 DSP 的 M 寄存器：a·b，24 bit。mul0 交替保存 â0·b̂0 与 (â0+â1)(b̂0+b̂1)，"
          "mul1 交替保存 â1·b̂1 与 (â1b̂1 mod q)·γ"),
         ("p2", ["u_alu.u_mul0.p2", "u_alu.u_mul1.p2"], "DSP 的 P 寄存器：同一乘积晚一拍"),
     ]},
    {"key": "P2", "expr": "(ŝᵀ∘û) mod q", "tex": r"$(\hat{s}^T\circ\hat{u})\ \mathrm{mod}\ q$", "what": "Barrett 约减后的结果",
     "window": r"^BMUL s7 \* s6", "window_text": "同 P1",
     "parts": [
         ("r", ["u_alu.u_mul0.r", "u_alu.u_mul1.r"], "Barrett 输出 r = a·b mod q（12 bit × 2）"),
         ("basemul", ["u_alu.p00", "u_alu.p11", "u_alu.p11d", "u_alu.p00d*", "u_alu.s1", "u_alu.h1",
                      "u_alu.h1d*", "u_alu.h0"],
          "基乘组合（全部已模 q）：a0b0、a1b1 及其延迟链，s1、h1 = a0b1+a1b0，h0 = a0b0+a1b1γ"),
         ("acc", ["u_alu.wb", "u_alu.wbd", "u_alu.rb_din"],
          "累加结果 Σᵢ ŝᵢ∘ûᵢ mod q（每个字 = 两个系数拼接的 24 bit）及 RAM 写回数据"),
     ]},
    {"key": "P3", "expr": "v − (sᵀu mod q)", "tex": r"$v - (s^T u\ \mathrm{mod}\ q)$", "what": "解密的减法",
     "window": r"^SUB ", "window_text": "SUB s6 - s3 -> s3（s6 = v，s3 = INTT 结果）",
     "parts": [
         ("ls", ["u_alu.ls"], "减法结果寄存器 ls = msub(v, w)，两个系数拼接 24 bit（读后第 4 拍）"),
         ("rb_din", ["u_alu.rb_din"], "RAM 写回数据，ls 的下一拍"),
     ]},
]
POINT_COLORS = {"P1": "#d62728", "P2": "#1f77b4", "P3": "#2ca02c"}


def point_groups():
    """--group arguments of the engine: every part, then every point."""
    out = []
    for pt in POINTS:
        sigs = []
        for name, signals, _ in pt["parts"]:
            out.append("%s_%s=%s" % (pt["key"], name, ",".join(signals)))
            sigs += signals
        out.append("%s=%s" % (pt["key"], ",".join(sigs)))
    return out


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
           "--sim", MODEL, "--rom", os.path.join(ROOT, "hdl", "core", "mlkem_ucode_rom.sv"),
           "--model", args.model]
    if args.op == "decaps":
        for g in point_groups():
            cmd += ["--group", g]
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
    mean, gstat = None, {}
    mp = os.path.join(d, "tvla_mean.csv")
    if os.path.exists(mp):
        with open(mp) as f:
            rows = list(csv.reader(f))
        mh, mr = rows[0], rows[1:]
        mean = (np.array([float(r[1]) for r in mr]), np.array([float(r[2]) for r in mr]))
        for j, name in enumerate(mh):        # {mean,std}_{fixed,random}_<group>
            m = re.match(r"(mean|std)_(fixed|random)_(.+)$", name)
            if m:
                gstat.setdefault(m.group(3), {})[m.group(1) + "_" + m.group(2)] = \
                    np.array([float(r[j]) for r in mr])
    meta = {}
    sp = os.path.join(d, "summary.txt")
    if os.path.exists(sp):
        with open(sp) as f:
            txt = f.read()
        m = re.search(r"power model: (\w+)", txt)
        meta["model"] = m.group(1) if m else "hd"
        meta["bits"] = {g: int(b) for g, b in re.findall(r"^(\S+)\s+(\d+)\s+[\d.]+\s+\d+\s+[\d.]+%", txt, re.M)}
        m = re.search(r"seed: ([0-9a-f]+)", txt)
        meta["seed"] = m.group(1) if m else ""
        m = re.search(r"traces: (\d+) \((\d+) fixed, (\d+) random\), cycles per trace: (\d+)\.\.(\d+), "
                      r"noise sigma: ([\d.]+)", txt)
        if m:
            meta.update(traces=int(m.group(1)), fixed=int(m.group(2)), random=int(m.group(3)),
                        cycles_min=int(m.group(4)), cycles_max=int(m.group(5)), noise=float(m.group(6)))
        meta["title"] = txt.splitlines()[0] if txt else ""
    return {"cycle": cyc, "pc": pc, "instr": instr, "cls": [instr_class(s) for s in instr],
            "t": t, "mean": mean, "gstat": gstat, "meta": meta}


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


def point_windows(run, pattern):
    """[(start, end_exclusive)] row ranges of the instruction runs whose text matches."""
    rx = re.compile(pattern)
    return [(s, e) for s, e, _ in runs_of(list(run["pc"])) if rx.search(run["instr"][s])]


def signal_variance(run, group):
    """Data-dependent variance of a group per cycle: variance of the random class minus the
    variance of the added noise (the simulation itself is noiseless). None if not available."""
    import numpy as np
    st = run["gstat"].get(group, {})
    if "std_random" not in st:
        return None
    return np.maximum(st["std_random"] ** 2 - run["meta"].get("noise", 0.0) ** 2, 0.0)


def analyse_points(run):
    """Leakage of P1/P2/P3 on their spliced windows; None if the run has no point groups.

    Two measures per point (and per register group of a point):
      TVLA   fixed-vs-random Welch t on the spliced trace: detects leakage;
      sigma  the data-dependent standard deviation of the point's sample (random class):
             the signal of the SNR, used to rank the points. With little noise, |t| of a
             noiseless simulation saturates at about sqrt(n/2) |z| for ANY data-dependent
             group and cannot rank them; sigma does not depend on the fixed input."""
    import numpy as np
    if not all(pt["key"] in run["t"] for pt in POINTS):
        return None
    bits = run["meta"].get("bits", {})
    out = []
    for pt in POINTS:
        wins = point_windows(run, pt["window"])
        if not wins:
            return None
        idx = np.concatenate([np.arange(s, e) for s, e in wins])
        bounds = np.cumsum([0] + [e - s for s, e in wins])   # window starts in the spliced trace

        def stats(group):
            a = np.abs(run["t"][group][idx])
            k = int(np.argmax(a))
            w = int(np.searchsorted(bounds, k, side="right") - 1)
            leaky = a > THRESHOLD
            r = {"max_t": float(a[k]), "t_at_max": float(run["t"][group][idx[k]]), "sample": k,
                 "cycle": int(run["cycle"][idx[k]]), "window": w, "offset": int(k - bounds[w]),
                 "mean_abs_t": float(a.mean()), "leaky_pct": 100.0 * float(leaky.mean()),
                 "leaky": int(leaky.sum()), "bits": bits.get(group), "sigma_rms": None, "sigma_max": None}
            var = signal_variance(run, group)
            if var is not None:
                v = var[idx]
                r["sigma_rms"] = float(np.sqrt(v.mean()))
                r["sigma_max"] = float(np.sqrt(v.max()))
                r["sigma_max_cycle"] = int(run["cycle"][idx[int(np.argmax(v))]])
                if r["bits"]:
                    r["var_per_bit"] = float(v.mean()) / r["bits"]
            return r

        e = {"key": pt["key"], "expr": pt["expr"], "what": pt["what"], "window_text": pt["window_text"],
             "samples": int(len(idx)), "noise_floor": math.sqrt(2 * math.log(max(len(idx), 2))),
             "windows": [{"first": int(run["cycle"][s]), "last": int(run["cycle"][e - 1]),
                          "pc": int(run["pc"][s]), "instruction": run["instr"][s]} for s, e in wins],
             "bounds": [int(b) for b in bounds]}
        e.update(stats(pt["key"]))
        e["parts"] = []
        for name, signals, desc in pt["parts"]:
            p = {"name": name, "signals": signals, "desc": desc}
            p.update(stats("%s_%s" % (pt["key"], name)))
            e["parts"].append(p)
        out.append(e)
    return out


def splice(run, values, pt):
    """values (one per cycle) cut to the windows of point pt and concatenated."""
    import numpy as np
    return np.concatenate([values[run["cycle"].searchsorted(w["first"]):
                                  run["cycle"].searchsorted(w["last"]) + 1] for w in pt["windows"]])


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


def make_figures(run, sanity, an, an_s, fig_dir, title, pts=None):
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

    if pts:
        point_figures(run, pts, title, save, thresholds)
    return files


def point_window_label(pt, i):
    w = pt["windows"][i]
    if len(pt["windows"]) > 1:
        return "BMUL i=%d (cycles %d–%d)" % (i, w["first"], w["last"])
    return "%s (cycles %d–%d)" % (w["instruction"].split()[0], w["first"], w["last"])


def point_figures(run, pts, title, save, thresholds):
    """Figures 08-11: the three decryption expressions P1/P2/P3."""
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch
    import numpy as np

    cyc = run["cycle"]
    tex = {pt["key"]: pt["tex"] for pt in POINTS}
    model = "Hamming weight" if run["meta"].get("model") == "hw" else "Hamming distance"

    # 8. Where the windows are: t of each point's registers over the decryption.
    end = max(w["last"] for pt in pts for w in pt["windows"]) + 300
    sel = cyc <= end
    fig, axes = plt.subplots(4, 1, figsize=(14, 8.5), sharex=True,
                             gridspec_kw={"height_ratios": [3, 3, 3, 0.8]})
    for ax, pt in zip(axes, pts):
        ax.plot(cyc[sel], run["t"][pt["key"]][sel], lw=0.4, color=POINT_COLORS[pt["key"]])
        for i, w in enumerate(pt["windows"]):
            ax.axvspan(w["first"], w["last"] + 1, color=POINT_COLORS[pt["key"]], alpha=0.12, lw=0)
            ax.text((w["first"] + w["last"]) / 2, 0.97, "window %d" % i if len(pt["windows"]) > 1 else "window",
                    transform=ax.get_xaxis_transform(), ha="center", va="top", fontsize=7)
        thresholds(ax)
        ax.set_ylabel("%s\n%s" % (pt["key"], tex[pt["key"]]), rotation=0, ha="right", va="center", fontsize=9)
    axes[0].set_title("%s — t of the registers of each point over the decryption; shaded: the cycles "
                      "kept for that point" % title)
    used = class_band(axes[3], {"cycle": cyc[sel], "cls": [c for c, k in zip(run["cls"], sel) if k]})
    axes[3].set_yticks([])
    axes[3].set_xlabel("clock cycle")
    axes[3].legend(handles=[Patch(color=CLASS_COLORS.get(c, "#fff"), label=c) for c in used],
                   ncol=len(used), fontsize=7, loc="upper center", bbox_to_anchor=(0.5, -1.6))
    save(fig, "08_points_windows.png")

    # 9. Spliced t of each point.
    fig, axes = plt.subplots(len(pts), 1, figsize=(14, 2.6 * len(pts) + 0.6))
    for ax, pt in zip(axes, pts):
        t = splice(run, run["t"][pt["key"]], pt)
        ax.plot(np.arange(len(t)), t, lw=0.5, color=POINT_COLORS[pt["key"]])
        thresholds(ax)
        for i, b in enumerate(pt["bounds"][:-1]):
            if i:
                ax.axvline(b, color="black", lw=0.8, ls="-.")
            ax.text(b + 3, 0.97, point_window_label(pt, i), transform=ax.get_xaxis_transform(),
                    ha="left", va="top", fontsize=7)
        ax.plot([pt["sample"]], [pt["t_at_max"]], "kx")
        ax.set_xlim(0, len(t))
        ax.set_ylabel("%s  %s" % (pt["key"], tex[pt["key"]]), fontsize=9)
        ax.text(0.995, 0.03, "max|t| %.1f (x), mean|t| %.1f, %.0f%% samples > %.1f, %s bits"
                % (pt["max_t"], pt["mean_abs_t"], pt["leaky_pct"], THRESHOLD, pt["bits"]),
                transform=ax.transAxes, ha="right", va="bottom", fontsize=8, bbox=dict(fc="white", alpha=0.85, lw=0))
    axes[0].set_title("%s — Welch t of each point on its spliced trace (%s)" % (title, model))
    axes[-1].set_xlabel("sample of the spliced trace (one sample per core clock cycle)")
    fig.tight_layout()
    save(fig, "09_points_t.png")

    # 10. Comparison: leakage strength (sigma) and TVLA detection.
    unit = "bits at 1" if run["meta"].get("model") == "hw" else "toggles"
    have_sigma = all(pt["sigma_rms"] is not None for pt in pts)
    keys = [pt["key"] for pt in pts]
    cols = [POINT_COLORS[k] for k in keys]
    x = np.arange(len(pts))
    xt = ["%s\n%s" % (pt["key"], tex[pt["key"]]) for pt in pts]
    fig, axs = plt.subplots(1, 3 if have_sigma else 1, figsize=(16 if have_sigma else 6, 5),
                            gridspec_kw={"width_ratios": [1, 1, 1.5]} if have_sigma else None)
    axs = np.atleast_1d(axs)
    if have_sigma:
        a = axs[0]
        a.bar(x - 0.2, [pt["sigma_rms"] for pt in pts], 0.4, color=cols, label="RMS over the window")
        a.bar(x + 0.2, [pt["sigma_max"] for pt in pts], 0.4, color=cols, alpha=0.45, hatch="//",
              label="max over the window")
        for i, pt in enumerate(pts):
            a.text(i - 0.2, pt["sigma_rms"], "%.1f" % pt["sigma_rms"], ha="center", va="bottom", fontsize=8)
            a.text(i + 0.2, pt["sigma_max"], "%.1f" % pt["sigma_max"], ha="center", va="bottom", fontsize=8)
        a.set_xticks(x)
        a.set_xticklabels(xt)
        a.set_ylabel("data-dependent std. dev. (%s)" % unit)
        a.legend(fontsize=8)
        a.set_title("(a) leakage strength σ_data  (ranking)")
    a = axs[1] if have_sigma else axs[0]
    a.bar(x - 0.2, [pt["max_t"] for pt in pts], 0.4, color=cols, label="max |t|")
    a.bar(x + 0.2, [pt["mean_abs_t"] for pt in pts], 0.4, color=cols, alpha=0.45, hatch="//",
          label="mean |t|")
    for i, pt in enumerate(pts):
        a.text(i - 0.2, pt["max_t"], "%.1f" % pt["max_t"], ha="center", va="bottom", fontsize=8)
        a.text(i + 0.2, pt["mean_abs_t"], "%.1f" % pt["mean_abs_t"], ha="center", va="bottom", fontsize=8)
    a.axhline(THRESHOLD, color="red", ls="--", lw=0.8)
    a.set_xticks(x)
    a.set_xticklabels(xt)
    a.set_ylabel("|t|")
    a.legend(fontsize=8)
    a.set_title("(b) TVLA fixed vs random  (detection)")
    if have_sigma:
        names, vals, pc = [], [], []
        for pt in pts:
            for p in pt["parts"]:
                names.append("%s %s (%s bit)" % (pt["key"], p["name"], p["bits"]))
                vals.append(p["sigma_rms"])
                pc.append(POINT_COLORS[pt["key"]])
        a = axs[2]
        a.barh(names, vals, color=pc)
        for i, v in enumerate(vals):
            a.text(v, i, " %.1f" % v, va="center", fontsize=8)
        a.invert_yaxis()
        a.set_xlabel("RMS σ_data in the point's windows (%s)" % unit)
        a.set_title("(c) σ_data per register group")
    fig.suptitle("%s — which expression leaks most (%s model)" % (title, model))
    fig.tight_layout()
    save(fig, "10_points_compare.png")

    # 11. Simulated power of each point, spliced: fixed vs random mean, data-dependent spread.
    if have_sigma:
        fig, axes = plt.subplots(len(pts), 1, figsize=(14, 2.6 * len(pts) + 0.6))
        for ax, pt in zip(axes, pts):
            st = run["gstat"][pt["key"]]
            f, r = splice(run, st["mean_fixed"], pt), splice(run, st["mean_random"], pt)
            sd = np.sqrt(splice(run, signal_variance(run, pt["key"]), pt))
            n = np.arange(len(f))
            ax.fill_between(n, r - sd, r + sd, color=POINT_COLORS[pt["key"]], alpha=0.2, lw=0,
                            label="random ± σ_data")
            ax.plot(n, r, lw=0.6, label="random mean", color=POINT_COLORS[pt["key"]])
            ax.plot(n, f, lw=0.6, label="fixed", color="black")
            for b in pt["bounds"][1:-1]:
                ax.axvline(b, color="black", lw=0.8, ls="-.")
            ax.set_xlim(0, len(f))
            ax.set_ylabel("%s\n%s" % (pt["key"], unit), fontsize=9)
            ax.legend(fontsize=7, loc="upper right", ncol=3)
        axes[0].set_title("%s — simulated power of each point on its spliced trace (%s)" % (title, model))
        axes[-1].set_xlabel("sample of the spliced trace")
        fig.tight_layout()
        save(fig, "11_points_power.png")


# ===========================================================================
# Report
# ===========================================================================

def write_report(run_dir, args_dict, run, sanity, an, an_s, figs, started, pts=None, pts_s=None):
    meta = run["meta"]
    L = []
    w = L.append
    sec = [0]

    def h2(text):
        sec[0] += 1
        w("## %d. %s" % (sec[0], text))
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
    if meta.get("model") == "hw":
        w("| 功耗模型 | 汉明重量（HW）：每周期末为 1 的比特数，核心内全部信号，按模块 / 运算式分别统计 |")
    else:
        w("| 功耗模型 | 汉明距离（HD）：每周期翻转的比特数，核心内全部信号，按模块 / 运算式分别统计 |")
    w("| 阈值 | \\|t\\| > %.1f |" % THRESHOLD)
    w("| fixed 组 seed | `%s` |" % (meta.get("seed", "")[:32] + "…" if meta.get("seed") else ""))
    w("| 源码版本 | git %s |" % git_head())
    w("")
    if pts:
        write_points_section(w, h2, run, pts, pts_s, meta, figs)
    h2("整个核心的 t 曲线")
    w("")
    w("![t total](figures/01_t_total.png)")
    w("")
    w("上图：每个周期的 Welch t 值，红色虚线为 ±%.1f。下方色带表示该周期正在执行的微码指令类别。" % THRESHOLD)
    w("")
    h2("各模块")
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
    h2("按微码指令")
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
        h2("平均功耗曲线")
        w("")
        w("![mean power](figures/03_power_mean.png)")
        w("")
        w("上：两组的平均翻转数（功耗形状，可以看出各运算阶段）；下：两组之差。")
        w("")
    if sanity is not None:
        h2("流程自检：fixed vs fixed")
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
        if pts_s:
            w("三个运算式在各自拼接窗口上的自检：")
            w("")
            w("| 点 | 采样点数 | max\\|t\\| | 纯噪声预期最大值 | 超阈值样本数 | σ_data RMS（应接近 0） |")
            w("| -- | -------: | -------: | ---------------: | -----------: | ---------------------: |")
            for pt in pts_s:
                w("| %s | %d | %.2f | %.2f | %d | %s |" % (pt["key"], pt["samples"], pt["max_t"], pt["noise_floor"],
                                                        pt["leaky"], "%.2f" % pt["sigma_rms"]
                                                        if pt["sigma_rms"] is not None else "—"))
            w("")
        w("![sanity](figures/06_sanity_fixed_vs_fixed.png)")
        w("")
        w("![t distribution](figures/07_t_distribution.png)")
        w("")
    w("## 文件")
    w("")
    w("- `figures/`：上面的图（PNG）")
    w("- `raw/main/tvla_t.csv`：每个周期一行：cycle, pc, instruction, t_total, t_<模块>…, t_P1…（运算式的寄存器组）")
    w("- `raw/main/tvla_mean.csv`：每个周期两组的平均样本值（total 与各运算式）")
    w("- `raw/main/vectors.csv`：本次使用的全部输入（index, class, input_hex）")
    w("- `raw/main/vcd/`：每组保留的 VCD 波形（用 GTKWave / Surfer 打开）")
    w("- `raw/main/summary.txt`、`raw/main/engine.log`：引擎原始输出")
    w("- `raw/sanity/`：fixed-vs-fixed 的同样文件")
    w("- `analysis.json`：本报告用到的统计数据")
    w("")
    w("说明见 `docs/TVLA_SIM.md`。仿真的汉明距离 / 重量模型不包含布线电容、毛刺和耦合，"
      "真实芯片上的泄漏只会更多。这个核心没有做掩码，检测到泄漏是预期结果。")
    with open(os.path.join(run_dir, "report.md"), "w") as f:
        f.write("\n".join(L) + "\n")


def rank_points(pts):
    """Points from most to least leaky: by sigma_data (RMS), or max|t| if sigma is missing."""
    if all(pt.get("sigma_rms") is not None for pt in pts):
        return sorted(pts, key=lambda p: -p["sigma_rms"])
    return sorted(pts, key=lambda p: -p["max_t"])


def write_points_section(w, h2, run, pts, pts_s, meta, figs):
    """Report section: which of P1/P2/P3 leaks most, with model and splicing method."""
    hw = meta.get("model") == "hw"
    unit = "个为 1 的比特" if hw else "次翻转"
    have_sigma = all(pt.get("sigma_rms") is not None for pt in pts)
    rank = rank_points(pts)
    half = (meta.get("traces") or 0) // 2
    h2("解密中三个运算式的泄漏对比（P1 / P2 / P3）")
    w("")
    w("解密 m' = Compress₁(v − INTT(ŝᵀ∘û)) 中的三个中间运算式：")
    w("")
    w("| 点 | 运算式 | 含义 | 截取的指令窗口 |")
    w("| -- | ------ | ---- | -------------- |")
    for pt in pts:
        w("| %s | %s | %s | %s |" % (pt["key"], pt["expr"], pt["what"], pt["window_text"]))
    w("")
    best = rank[0]
    if have_sigma:
        w("**结论：泄漏最强的是 %s（%s）：数据相关功耗的标准差 σ_data = %.1f %s（窗口内 RMS），"
          "是 %s。三个运算式在 fixed-vs-random TVLA 中都明显泄漏（max|t| 分别为 %s，均远超 %.1f）。**"
          % (best["key"], best["expr"], best["sigma_rms"], unit,
             "、".join("%s 的 %.2f 倍" % (p["key"], best["sigma_rms"] / max(p["sigma_rms"], 1e-9)) for p in rank[1:]),
             "、".join("%s %.1f" % (p["key"], p["max_t"]) for p in pts), THRESHOLD))
    else:
        w("**结论（只有 TVLA，没有 σ_data 数据，排序不可靠，见下文）：max|t| 最大的是 %s（%s）。**"
          % (best["key"], best["expr"]))
    w("")
    w("### 方法")
    w("")
    if hw:
        w("**功耗模型：汉明重量（HW）。** 每个周期末寄存器中为 1 的比特数 HW(x)（`--model hw`）。"
          "默认的汉明距离模型更接近 CMOS 硬件，见 `docs/TVLA_SIM.md`。")
    else:
        w("**功耗模型：汉明距离（HD）。** 仿真没有真实功耗，用 RTL 波形（VCD）计算：寄存器在时钟沿"
          "从旧值 x 变成新值 x′ 时翻转的比特数 HD(x, x′) = HW(x ⊕ x′)，它正比于 CMOS 寄存器和连线的"
          "动态功耗，是硬件实现的标准模型（汉明重量模型可用 `--model hw` 切换，适合软件实现）。")
    w("")
    w("**拼接方法（三个层次）：**")
    w("")
    w("1. **比特拼接。** 每个 RAM 字是两个 12 bit 系数拼成的 24 bit 字 {f[2w+1], f[2w]}；"
      "两个乘法器（u_mul0、u_mul1）的同名寄存器和一个运算式的所有流水线副本，也都当作一个长比特向量 X_P。"
      "HD 对比特可加，所以 HD(X_P) = Σ_寄存器 HD(寄存器)。每个点由哪些寄存器组成见下面的分项表。")
    w("2. **周期样本。** 点 P 在第 c 个时钟周期的样本为 L_P(c) = Σ_{r∈P} %s + N(0, σ²)，"
      "σ = %s；每个周期一个样本，相当于一个只对准这些寄存器的理想探头。"
      % ("HW(r(c))" if hw else "HD(r(c−1), r(c))", meta.get("noise")))
    w("3. **时间拼接。** 同一组寄存器在其他指令中保存的是别的值（例如 NTT 里乘法器算 ζ·b，"
      "下图中阴影以外的部分），所以只截取真正计算该运算式的周期：P1、P2 取解密的 3 条 "
      "`BMUL s7 * s6`（i = 0, 1, 2，每条 %d 个周期），按时间顺序首尾相接，得到 %d 个采样点的 trace；"
      "P3 取 `SUB s6 - s3 -> s3` 的 %d 个周期。核心是常数时间的（每条 trace 都是 %s 个周期），"
      "窗口在所有 trace 中位于同一周期位置，不需要额外对齐；所有统计量都在拼接后的 trace 上逐点计算。"
      % (pts[0]["windows"][0]["last"] - pts[0]["windows"][0]["first"] + 1, pts[0]["samples"],
         pts[2]["samples"], meta.get("cycles_max")))
    w("")
    w("流水线中同一周期的不同寄存器保存的是不同系数字的值（BMUL 每 2 个周期读一个字，"
      "乘积在读后第 5 拍进入 DSP 的 M 寄存器，结果在第 21 拍写回），所以一个样本是几个字的叠加，"
      "这与真实功耗一致。fixed 组的密文固定（û、v 固定），random 组每条 trace 一个新的合法密文；"
      "两组的私钥 ŝ 相同。")
    w("")
    w("![windows](figures/08_points_windows.png)")
    w("")
    w("**两个指标：**")
    w("")
    w("- **TVLA（检测）**：fixed vs random 的 Welch t，|t| > %.1f 即判定该点泄漏。" % THRESHOLD)
    w("- **σ_data（泄漏强度，用于排序）**：random 组每个采样点的标准差去掉所加噪声，"
      "σ_data(c) = √(Var_random(L_P(c)) − σ²)。仿真本身没有噪声，random 组的方差完全来自数据"
      "（随机密文的 û、v），也就是 SNR 的分子（信号）。σ_data 越大，在同样的测量噪声下越容易被 TVLA 检出、"
      "被 CPA 攻击（相关系数 ρ = √(SNR / (1 + SNR))）。它只用 random 组，不依赖 fixed 组选了哪个输入。")
    w("")
    w("**为什么不直接用 max|t| 排序：** 仿真没有噪声时，fixed 组的方差只有所加的噪声 σ²，"
      "t ≈ √(N/2)·z，其中 z = (x_fixed − μ_random)/σ_data 只表示这一个固定输入在随机分布中偏离多远，"
      "与该运算式泄漏多少无关。所以任何与数据有关的寄存器组，平均 |t| 都约为 √(N/2)·0.8 = %.1f"
      "（本次 P1/P2/P3 为 %s），max|t| 的先后会随固定输入改变（换 fixed 输入重跑就能看到）。"
      "只有当噪声远大于 σ_data 时，t ≈ √(N/4)·z·σ_data/σ 才与 σ_data 成正比，"
      "所以按 σ_data 排序就是“真实测量噪声下 TVLA 的排序”。"
      % (math.sqrt(half) * 0.8, " / ".join("%.1f" % p["mean_abs_t"] for p in pts)))
    w("")
    w("### 结果")
    w("")
    if have_sigma:
        w("| 点 | 运算式 | 比特数 | 采样点数 | **σ_data RMS** | σ_data 最大（周期） | σ²/bit | TVLA max\\|t\\| | 平均 \\|t\\| | 超阈值样本占比 |")
        w("| -- | ------ | -----: | -------: | -------------: | ------------------- | -----: | ------------: | ---------: | -------------: |")
        for pt in rank:
            w("| %s | %s | %s | %d | **%.1f** | %.1f（%d） | %s | %.1f | %.1f | %.1f%% |"
              % (pt["key"], pt["expr"], pt["bits"], pt["samples"], pt["sigma_rms"], pt["sigma_max"],
                 pt["sigma_max_cycle"], "%.3f" % pt["var_per_bit"] if pt.get("var_per_bit") else "—",
                 pt["max_t"], pt["mean_abs_t"], pt["leaky_pct"]))
        w("")
        w("按 σ_data 从大到小排列；σ_data 的单位是%s / 周期。σ²/bit = 窗口内平均方差 ÷ 比特数，"
          "即每个寄存器比特贡献的数据相关方差；比特以 1/2 概率独立翻转时为 0.25（比特之间相关时可以略高）。" % unit)
        vpb = [pt.get("var_per_bit") for pt in pts]
        if all(vpb):
            same = max(vpb) / min(vpb) < 1.5
            w("")
            w("按每比特看，P1/P2/P3 的 σ²/bit 为 %s，%s" % (
                " / ".join("%.3f" % v for v in vpb),
                "相差不到 1.5 倍、都接近随机数据的 0.25：每个寄存器比特泄漏得差不多，"
                "排序主要取决于硬件里保存该运算式的寄存器比特数（P2 包括 Barrett 输出、Karatsuba 基乘的中间项"
                "及其延迟链、累加器，共 %s bit）。" % pts[1]["bits"] if same else
                "每比特的泄漏也明显不同。"))
    else:
        w("| 点 | 运算式 | 比特数 | 采样点数 | TVLA max\\|t\\| | 平均 \\|t\\| | 超阈值样本占比 |")
        w("| -- | ------ | -----: | -------: | ------------: | ---------: | -------------: |")
        for pt in rank:
            w("| %s | %s | %s | %d | %.1f | %.1f | %.1f%% |" % (pt["key"], pt["expr"], pt["bits"], pt["samples"],
                                                          pt["max_t"], pt["mean_abs_t"], pt["leaky_pct"]))
    w("")
    w("![compare](figures/10_points_compare.png)")
    w("")
    w("![spliced t](figures/09_points_t.png)")
    w("")
    w("上图：每个点在拼接 trace 上的 Welch t（× 为 max|t|，点划线为窗口之间的拼接处）。")
    w("")
    if "11_points_power.png" in figs:
        w("![power](figures/11_points_power.png)")
        w("")
        w("上图：拼接 trace 上各点的功耗（%s）。黑线：fixed 组；彩色线和阴影：random 组的均值 ± σ_data。"
          "阴影越宽，该运算式的功耗随数据变化越大，即泄漏越强。" % ("为 1 的比特数" if hw else "翻转数"))
        w("")
    w("分项（每个点里各组寄存器单独统计）：")
    w("")
    w("| 点 | 寄存器组 | 信号 | 比特数 | σ_data RMS | σ²/bit | TVLA max\\|t\\| | 说明 |")
    w("| -- | -------- | ---- | -----: | ---------: | -----: | ------------: | ---- |")
    for pt in pts:
        for p in pt["parts"]:
            w("| %s | %s | `%s` | %s | %s | %s | %.1f | %s |"
              % (pt["key"], p["name"], ", ".join(p["signals"]), p["bits"],
                 "%.1f" % p["sigma_rms"] if p["sigma_rms"] is not None else "—",
                 "%.3f" % p["var_per_bit"] if p.get("var_per_bit") else "—", p["max_t"], p["desc"]))
    w("")
    if have_sigma:
        parts = sorted(((pt["key"], p) for pt in pts for p in pt["parts"]), key=lambda kp: -kp[1]["sigma_rms"])
        w("单个寄存器组中泄漏最强的是 %s 的 %s（σ_data = %.1f），最弱的是 %s 的 %s（σ_data = %.1f）。"
          % (parts[0][0], parts[0][1]["name"], parts[0][1]["sigma_rms"],
             parts[-1][0], parts[-1][1]["name"], parts[-1][1]["sigma_rms"]))
        w("")
    w("比较时注意：σ_data 随参与翻转的比特数增大（独立比特时约按 √比特数），所以“点”这一行比较的是"
      "该运算式在硬件里的全部寄存器（真实芯片上泄漏的就是这些），分项表可以按单个寄存器组比较"
      "（例如 P1 的 p1、P2 的 r、P3 的 ls）。")
    w("")


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
    pts = analyse_points(run)
    pts_s = analyse_points(sanity) if sanity is not None else None
    title = "ML-KEM-768 %s, %s traces" % (args_dict["op"].capitalize(), run["meta"].get("traces", "?"))
    log("plotting ...")
    figs = make_figures(run, sanity, an, an_s, os.path.join(run_dir, "figures"), title, pts)
    with open(os.path.join(run_dir, "analysis.json"), "w") as f:
        json.dump({"params": args_dict, "meta": run["meta"], "main": an, "sanity": an_s,
                   "points": pts, "points_sanity": pts_s}, f, indent=1)
    write_report(run_dir, args_dict, run, sanity, an, an_s, figs, started, pts, pts_s)

    log("")
    log("%-10s %8s %8s %10s  %s" % ("module", "max|t|", "cycle", "leaky", "instruction at max"))
    for m in ["total"] + [x for x in MODULES if x in an["modules"]]:
        v = an["modules"][m]
        log("%-10s %8.1f %8d %9.2f%%  %s" % (m, v["max_t"], v["cycle"], v["leaky_pct"], v["instruction"]))
    if pts:
        log("")
        log("decryption expressions (spliced windows, %s model), most leaky first:"
            % run["meta"].get("model", "hd").upper())
        log("%-4s %-18s %6s %8s %10s %8s %8s %8s" % ("", "expression", "bits", "samples", "sigma_data",
                                                    "max|t|", "mean|t|", "leaky"))
        for pt in rank_points(pts):
            sig = "%10.1f" % pt["sigma_rms"] if pt["sigma_rms"] is not None else "%10s" % "-"
            log("%-4s %-18s %6s %8d %s %8.1f %8.1f %7.1f%%" % (pt["key"], pt["expr"], pt["bits"], pt["samples"],
                                                            sig, pt["max_t"], pt["mean_abs_t"], pt["leaky_pct"]))
        log("most leaky (by sigma_data, the data-dependent std. dev.): %s %s"
            % (rank_points(pts)[0]["key"], rank_points(pts)[0]["expr"]))
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
    ap.add_argument("--model", choices=["hd", "hw"], default="hd",
                    help="power model: hd = Hamming distance (bits toggling per cycle, default), "
                         "hw = Hamming weight (bits at 1)")
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
        try:
            with open(os.path.join(args.replot, "analysis.json")) as f:
                params = json.load(f)["params"]
        except FileNotFoundError:           # Run interrupted before the report: from the directory name.
            base = os.path.basename(os.path.normpath(args.replot))
            params = {"op": "keygen" if "_keygen" in base else "decaps", "invalid_random": "_invalid" in base,
                      "model": "hw" if base.endswith("_hw") else "hd"}
        return analyse_and_report(args.replot, params,
                                  datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "（重新绘图）")

    started = datetime.datetime.now()
    name = "%s_%s%s%s" % (started.strftime("%Y%m%d_%H%M%S"), args.op,
                          "_invalid" if args.invalid_random and args.op == "decaps" else "",
                          "_hw" if args.model == "hw" else "")
    run_dir = os.path.join(args.out, name)
    log("simulated TVLA, ML-KEM-768 %s, %d traces, %s model, noise %.2f -> %s"
        % (args.op, args.traces, args.model.upper(), args.noise, show(run_dir)))
    build()
    log("")
    log("main test: fixed vs random")
    run_engine(os.path.join(run_dir, "raw", "main"), args, sanity=False)
    if not args.no_sanity:
        log("")
        log("sanity check: fixed vs fixed")
        run_engine(os.path.join(run_dir, "raw", "sanity"), args, sanity=True)
    params = {"op": args.op, "traces": args.traces, "noise": args.noise, "noise_seed": args.noise_seed,
              "seed": args.seed, "invalid_random": args.invalid_random, "keep_vcd": args.keep_vcd,
              "model": args.model}
    return analyse_and_report(run_dir, params, started.strftime("%Y-%m-%d %H:%M:%S"))


if __name__ == "__main__":
    sys.exit(main())
