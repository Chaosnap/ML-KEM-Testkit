// Vanilla JS, no build step, no framework -- fetch() against the stdlib
// backend in web_gui.py. Kept deliberately simple: this is a setup/smoke
// test tool, not an app.

const $ = (id) => document.getElementById(id);

async function api(method, path, body) {
  const opts = { method };
  if (body !== undefined) {
    opts.headers = { "Content-Type": "application/json" };
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || ("HTTP " + res.status));
  return data;
}

function setStatus(el, state, text) {
  el.className = "status " + state;
  el.querySelector(".dot").className = "dot " + state;
  el.querySelector("span:last-child").textContent = text;
}

function setKV(containerId, obj) {
  const rows = document.querySelectorAll("#" + containerId + " .kv");
  rows.forEach((row) => {
    const key = row.querySelector(".k").textContent.toLowerCase();
    const val = obj && obj[key] !== undefined && obj[key] !== null ? obj[key] : "—";
    row.querySelector(".v").textContent = typeof val === "object" ? JSON.stringify(val) : String(val);
  });
}

// --- unified top status strip: FPGA / Scope / Model / Dataset / Acquisition -----
function updateChip(chipId, state, label) {
  const el = $(chipId);
  if (!el) return;
  el.querySelector(".dot").className = "dot " + state;
  el.lastChild.textContent = label;  // trailing text node after the <span class="dot">
}

function updateModelChip(moduleName) {
  updateChip("chip-model", moduleName ? "ok" : "idle", moduleName ? `Model: ${moduleName}` : "Model not loaded");
}

// --- tabs --------------------------------------------------------------------
document.querySelectorAll(".tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    $("tab-" + btn.dataset.tab).classList.add("active");
  });
});

// --- dynamic step numbering ---------------------------------------------------
// Fixes "1. Dataset" jumping straight to "3. Run Analysis" when "2. Leakage
// Model" is hidden for a TVLA dataset: every <h2>/<h3> with a <span
// class="step-num"> gets numbered by VISIBLE order within its tab panel,
// recomputed whenever a visibility toggle runs, instead of a number baked
// into the HTML that goes stale the moment a step is conditionally hidden.
function renumberSteps(tabPanelId) {
  const panel = $(tabPanelId);
  if (!panel) return;
  let n = 0;
  panel.querySelectorAll(".step-num").forEach((span) => {
    const row = span.closest(".row");
    const visible = row ? (row.offsetParent !== null && getComputedStyle(row).display !== "none") : true;
    span.textContent = visible ? String(++n) : "";
  });
}

// --- collapsible bottom console (replaces the old always-open Log card) --------
$("console-toggle").addEventListener("click", () => {
  $("console-bar").classList.toggle("open");
});

// --- log polling ---------------------------------------------------------------
let logCursor = 0;
async function pollLog() {
  try {
    const { lines, next } = await api("GET", "/api/log?since=" + logCursor);
    if (lines.length) {
      const pre = $("log");
      pre.textContent += (pre.textContent ? "\n" : "") + lines.join("\n");
      pre.scrollTop = pre.scrollHeight;
      logCursor = next;
    }
  } catch (e) { /* server not up yet; ignore and retry */ }
  setTimeout(pollLog, 1000);
}

function appendLog(line) {
  const pre = $("log");
  pre.textContent += (pre.textContent ? "\n" : "") + line;
  pre.scrollTop = pre.scrollHeight;
}

// --- FPGA tab ------------------------------------------------------------------
async function refreshPorts() {
  try {
    const { ports } = await api("GET", "/api/fpga/ports");
    const sel = $("fpga-port");
    sel.innerHTML = "";
    ports.forEach((p) => {
      const opt = document.createElement("option");
      opt.value = p.device;
      opt.textContent = p.device + " — " + p.description;
      sel.appendChild(opt);
    });
    appendLog(ports.length ? ("found " + ports.length + " serial port(s)") : "no serial ports found");
  } catch (e) { appendLog("[error] " + e.message); }
}

async function fpgaConnect() {
  const port = $("fpga-port").value || $("fpga-port").options[0]?.value;
  const baud = $("fpga-baud").value;
  if (!port) { appendLog("[error] no serial port selected"); return; }
  setStatus($("fpga-status"), "warn", "connecting…");
  try {
    const { info } = await api("POST", "/api/fpga/connect", { port, baud });
    setStatus($("fpga-status"), "ok", "connected — " + port);
    setKV("fpga-info", info);
    $("fpga-connect").disabled = true;
    $("fpga-disconnect").disabled = false;
    appendLog("connected: " + JSON.stringify(info));
    updateChip("chip-fpga", "ok", "FPGA connected");
    acqRefreshInfo();
  } catch (e) {
    setStatus($("fpga-status"), "bad", "connection failed");
    appendLog("[error] " + e.message);
    updateChip("chip-fpga", "bad", "FPGA failed");
  }
}

async function fpgaDisconnect() {
  await api("POST", "/api/fpga/disconnect", {}).catch((e) => appendLog("[error] " + e.message));
  setStatus($("fpga-status"), "idle", "disconnected");
  setKV("fpga-info", null);
  $("fpga-connect").disabled = false;
  $("fpga-disconnect").disabled = true;
  updateChip("chip-fpga", "idle", "FPGA disconnected");
  acqRefreshInfo();
}

async function fpgaPreflight() {
  const tbody = $("fpga-preflight-table").querySelector("tbody");
  tbody.innerHTML = "";
  try {
    const { checks } = await api("POST", "/api/fpga/preflight", {});
    checks.forEach((c) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${c.name}</td><td class="${c.ok ? "pass" : "fail"}">${c.ok ? "PASS" : "FAIL"}</td><td>${c.detail}</td>`;
      tbody.appendChild(tr);
    });
  } catch (e) { appendLog("[error] " + e.message); }
}

async function fpgaReadReg() {
  const name = $("fpga-reg-name").value;
  try {
    const r = await api("POST", "/api/fpga/read_reg", { name });
    $("fpga-reg-result").textContent = `${r.name} = ${r.hex} (${r.value})`;
  } catch (e) { appendLog("[error] " + e.message); }
}

async function fpgaWriteReg() {
  const name = $("fpga-reg-name").value;
  const value = $("fpga-reg-value").value;
  try {
    const r = await api("POST", "/api/fpga/write_reg", { name, value });
    $("fpga-reg-result").textContent = `wrote ${r.name} <- 0x${r.value.toString(16)}`;
  } catch (e) { appendLog("[error] " + e.message); }
}

// hexStr -> classic hex dump: "0000: a5 5a 00 11 ...  .Z.."
function hexDump(hexStr, baseOffset) {
  const bytes = hexStr.match(/.{1,2}/g) || [];
  const lines = [];
  for (let i = 0; i < bytes.length; i += 16) {
    const chunk = bytes.slice(i, i + 16);
    const addr = (baseOffset + i).toString(16).padStart(4, "0");
    const hex = chunk.join(" ").padEnd(47, " ");
    const ascii = chunk.map((b) => {
      const c = parseInt(b, 16);
      return c >= 32 && c < 127 ? String.fromCharCode(c) : ".";
    }).join("");
    lines.push(`${addr}: ${hex} ${ascii}`);
  }
  return lines.join("\n") || "(empty)";
}

async function fpgaBufRead() {
  const offset = $("fpga-buf-read-offset").value;
  const length = $("fpga-buf-read-len").value;
  try {
    const r = await api("POST", "/api/fpga/read_data", { offset, length });
    $("fpga-buf-read-result").textContent = hexDump(r.hex, r.offset);
  } catch (e) {
    $("fpga-buf-read-result").textContent = "error";
    appendLog("[error] " + e.message);
  }
}

async function fpgaBufWrite() {
  const offset = $("fpga-buf-write-offset").value;
  const hex = $("fpga-buf-write-data").value;
  try {
    const r = await api("POST", "/api/fpga/write_data", { offset, hex });
    $("fpga-buf-write-result").textContent = `wrote ${r.length} byte(s) at 0x${r.offset.toString(16)}`;
  } catch (e) { appendLog("[error] " + e.message); }
}

async function fpgaOpRun() {
  const dk_hex = $("fpga-op-dk").value.trim();
  const ct_hex = $("fpga-op-ct").value.trim();
  const resend_dk = $("fpga-op-resend-dk").checked;
  $("fpga-op-result").textContent = "running…";
  try {
    const r = await api("POST", "/api/fpga/decaps", { dk_hex, ct_hex, resend_dk });
    $("fpga-op-result").textContent =
      `${r.describe}\ncycles: ${r.cycles}\nstatus: 0x${r.status.toString(16)}  error_code: ${r.error_code}\n` +
      `output (${r.output_hex.length / 2} bytes): ${r.output_hex || "(none)"}`;
    appendLog("decaps: " + r.describe);
  } catch (e) {
    $("fpga-op-result").textContent = "error";
    appendLog("[error] " + e.message);
  }
}

// --- Scope tab -----------------------------------------------------------------
function refreshResourcePreview() {
  const ip = $("scope-ip").value.trim();
  $("scope-resource-preview").textContent = "resource: TCPIP0::" + ip + "::INSTR  (edit config.py's SCOPE_RESOURCE for a different template)";
}

let placeholderIp = "";

function checkPlaceholder() {
  const ip = $("scope-ip").value.trim();
  const warn = $("scope-placeholder-warn");
  warn.textContent = (ip === placeholderIp)
    ? "⚠ still the config.py placeholder (RFC 5737 TEST-NET-1) — edit it before connecting"
    : "";
}

async function scopeConnect() {
  const body = {
    ip: $("scope-ip").value, channel: $("scope-channel").value, dialect: $("scope-dialect").value,
    points: $("scope-points").value, timeout_ms: $("scope-timeout").value,
  };
  setStatus($("scope-status"), "warn", "connecting…");
  try {
    const { info } = await api("POST", "/api/scope/connect", body);
    setStatus($("scope-status"), "ok", "connected");
    setKV("scope-info", info);
    $("scope-connect").disabled = true;
    $("scope-disconnect").disabled = false;
    appendLog("scope connected: " + JSON.stringify(info));
    updateChip("chip-scope", "ok", "Scope connected");
    acqRefreshInfo();
  } catch (e) {
    setStatus($("scope-status"), "bad", "connection failed");
    appendLog("[error] " + e.message);
    updateChip("chip-scope", "bad", "Scope failed");
  }
}

async function scopeDisconnect() {
  await api("POST", "/api/scope/disconnect", {}).catch((e) => appendLog("[error] " + e.message));
  setStatus($("scope-status"), "idle", "disconnected");
  setKV("scope-info", null);
  $("scope-connect").disabled = false;
  $("scope-disconnect").disabled = true;
  updateChip("chip-scope", "idle", "Scope disconnected");
  acqRefreshInfo();
}

async function scopeListResources() {
  const tbody = $("scope-resources-table").querySelector("tbody");
  tbody.innerHTML = "";
  try {
    const { resources } = await api("GET", "/api/scope/resources");
    if (!resources.length) appendLog("no VISA resources found on this machine");
    resources.forEach((r) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${r.resource}</td><td>${r.idn}</td>`;
      tbody.appendChild(tr);
    });
  } catch (e) { appendLog("[error] " + e.message); }
}

async function scopeSaveConfig() {
  try {
    await api("POST", "/api/scope/save_config", {
      ip: $("scope-ip").value, channel: $("scope-channel").value, dialect: $("scope-dialect").value,
    });
    appendLog("saved to config.py");
  } catch (e) { appendLog("[error] " + e.message); }
}

// --- Advanced Model Settings (collapsed, inside CPA Analysis) --------------------
// Model-plugin discovery/load/self-test. The actually-used target/rprev for
// running analysis lives in #an-target/#an-rprev (Leakage Model section
// below) -- loading a module here only refreshes what Advanced Model
// Settings shows (doc/Q/K) and self-test; it does not itself change the
// Leakage Model selection, so there is exactly one place that decides what
// an analysis run actually uses (requirement: no duplicate/inconsistent
// config between two UI areas).
async function modelRefreshList() {
  try {
    const state = await api("GET", "/api/model/list");
    const sel = $("model-select");
    sel.innerHTML = "";
    state.modules.forEach((name) => {
      const opt = document.createElement("option");
      opt.value = name; opt.textContent = name;
      if (name === (state.loaded || state.default_module)) opt.selected = true;
      sel.appendChild(opt);
    });
    if (!state.modules.length) appendLog("[warn] no CPA model modules found in this folder");
    if (state.loaded) {
      setStatus($("model-status"), "ok", "loaded — " + state.loaded);
      $("model-selftest").disabled = false;
    }
    updateModelChip(state.loaded);
    return state;
  } catch (e) { appendLog("[error] " + e.message); return null; }
}

async function modelLoad() {
  const name = $("model-select").value;
  if (!name) { appendLog("[error] no model module selected"); return; }
  setStatus($("model-status"), "warn", "loading…");
  try {
    const info = await api("POST", "/api/model/load", { module: name });
    setStatus($("model-status"), "ok", "loaded — " + info.module);
    setKV("model-info", { module: info.module, doc: info.doc, q: info.q, k: info.k });
    $("model-selftest").disabled = false;
    appendLog("loaded model module: " + info.module);
    updateModelChip(info.module);
  } catch (e) {
    setStatus($("model-status"), "bad", "load failed");
    appendLog("[error] " + e.message);
  }
}

async function modelSelftest() {
  $("model-selftest-result").textContent = "running…";
  try {
    const r = await api("POST", "/api/model/selftest", {});
    const label = r.ok === true ? "PASS" : r.ok === false ? "FAIL" : "N/A";
    $("model-selftest-result").textContent = `[${label}] ${r.detail}`;
  } catch (e) {
    $("model-selftest-result").textContent = "error";
    appendLog("[error] " + e.message);
  }
}

async function modelSaveDefault() {
  // Explicit action only -- never triggered by just picking a preset or
  // running an analysis (requirement: no silent global default changes).
  const target = $("an-target").value;
  const rprev = $("an-rprev").value;
  if (!target || !rprev) { appendLog("[error] pick a leakage model first"); return; }
  try {
    await api("POST", "/api/model/save_default", { target, rprev });
    appendLog(`saved CPA_TARGET=${target} CPA_RPREV=${rprev} to config.py as the CLI default`);
  } catch (e) { appendLog("[error] " + e.message); }
}

// --- Trace Acquisition tab: Step 1, Generate Dataset (offline) -------------------
async function acqRefreshInfo() {
  try {
    const info = await api("GET", "/api/acquire/info");
    $("acq-hw-warn").textContent = info.hardware_ready ? "" :
      "⚠ Acquire Real Traces needs both the board (FPGA Connection tab) and the scope " +
      "(Oscilloscope Setup tab) connected first.";
    return info;
  } catch (e) { appendLog("[error] " + e.message); return null; }
}

function fmtDate(unix) {
  return unix ? new Date(unix * 1000).toLocaleString() : "—";
}

function genUpdateFieldVisibility() {
  const mode = $("gen-mode").value;
  $("gen-cpa-fields").style.display = mode === "cpa" ? "" : "none";
  $("gen-tvla-fields").style.display = mode === "tvla" ? "" : "none";
}

async function genRefreshInputSets() {
  try {
    const { input_sets } = await api("GET", "/api/dataset/input_sets");
    const tbody = $("input-sets-table").querySelector("tbody");
    tbody.innerHTML = "";
    input_sets.forEach((s) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${s.filename}</td><td>${s.mode}</td><td>${s.strategy || ""}</td>` +
        `<td>${s.n_traces}</td><td>${fmtDate(s.created_unix)}</td>`;
      tbody.appendChild(tr);
    });
    const sel = $("acq-input-set");
    const prev = sel.value;
    sel.innerHTML = "";
    if (!input_sets.length) {
      sel.innerHTML = '<option value="">— generate a dataset above first —</option>';
    }
    input_sets.forEach((s) => {
      const opt = document.createElement("option");
      opt.value = s.path;
      opt.textContent = `${s.filename}  [${s.mode}, ${s.strategy || "n/a"}, n=${s.n_traces}]`;
      if (s.path === prev) opt.selected = true;
      sel.appendChild(opt);
    });
    return input_sets;
  } catch (e) { appendLog("[error] " + e.message); return []; }
}

let genPollTimer = null;

async function genPoll(jobId) {
  try {
    const job = await api("GET", "/api/dataset/generate/status?job_id=" + jobId);
    if (job.status === "running") {
      setStatus($("gen-status"), "warn", job.message || "generating…");
      genPollTimer = setTimeout(() => genPoll(jobId), 500);
      return;
    }
    $("gen-start").disabled = false;
    if (job.status === "done") {
      setStatus($("gen-status"), "ok", "done");
      const r = job.result;
      const note = r.preview_is_exact
        ? "(this preview IS exactly what Step 2 will capture)"
        : "(preview only -- Step 2 regenerates for real and will NOT match this preview byte-for-byte; " +
          "only dk is seed-reproducible, see the recipe's dk_hash)";
      $("gen-result").textContent =
        `saved: ${r.path}\nmode: ${r.mode}  strategy: ${r.strategy || "—"}  n: ${r.n_generated}\n` +
        `dk_hash: ${r.dk_hash}\npreview ${note}:\n  ` + r.preview.join("\n  ");
      appendLog("dataset generated: " + r.path);
      genRefreshInputSets();
    } else if (job.status === "error") {
      setStatus($("gen-status"), "bad", "error");
      $("gen-result").textContent = "error: " + job.error;
      appendLog("[error] dataset generation: " + job.error);
    }
  } catch (e) {
    appendLog("[error] " + e.message);
    $("gen-start").disabled = false;
  }
}

async function genStart() {
  if (genPollTimer) { clearTimeout(genPollTimer); genPollTimer = null; }
  const body = {
    mode: $("gen-mode").value, strategy: $("gen-strategy").value,
    poly: $("gen-poly").value, pair: $("gen-pair").value,
    n_traces: $("gen-n").value, seed: $("gen-seed").value,
    invalid_random: $("gen-invalid-random").checked,
  };
  $("gen-start").disabled = true;
  $("gen-result").textContent = "—";
  setStatus($("gen-status"), "warn", "starting…");
  try {
    const r = await api("POST", "/api/dataset/generate/start", body);
    appendLog(`started dataset generation job ${r.job_id} (seed ${r.seed.slice(0, 16)}...)`);
    genPoll(r.job_id);
  } catch (e) {
    setStatus($("gen-status"), "bad", "failed to start");
    appendLog("[error] " + e.message);
    $("gen-start").disabled = false;
  }
}

// --- Trace Acquisition tab: Step 2, Acquire Real Traces (hardware only) ---------
async function acqRefreshDatasets() {
  try {
    const { datasets } = await api("GET", "/api/datasets");
    const tbody = $("acq-datasets-table").querySelector("tbody");
    tbody.innerHTML = "";
    datasets.forEach((d) => {
      const tr = document.createElement("tr");
      const src = d.simulated ? "synthetic" : "REAL";
      const resumeBtn = d.resumable
        ? `<button class="btn ghost" data-resume="${d.path}">Resume (${d.n_completed}/${d.n_traces_planned})</button>`
        : "";
      tr.innerHTML = `<td>${d.filename}</td><td>${d.mode}</td><td>${src}</td>` +
        `<td>${d.n_completed}/${d.n_traces_planned}</td><td>${d.strategy || ""}</td><td>${resumeBtn}</td>`;
      tbody.appendChild(tr);
    });
    tbody.querySelectorAll("button[data-resume]").forEach((btn) => {
      btn.addEventListener("click", () => {
        $("acq-resume-file").value = btn.dataset.resume;
        acqUpdateResumeVisibility();
        document.querySelector('[data-tab="acquire"]').scrollIntoView({ behavior: "smooth" });
      });
    });
    // Resume dropdown: only real-hardware, incomplete, recipe-bearing files.
    const resumeSel = $("acq-resume-file");
    const prevResume = resumeSel.value;
    resumeSel.innerHTML = '<option value="">— start fresh from the input set above —</option>';
    datasets.filter((d) => d.resumable && !d.simulated).forEach((d) => {
      const opt = document.createElement("option");
      opt.value = d.path;
      opt.textContent = `${d.filename}  (${d.n_completed}/${d.n_traces_planned} traces)`;
      if (d.path === prevResume) opt.selected = true;
      resumeSel.appendChild(opt);
    });
    acqUpdateResumeVisibility();
    return datasets;
  } catch (e) { appendLog("[error] " + e.message); return []; }
}

function acqUpdateResumeVisibility() {
  const resuming = !!$("acq-resume-file").value;
  $("acq-input-set").disabled = resuming;
}

let acqPollTimer = null;
let acqCurrentJob = null;

async function acqPoll(jobId) {
  try {
    const job = await api("GET", "/api/acquire/status?job_id=" + jobId);
    const pct = job.total ? Math.round((100 * job.progress) / job.total) : 0;
    $("acq-progress-fill").style.width = pct + "%";
    if (job.status === "running") {
      setStatus($("acq-status"), "warn", `running: ${job.progress}/${job.total} — ${job.message}`);
      updateChip("chip-acq", "warn", `Acquiring ${job.progress}/${job.total}`);
      acqPollTimer = setTimeout(() => acqPoll(jobId), 500);
      return;
    }
    $("acq-start").disabled = false;
    $("acq-stop").disabled = true;
    acqCurrentJob = null;
    if (job.status === "done") {
      const r = job.result;
      const stopped = r.stopped_early;
      setStatus($("acq-status"), stopped ? "warn" : "ok", stopped ? "stopped (partial)" : "done");
      updateChip("chip-acq", "ok", "Acquisition idle");
      $("acq-result").textContent =
        `file: ${r.path}\nmode: ${r.mode}  [REAL HARDWARE]${stopped ? "  -- STOPPED EARLY, resumable" : ""}\n` +
        `traces: ${r.n_completed}/${r.n_traces_planned}  ok: ${(100 * (r.ok_fraction || 0)).toFixed(1)}%` +
        (r.n_fixed !== undefined ? `\nfixed: ${r.n_fixed}  random: ${r.n_random}` : "");
      appendLog((stopped ? "acquisition stopped (partial): " : "acquisition finished: ") + r.path);
      acqRefreshDatasets();
      anRefreshDatasets();
    } else if (job.status === "error") {
      setStatus($("acq-status"), "bad", "error");
      updateChip("chip-acq", "bad", "Acquisition failed");
      $("acq-result").textContent = "error: " + job.error;
      appendLog("[error] acquisition: " + job.error);
    }
  } catch (e) {
    appendLog("[error] " + e.message);
    $("acq-start").disabled = false;
    $("acq-stop").disabled = true;
    acqCurrentJob = null;
  }
}

async function acqStart() {
  if (acqPollTimer) { clearTimeout(acqPollTimer); acqPollTimer = null; }
  const resumeFile = $("acq-resume-file").value;
  const body = resumeFile
    ? { resume_file: resumeFile, samples: $("acq-samples").value, resend_dk_every: $("acq-resend-dk").value }
    : { input_set: $("acq-input-set").value, samples: $("acq-samples").value, resend_dk_every: $("acq-resend-dk").value };
  if (!resumeFile && !body.input_set) { appendLog("[error] no input set selected -- generate one in Step 1 first"); return; }
  $("acq-start").disabled = true;
  $("acq-progress-fill").style.width = "0%";
  $("acq-result").textContent = "—";
  setStatus($("acq-status"), "warn", "starting…");
  try {
    const r = await api("POST", "/api/acquire/start", body);
    acqCurrentJob = r.job_id;
    $("acq-stop").disabled = false;
    appendLog(`started acquisition job ${r.job_id}` + (resumeFile ? " (resuming)" : ""));
    acqPoll(r.job_id);
  } catch (e) {
    setStatus($("acq-status"), "bad", "failed to start");
    appendLog("[error] " + e.message);
    $("acq-start").disabled = false;
  }
}

async function acqStop() {
  if (!acqCurrentJob) return;
  $("acq-stop").disabled = true;
  try {
    await api("POST", "/api/acquire/stop", { job_id: acqCurrentJob });
    appendLog("stop requested for acquisition job " + acqCurrentJob);
  } catch (e) { appendLog("[error] " + e.message); }
}

async function acqCaptureOne() {
  $("capture-one-result").textContent = "capturing…";
  try {
    const r = await api("POST", "/api/acquire/capture_one", {});
    renderLineChart($("waveform-preview-chart"), [
      { label: "capture", color: "#38bdf8", points: r.samples.map((v, i) => [i, v]) },
    ], { height: 200 });
    $("capture-one-result").textContent =
      `${r.describe}\n${r.samples.length} samples` +
      (r.sample_rate_hz ? `, ${r.sample_rate_hz.toExponential(3)} Sa/s` : ", sample rate unknown");
    appendLog("capture-one-trace: " + r.describe);
  } catch (e) {
    $("capture-one-result").textContent = "error";
    $("waveform-preview-chart").innerHTML = '<div class="chart-empty">error</div>';
    appendLog("[error] " + e.message);
  }
}

// --- CPA / TVLA Analysis tab ------------------------------------------------------
let anDatasets = [];
let anPresets = [];
let anPollTimer = null;

async function anRefreshDatasets() {
  try {
    const { datasets } = await api("GET", "/api/datasets");
    anDatasets = datasets;
    const sel = $("an-dataset");
    const prev = sel.value;
    sel.innerHTML = "";
    datasets.forEach((d) => {
      const opt = document.createElement("option");
      opt.value = d.path;
      const src = d.simulated ? "synthetic" : "REAL";
      opt.textContent = `${d.filename}  [${d.mode}, ${src}, ${d.n_completed} traces]`;
      if (d.path === prev) opt.selected = true;
      sel.appendChild(opt);
    });
    anShowDatasetInfo();
    rvRefreshRawDatasetSelect();
  } catch (e) { appendLog("[error] " + e.message); }
}

function anShowDatasetInfo() {
  const path = $("an-dataset").value;
  const d = anDatasets.find((x) => x.path === path);
  if (!d) {
    $("an-dataset-info").textContent = "—";
    updateChip("chip-dataset", "idle", "Dataset not ready");
  } else {
    const badge = d.simulated ? "SYNTHETIC — software validation only, not a real-hardware result"
                               : "REAL HARDWARE";
    $("an-dataset-info").textContent =
      `mode: ${d.mode}  [${badge}]\ntraces: ${d.n_completed}  ok: ${(100 * (d.ok_fraction || 0)).toFixed(1)}%  ` +
      `strategy: ${d.strategy || "—"}  captured: ${fmtDate(d.created_unix)}` +
      (d.n_fixed !== undefined ? `\nfixed: ${d.n_fixed}  random: ${d.n_random}` : "");
    updateChip("chip-dataset", "ok", `Dataset: ${d.filename}`);
  }
  anUpdateDatasetModeVisibility();
  rvAutoLoadForDataset(path);
}

let anAllTargets = [];  // [{name, bits}]
let anAllRprevs = [];   // [{name, desc}]

async function anRefreshPresets() {
  try {
    const { presets, targets, rprevs } = await api("GET", "/api/model/presets");
    anPresets = presets;
    anAllTargets = targets;
    anAllRprevs = rprevs;
    const psel = $("an-preset");
    psel.innerHTML = "";
    presets.forEach((p) => {
      const opt = document.createElement("option");
      opt.value = p.name; opt.textContent = `${p.name} — ${p.desc}`;
      if (p.name === "basemul-word") opt.selected = true;  // physically closest to a real probe
      psel.appendChild(opt);
    });
    const custom = document.createElement("option");
    custom.value = ""; custom.textContent = "Custom (manual target / R_prev)";
    psel.appendChild(custom);
    anUpdateModelVisibility();
  } catch (e) { appendLog("[error] " + e.message); }
}

function populateTargetSelect(selectedName) {
  const sel = $("an-target");
  sel.innerHTML = "";
  anAllTargets.forEach((t) => {
    const o = document.createElement("option");
    o.value = t.name; o.textContent = `${t.name} (${t.bits} bit)`;
    if (t.name === selectedName) o.selected = true;
    sel.appendChild(o);
  });
}

function populateRprevSelect(allowedNames, selectedName) {
  const sel = $("an-rprev");
  sel.innerHTML = "";
  anAllRprevs.filter((r) => allowedNames.includes(r.name)).forEach((r) => {
    const o = document.createElement("option");
    o.value = r.name; o.textContent = `${r.name} — ${r.desc}`;
    if (r.name === selectedName) o.selected = true;
    sel.appendChild(o);
  });
}

function renderModelDetail(p) {
  const bits = (anAllTargets.find((t) => t.name === p.target) || {}).bits ?? p.bits ?? "?";
  const rows = [
    ["Equation", p.equation], ["Register", p.register], ["Bit width", bits + " bit"],
    ["Pipeline stage", p.pipeline_stage], ["Required inputs", p.required_inputs],
    ["Limitations", p.limitations],
  ];
  $("an-model-detail").innerHTML = rows.map(([k, v]) =>
    `<div class="md-row"><span class="md-k">${k}</span>` +
    `<span class="md-v${k === "Required inputs" || k === "Limitations" ? " prose" : ""}">${v}</span></div>`
  ).join("");
}

// HW hides R_prev entirely (it is fixed to "zero" -- see mlkem_ref.py:
// HD(0,X)=HW(X)); HD/basemul-* show only the R_prev modes that are
// physically meaningful for that target (valid_rprevs, from the backend --
// e.g. "acc" is a no-op for p_self); Custom leaves both fully open.
function anUpdateModelVisibility() {
  const name = $("an-preset").value;
  const p = anPresets.find((x) => x.name === name);

  if (!name) {
    $("an-target-row").style.display = "";
    $("an-rprev-wrap").style.display = "";
    $("an-target").disabled = false;
    $("an-rprev").disabled = false;
    populateTargetSelect($("an-target").value || "word");
    populateRprevSelect(anAllRprevs.map((r) => r.name), $("an-rprev").value || "prev-word");
    $("an-model-detail").innerHTML = "";
    renumberSteps("tab-analysis");
    return;
  }

  renderModelDetail(p);
  $("an-target-row").style.display = "";
  populateTargetSelect(p.target);
  $("an-target").disabled = true;

  if (name === "HW") {
    $("an-rprev-wrap").style.display = "none";
  } else {
    $("an-rprev-wrap").style.display = "";
    populateRprevSelect(p.valid_rprevs, p.rprev);
    $("an-rprev").disabled = true;
  }
  renumberSteps("tab-analysis");
}

// TVLA datasets don't use a key-guessing model at all -- hide every
// CPA-specific section rather than show controls that don't apply. Also
// switches the Results Viewer to the matching sub-tab.
function anUpdateDatasetModeVisibility() {
  const path = $("an-dataset").value;
  const d = anDatasets.find((x) => x.path === path);
  const isCpa = !d || d.mode === "cpa";  // default to showing CPA controls when nothing is selected yet
  $("an-model-section").style.display = isCpa ? "" : "none";
  $("an-cpa-only-run-fields").style.display = isCpa ? "" : "none";
  $("an-run-cpa").style.display = isCpa ? "" : "none";
  $("an-run-tvla").style.display = isCpa ? "none" : "";
  renumberSteps("tab-analysis");
  rvSelectViewer(isCpa ? "cpa" : "tvla");
}

// --- lightweight SVG line chart (no library, deterministic coordinate math) -----
// Extended beyond the original correlation-curve chart to support: dashed
// horizontal threshold lines (TVLA's +-4.5), shaded vertical bands (BMUL/
// NTT/INTT windows), and a shaded min/max band under a mean line
// (cross-campaign success rate) -- still zero dependencies, still one
// coordinate-math function.
function fmtAxisNum(v) {
  if (Math.abs(v) >= 1000) return Math.round(v).toString();
  return (Math.round(v * 100) / 100).toString();
}

function renderLineChart(container, series, opts = {}) {
  const nonEmpty = series.filter((s) => s.points && s.points.length);
  const band = opts.band && opts.band.points && opts.band.points.length ? opts.band : null;
  if (!nonEmpty.length && !band) {
    container.innerHTML = '<div class="chart-empty">no data</div>';
    return;
  }
  const W = opts.width || 640, H = opts.height || 180;
  const padL = 46, padR = 14, padT = 10, padB = 22;
  const plotW = W - padL - padR, plotH = H - padT - padB;

  const allPts = nonEmpty.flatMap((s) => s.points);
  const bandPts = band ? band.points.flatMap((p) => [[p[0], p[1]], [p[0], p[2]]]) : [];
  const hLineYs = (opts.hLines || []).map((h) => h.y);
  const xs = allPts.map((p) => p[0]).concat(bandPts.map((p) => p[0]));
  const ys = allPts.map((p) => p[1]).concat(bandPts.map((p) => p[1])).concat(hLineYs);
  const xMin = opts.xMin ?? Math.min(...xs), xMax = opts.xMax ?? Math.max(...xs);
  const yMin = opts.yMin ?? Math.min(0, ...ys), yMax = opts.yMax ?? Math.max(...ys);
  const xSpan = (xMax - xMin) || 1, ySpan = (yMax - yMin) || 1;
  const sx = (x) => padL + ((x - xMin) / xSpan) * plotW;
  const sy = (y) => padT + plotH - ((y - yMin) / ySpan) * plotH;

  let svg = `<svg viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg">`;

  // Shaded vertical bands (drawn first, under everything else).
  (opts.bands || []).forEach((b) => {
    const x0 = sx(Math.max(xMin, b.x0)), x1 = sx(Math.min(xMax, b.x1));
    if (x1 > x0) {
      svg += `<rect x="${x0.toFixed(1)}" y="${padT}" width="${(x1 - x0).toFixed(1)}" height="${plotH}" ` +
             `fill="${b.color || "#38bdf822"}"/>`;
      if (b.label) {
        svg += `<text x="${((x0 + x1) / 2).toFixed(1)}" y="${padT + 10}" text-anchor="middle" ` +
               `class="chart-axis-label">${b.label}</text>`;
      }
    }
  });

  [0, 0.5, 1].forEach((f) => {
    const y = padT + plotH * (1 - f);
    svg += `<line x1="${padL}" y1="${y.toFixed(1)}" x2="${W - padR}" y2="${y.toFixed(1)}" ` +
           `stroke="#334155" stroke-width="1"/>`;
    svg += `<text x="${padL - 6}" y="${(y + 3).toFixed(1)}" text-anchor="end" ` +
           `class="chart-axis-label">${fmtAxisNum(yMin + ySpan * f)}</text>`;
  });
  svg += `<text x="${padL}" y="${H - 4}" class="chart-axis-label">${fmtAxisNum(xMin)}</text>`;
  svg += `<text x="${W - padR}" y="${H - 4}" text-anchor="end" class="chart-axis-label">${fmtAxisNum(xMax)}</text>`;

  // Shaded min/max band under a mean line (cross-campaign success rate).
  if (band) {
    const upper = band.points.map(([x, , hi]) => `${sx(x).toFixed(1)},${sy(hi).toFixed(1)}`);
    const lower = band.points.slice().reverse().map(([x, lo]) => `${sx(x).toFixed(1)},${sy(lo).toFixed(1)}`);
    svg += `<polygon points="${upper.concat(lower).join(" ")}" fill="${band.color || "#38bdf833"}" stroke="none"/>`;
  }

  // Dashed horizontal threshold lines (TVLA's +-4.5).
  (opts.hLines || []).forEach((h) => {
    const y = sy(h.y);
    svg += `<line x1="${padL}" y1="${y.toFixed(1)}" x2="${W - padR}" y2="${y.toFixed(1)}" ` +
           `stroke="${h.color || "#fbbf24"}" stroke-width="1" stroke-dasharray="4,3"/>`;
    if (h.label) {
      svg += `<text x="${W - padR}" y="${(y - 3).toFixed(1)}" text-anchor="end" ` +
             `class="chart-axis-label" fill="${h.color || "#fbbf24"}">${h.label}</text>`;
    }
  });

  nonEmpty.forEach((s) => {
    const pts = s.points.map(([x, y]) => `${sx(x).toFixed(1)},${sy(y).toFixed(1)}`).join(" ");
    svg += `<polyline points="${pts}" fill="none" stroke="${s.color}" stroke-width="1.6"/>`;
    if (s.markPeak) {
      let best = s.points[0];
      for (const pt of s.points) if (Math.abs(pt[1]) > Math.abs(best[1])) best = pt;
      svg += `<circle cx="${sx(best[0]).toFixed(1)}" cy="${sy(best[1]).toFixed(1)}" r="3.5" ` +
             `fill="${s.color}"/>`;
    }
  });
  svg += "</svg>";
  container.innerHTML = svg;
}

// --- Correlation vs. Time (one coefficient, on demand) ---------------------------
async function ccRun() {
  const file = $("an-dataset").value;
  if (!file) { appendLog("[error] no dataset selected"); return; }
  const body = {
    file, target: $("an-target").value, rprev: $("an-rprev").value,
    poly: $("cc-poly").value, coeff: $("cc-coeff").value,
    max_traces: $("an-max-traces").value, dk_ref_path: $("an-dkref").value.trim(),
  };
  $("cc-summary").textContent = "running…";
  try {
    const r = await api("POST", "/api/analysis/correlation_curve", body);
    const series = [{
      label: "best guess", color: "#38bdf8", markPeak: true,
      points: r.best_curve.map((v, i) => [i, v]),
    }];
    if (r.oracle_assisted) {
      series.push({
        label: "true key", color: "#4ade80", markPeak: true,
        points: r.true_curve.map((v, i) => [i, v]),
      });
    }
    renderLineChart($("cc-chart"), series, { height: 180, yMin: -1, yMax: 1 });
    let txt = `poly ${r.poly}, coeff ${r.coeff} (${r.n_traces} traces, ${r.n_samples} samples):\n` +
              `best guess ${r.best_guess}  (peak |ρ| = ${r.best_corr.toFixed(4)})`;
    if (r.oracle_assisted) {
      txt += `\ntrue value ${r.true_value}, rank ${r.true_rank} of ${3329} candidates`;
    }
    $("cc-summary").textContent = txt;
  } catch (e) {
    $("cc-chart").innerHTML = '<div class="chart-empty">error</div>';
    $("cc-summary").textContent = "error";
    appendLog("[error] " + e.message);
  }
}

function anRenderCPAResult(r) {
  const oracle = r.oracle_assisted
    ? "ORACLE-ASSISTED (--dk-ref given) — NOT a blind-attack result"
    : "unknown-key attack — the real attack";
  let txt = `file: ${r.file}\ntraces used: ${r.n_traces_used}\n` +
            `model: target=${r.target} rprev=${r.rprev}\n[${oracle}]`;
  if (r.oracle_assisted) {
    txt += `\n\nrank-1 fraction: ${(100 * r.rank1_fraction).toFixed(1)}%\n` +
           `median true rank: ${Math.round(r.median_true_rank)} of ${r.n_candidates}\n` +
           `mean |ρ| at true key: ${r.mean_true_corr.toFixed(4)}`;
  }
  if (r.curve) {
    txt += `\n\nMTD (traces to disclosure): ` +
           (r.curve.mtd !== null && r.curve.mtd !== undefined ? r.curve.mtd : "not reached");
  }
  if (r.report_path) txt += `\n\nsaved report: ${r.report_path}`;
  $("an-summary").textContent = txt;

  const tbody = $("an-results-table").querySelector("tbody");
  tbody.innerHTML = "";
  (r.results || []).forEach((row) => {
    const tr = document.createElement("tr");
    const tv = row.true_value === -1 ? "—" : row.true_value;
    const tc = (row.true_corr === null || row.true_corr === undefined) ? "—" : row.true_corr.toFixed(4);
    tr.innerHTML = `<td>${row.poly}</td><td>${row.coeff}</td><td>${row.best_guess}</td>` +
      `<td>${row.best_corr.toFixed(4)}</td><td>${tv}</td><td>${row.true_rank ?? "—"}</td><td>${tc}</td>`;
    tbody.appendChild(tr);
  });
  $("an-download-results").dataset.path = r.results_csv || "";

  const ctbody = $("an-curve-table").querySelector("tbody");
  ctbody.innerHTML = "";
  if (r.curve) {
    r.curve.checkpoints.forEach((row) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${row.n_traces}</td><td>${(100 * row.rank1_fraction).toFixed(1)}%</td>` +
        `<td>${Math.round(row.median_rank)}</td><td>${row.mean_true_corr.toFixed(4)}</td>`;
      ctbody.appendChild(tr);
    });
    $("an-download-curve").dataset.path = r.curve.curve_csv || "";
    renderLineChart($("curve-chart"), [{
      label: "rank-1 %", color: "#38bdf8",
      points: r.curve.checkpoints.map((c) => [c.n_traces, 100 * c.rank1_fraction]),
    }], { height: 160, yMin: 0, yMax: 100 });
  } else {
    $("an-download-curve").dataset.path = "";
    $("curve-chart").innerHTML = '<div class="chart-empty">enable "key-rank-vs-trace-count curve" and re-run</div>';
  }
  if (r.model_config) srRun(r.model_config.target, r.model_config.rprev);
}

const NTT_INTT_NOTE = "NTT/INTT windows not shown: their cycle ranges have not been measured " +
  "yet (config.py: NTT_WINDOWS_CYCLES/INTT_WINDOW_CYCLES are None) -- never guessed.";

function bmulBands(windowsSamples) {
  const colors = ["#38bdf822", "#a78bfa22", "#fbbf2422"];
  return (windowsSamples || []).map((w, i) => ({ x0: w[0], x1: w[1], color: colors[i % 3], label: "BMUL " + i }));
}

function anRenderTVLAResult(r) {
  const badge = r.simulated ? "SYNTHETIC — not a real-hardware result" : "REAL HARDWARE";
  $("an-summary").textContent =
    `file: ${r.file}  [${badge}]\nfixed: ${r.n_fixed}  random: ${r.n_random}\n` +
    `max |t|: ${r.max_abs_t.toFixed(2)} at sample ${r.max_abs_t_sample} (threshold ${r.leakage_threshold})\n` +
    `leakage: ${r.leakage_detected ? "DETECTED" : "not detected"}` +
    (r.report_path ? `\n\nsaved report: ${r.report_path}` : "");
  $("an-results-table").querySelector("tbody").innerHTML = "";
  $("an-curve-table").querySelector("tbody").innerHTML = "";
  $("an-download-results").dataset.path = "";
  $("an-download-curve").dataset.path = "";
  rvRenderTvla(r);
}

function anDone() {
  $("an-run-cpa").disabled = false;
  $("an-run-tvla").disabled = false;
}

async function anPoll(jobId, kind) {
  try {
    const job = await api("GET", "/api/analysis/status?job_id=" + jobId);
    if (job.status === "running") {
      setStatus($("an-status"), "warn", job.message || "running…");
      anPollTimer = setTimeout(() => anPoll(jobId, kind), 700);
      return;
    }
    anDone();
    if (job.status === "done") {
      setStatus($("an-status"), "ok", "done");
      if (kind === "cpa") anRenderCPAResult(job.result); else anRenderTVLAResult(job.result);
      rvRefreshList();
    } else if (job.status === "error") {
      setStatus($("an-status"), "bad", "error");
      $("an-summary").textContent = "error: " + job.error;
      appendLog("[error] analysis: " + job.error);
    }
  } catch (e) {
    appendLog("[error] " + e.message);
    anDone();
  }
}

async function anRunCPA() {
  const file = $("an-dataset").value;
  if (!file) { appendLog("[error] no dataset selected"); return; }
  if (anPollTimer) { clearTimeout(anPollTimer); anPollTimer = null; }
  const body = {
    file, model: $("an-preset").value, target: $("an-target").value, rprev: $("an-rprev").value,
    max_traces: $("an-max-traces").value, dk_ref_path: $("an-dkref").value.trim(),
    curve: $("an-curve").checked,
  };
  $("an-run-cpa").disabled = true; $("an-run-tvla").disabled = true;
  rvSelectViewer("cpa");
  setStatus($("an-status"), "warn", "starting…");
  try {
    const r = await api("POST", "/api/analysis/cpa/start", body);
    appendLog("started CPA analysis job " + r.job_id);
    anPoll(r.job_id, "cpa");
  } catch (e) {
    setStatus($("an-status"), "bad", "failed to start");
    appendLog("[error] " + e.message);
    anDone();
  }
}

async function anRunTVLA() {
  const file = $("an-dataset").value;
  if (!file) { appendLog("[error] no dataset selected"); return; }
  if (anPollTimer) { clearTimeout(anPollTimer); anPollTimer = null; }
  const body = { file, max_traces: $("an-max-traces").value };
  $("an-run-cpa").disabled = true; $("an-run-tvla").disabled = true;
  rvSelectViewer("tvla");
  setStatus($("an-status"), "warn", "starting…");
  try {
    const r = await api("POST", "/api/analysis/tvla/start", body);
    appendLog("started TVLA analysis job " + r.job_id);
    anPoll(r.job_id, "tvla");
  } catch (e) {
    setStatus($("an-status"), "bad", "failed to start");
    appendLog("[error] " + e.message);
    anDone();
  }
}

async function anDownload(btnId) {
  const path = $(btnId).dataset.path;
  if (!path) { appendLog("[error] no CSV available yet — run an analysis first"); return; }
  try {
    const r = await api("POST", "/api/analysis/download", { path });
    const blob = new Blob([r.csv], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = r.filename;
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(a.href);
  } catch (e) { appendLog("[error] " + e.message); }
}

// --- Results Viewer: load persisted reports, TVLA/CPA/Raw sub-tabs ---------------
document.querySelectorAll(".viewer-tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => rvSelectViewer(btn.dataset.viewer));
});

function rvSelectViewer(name) {
  document.querySelectorAll(".viewer-tab-btn").forEach((b) => b.classList.toggle("active", b.dataset.viewer === name));
  document.querySelectorAll(".viewer-panel").forEach((p) => p.classList.toggle("active", p.id === "viewer-" + name));
}

let rvResults = [];

async function rvRefreshList(fileFilter) {
  try {
    const { results } = await api("POST", "/api/results/list", { file: fileFilter || "" });
    rvResults = results;
    const sel = $("rv-select");
    const prev = sel.value;
    sel.innerHTML = '<option value="">— none loaded —</option>';
    results.forEach((r) => {
      const opt = document.createElement("option");
      opt.value = r.path;
      opt.textContent = `${r.dataset_filename}  [${r.kind}]  ${fmtDate(r.analysis_timestamp_unix)}`;
      if (r.path === prev) opt.selected = true;
      sel.appendChild(opt);
    });
    return results;
  } catch (e) { appendLog("[error] " + e.message); return []; }
}

async function rvLoad(path) {
  if (!path) {
    $("rv-info").textContent = "Not Available — run an analysis above, or pick a saved result.";
    return;
  }
  try {
    const rep = await api("POST", "/api/results/load", { path });
    $("rv-info").textContent =
      `kind: ${rep.kind}  dataset: ${rep.dataset.path}\n` +
      `model: ${JSON.stringify(rep.model_config || {})}\n` +
      `model_version: ${rep.model_version}  analysed: ${fmtDate(rep.analysis_timestamp_unix)}`;
    if (rep.kind === "cpa") {
      rvSelectViewer("cpa");
      anRenderCPAResult(rep);
    } else {
      rvSelectViewer("tvla");
      anRenderTVLAResult(rep);
    }
  } catch (e) {
    $("rv-info").textContent = "Not Available — " + e.message;
    appendLog("[error] " + e.message);
  }
}

// Selecting a dataset in Step 1 auto-loads its most recent saved result.
async function rvAutoLoadForDataset(path) {
  if (!path) { $("rv-info").textContent = "Not Available — pick a dataset."; return; }
  const results = await rvRefreshList(path);
  if (results.length) {
    $("rv-select").value = results[0].path;
    rvLoad(results[0].path);
  } else {
    $("rv-info").textContent = "Not Available — this dataset has no saved analysis yet. Run one above.";
  }
}

// --- TVLA Viewer: t-curve, mean traces, detection-vs-N -----------------------------
async function rvRenderTvla(r) {
  // Window bands need this dataset's sample rate, which run_tvla()'s result
  // doesn't carry (it's in the file's own metadata) -- fetch a tiny (1
  // trace) raw-trace read just for bmul_windows_samples/sample_rate_hz,
  // reusing the Raw Trace Viewer's own endpoint rather than adding another one.
  let windows = [];
  try {
    const raw = await api("POST", "/api/analysis/raw_traces", { file: r.file, first_n: 1, max_samples: 1 });
    windows = raw.bmul_windows_samples;
  } catch (e) { /* non-fatal -- chart still renders without window bands */ }

  const threshold = r.leakage_threshold;
  renderLineChart($("tvla-t-chart"), [
    { label: "|t|", color: "#38bdf8", points: r.sample_indices.map((x, i) => [x, r.t_curve[i]]) },
  ], {
    height: 200,
    hLines: [{ y: threshold, color: "#fbbf24", label: "+4.5" }, { y: -threshold, color: "#fbbf24", label: "-4.5" }],
    bands: bmulBands(windows),
  });
  setKV("tvla-stats", {
    "max |t|": r.max_abs_t.toFixed(2), "peak sample": r.max_abs_t_sample,
    "traces to detection": r.traces_to_detection ?? "not reached",
    leakage: r.leakage_detected ? "DETECTED" : "not detected",
  });

  renderLineChart($("tvla-mean-chart"), [
    { label: "fixed", color: "#4ade80", points: r.sample_indices.map((x, i) => [x, r.fixed_mean_curve[i]]) },
    { label: "random", color: "#f87171", points: r.sample_indices.map((x, i) => [x, r.random_mean_curve[i]]) },
  ], { height: 160, bands: bmulBands(windows) });

  const detCkpts = r.detection_curve || [];
  renderLineChart($("tvla-detection-chart"), [
    { label: "max |t|", color: "#38bdf8", points: detCkpts.map((c) => [c.n_traces, c.max_abs_t]) },
  ], { height: 150, hLines: [{ y: threshold, color: "#fbbf24", label: "threshold" }], yMin: 0 });
  const dtbody = $("tvla-detection-table").querySelector("tbody");
  dtbody.innerHTML = "";
  detCkpts.forEach((c) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${c.n_traces}</td><td>${c.max_abs_t.toFixed(2)}</td>` +
      `<td class="${c.leakage_detected ? "pass" : ""}">${c.leakage_detected ? "yes" : "no"}</td>`;
    dtbody.appendChild(tr);
  });
}

// --- CPA Viewer: cross-campaign success rate -------------------------------------
async function srRun(target, rprev) {
  target = target || $("an-target").value;
  rprev = rprev || $("an-rprev").value;
  if (!target || !rprev) { $("sr-summary").textContent = "Not Available — pick a leakage model first."; return; }
  try {
    const r = await api("POST", "/api/analysis/success_rate", { target, rprev });
    if (!r.available) {
      $("sr-chart").innerHTML = '<div class="chart-empty">Not Available</div>';
      $("sr-summary").textContent = `Not Available — ${r.reason}`;
      return;
    }
    renderLineChart($("sr-chart"), [
      { label: "mean rank-1 %", color: "#38bdf8",
        points: r.checkpoints.filter((c) => c.mean_rank1_fraction !== null)
                              .map((c) => [c.n_traces, 100 * c.mean_rank1_fraction]) },
    ], {
      height: 180, yMin: 0, yMax: 100,
      band: {
        color: "#38bdf833",
        points: r.checkpoints.filter((c) => c.min_rank1_fraction !== null)
                              .map((c) => [c.n_traces, 100 * c.min_rank1_fraction, 100 * c.max_rank1_fraction]),
      },
    });
    $("sr-summary").textContent =
      `${r.n_campaigns} independent campaigns (target=${r.target}, rprev=${r.rprev}):\n` +
      r.dataset_paths.join("\n");
  } catch (e) {
    $("sr-chart").innerHTML = '<div class="chart-empty">error</div>';
    $("sr-summary").textContent = "error: " + e.message;
    appendLog("[error] " + e.message);
  }
}

// --- Raw Trace Viewer --------------------------------------------------------------
let rawFetched = null;  // last fetch, for ROI cropping / export without re-fetching

async function rvRefreshRawDatasetSelect() {
  const sel = $("raw-dataset");
  const prev = sel.value;
  sel.innerHTML = "";
  anDatasets.forEach((d) => {
    const opt = document.createElement("option");
    const src = d.simulated ? "synthetic" : "REAL";
    opt.value = d.path;
    opt.textContent = `${d.filename}  [${d.mode}, ${src}, ${d.n_completed} traces]`;
    if (d.path === prev) opt.selected = true;
    sel.appendChild(opt);
  });
}

async function rawLoad() {
  const file = $("raw-dataset").value;
  if (!file) { appendLog("[error] no dataset selected for Raw Trace Viewer"); return; }
  const idxText = $("raw-indices").value.trim();
  const body = { file, max_samples: 4000 };
  if (idxText) body.indices = idxText.split(",").map((s) => parseInt(s.trim(), 10)).filter((n) => !isNaN(n));
  else body.first_n = 4;
  try {
    const r = await api("POST", "/api/analysis/raw_traces", body);
    rawFetched = r;
    $("raw-roi-from").value = "0";
    $("raw-roi-to").value = "";
    rawRenderChart();
    setKV("raw-info", {
      "sample rate": r.sample_rate_hz ? r.sample_rate_hz.toExponential(3) + " Sa/s" : "unknown",
      "trigger/source": "status_busy (see ATTACK_MODEL.md / arty_a7_top.sv trig_out)",
      decimation: r.decimated ? `stride ${r.stride} (every ${r.stride}th sample shown)` : "none (full resolution)",
    });
    const tbody = $("raw-traces-table").querySelector("tbody");
    tbody.innerHTML = "";
    r.traces.forEach((t) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${t.index}</td><td class="${t.ok ? "pass" : "fail"}">${t.ok ? "ok" : "FAIL"}</td>` +
        `<td>${t.cycles}</td><td>${t.ct_preview}</td>`;
      tbody.appendChild(tr);
    });
  } catch (e) {
    $("raw-chart").innerHTML = '<div class="chart-empty">error</div>';
    appendLog("[error] " + e.message);
  }
}

function rawRenderChart() {
  if (!rawFetched) return;
  const r = rawFetched;
  const from = parseInt($("raw-roi-from").value, 10) || 0;
  const toText = $("raw-roi-to").value.trim();
  const to = toText ? parseInt(toText, 10) : r.sample_indices.length - 1;
  const colors = ["#38bdf8", "#4ade80", "#f87171", "#fbbf24", "#a78bfa", "#2dd4bf", "#f472b6", "#94a3b8"];
  const series = r.traces.map((t, i) => ({
    label: "trace " + t.index, color: colors[i % colors.length],
    points: r.sample_indices.map((x, j) => [x, t.samples[j]]).filter(([x]) => x >= from && x <= to),
  }));
  renderLineChart($("raw-chart"), series, { height: 260, bands: bmulBands(r.bmul_windows_samples) });
}

function rawResetZoom() {
  if (!rawFetched) return;
  $("raw-roi-from").value = "0";
  $("raw-roi-to").value = "";
  rawRenderChart();
}

function rawExportSvg() {
  const svg = $("raw-chart").querySelector("svg");
  if (!svg) { appendLog("[error] nothing to export -- load traces first"); return; }
  const blob = new Blob([new XMLSerializer().serializeToString(svg)], { type: "image/svg+xml" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = "raw_traces.svg";
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(a.href);
}

function rawExportPng() {
  const svg = $("raw-chart").querySelector("svg");
  if (!svg) { appendLog("[error] nothing to export -- load traces first"); return; }
  const xml = new XMLSerializer().serializeToString(svg);
  const img = new Image();
  const svgBlob = new Blob([xml], { type: "image/svg+xml" });
  const url = URL.createObjectURL(svgBlob);
  img.onload = () => {
    const vb = svg.viewBox.baseVal;
    const canvas = document.createElement("canvas");
    canvas.width = (vb && vb.width) || 640; canvas.height = (vb && vb.height) || 260;
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#0b1220"; ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
    URL.revokeObjectURL(url);
    canvas.toBlob((blob) => {
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob); a.download = "raw_traces.png";
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(a.href);
    });
  };
  img.src = url;
}

function rawExportCsv() {
  if (!rawFetched) { appendLog("[error] nothing to export -- load traces first"); return; }
  const r = rawFetched;
  const header = ["sample_index", "time_s"].concat(r.traces.map((t) => "trace_" + t.index)).join(",");
  const lines = [header];
  r.sample_indices.forEach((x, j) => {
    const row = [x, r.time_axis[j]].concat(r.traces.map((t) => t.samples[j]));
    lines.push(row.join(","));
  });
  const blob = new Blob([lines.join("\n")], { type: "text/csv" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = "raw_traces.csv";
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(a.href);
}

// --- init ------------------------------------------------------------------------
async function init() {
  $("fpga-reg-name").innerHTML = "";
  try {
    const state = await api("GET", "/api/state");
    $("fpga-port").innerHTML = `<option value="${state.fpga.port_default}">${state.fpga.port_default}</option>`;
    $("fpga-baud").value = state.fpga.baud_default;
    state.fpga.registers.forEach((r) => {
      const opt = document.createElement("option");
      opt.value = r; opt.textContent = r;
      if (r === "STATUS") opt.selected = true;
      $("fpga-reg-name").appendChild(opt);
    });

    $("scope-ip").value = state.scope.ip_default;
    $("scope-channel").value = state.scope.channel_default;
    $("scope-dialect").value = state.scope.dialect_default;
    $("scope-timeout").value = state.scope.timeout_default;
    placeholderIp = state.scope.placeholder_ip;
    checkPlaceholder();
    refreshResourcePreview();

    if (state.fpga.connected) {
      setStatus($("fpga-status"), "ok", "connected");
      setKV("fpga-info", state.fpga.info);
      $("fpga-connect").disabled = true; $("fpga-disconnect").disabled = false;
      updateChip("chip-fpga", "ok", "FPGA connected");
    } else {
      updateChip("chip-fpga", "idle", "FPGA disconnected");
    }
    if (state.scope.connected) {
      setStatus($("scope-status"), "ok", "connected");
      setKV("scope-info", state.scope.info);
      $("scope-connect").disabled = true; $("scope-disconnect").disabled = false;
      updateChip("chip-scope", "ok", "Scope connected");
    } else {
      updateChip("chip-scope", "idle", "Scope disconnected");
    }
  } catch (e) { appendLog("[error] could not load initial state: " + e.message); }

  refreshPorts();

  $("fpga-refresh-ports").addEventListener("click", refreshPorts);
  $("fpga-connect").addEventListener("click", fpgaConnect);
  $("fpga-disconnect").addEventListener("click", fpgaDisconnect);
  $("fpga-preflight").addEventListener("click", fpgaPreflight);
  $("fpga-read-reg").addEventListener("click", fpgaReadReg);
  $("fpga-write-reg").addEventListener("click", fpgaWriteReg);
  $("fpga-buf-read").addEventListener("click", fpgaBufRead);
  $("fpga-buf-write").addEventListener("click", fpgaBufWrite);
  $("fpga-op-run").addEventListener("click", fpgaOpRun);

  $("scope-ip").addEventListener("input", () => {
    refreshResourcePreview();
    checkPlaceholder();
  });
  $("scope-connect").addEventListener("click", scopeConnect);
  $("scope-disconnect").addEventListener("click", scopeDisconnect);
  $("scope-list-resources").addEventListener("click", scopeListResources);
  $("scope-save-config").addEventListener("click", scopeSaveConfig);

  // Advanced Model Settings (collapsed, inside CPA Analysis) -- plugin
  // discovery state only; does NOT set the Leakage Model section's
  // target/rprev (anRefreshPresets, below, owns that -- one place decides
  // what an analysis run actually uses).
  await modelRefreshList();
  $("model-refresh").addEventListener("click", modelRefreshList);
  $("model-load").addEventListener("click", modelLoad);
  $("model-selftest").addEventListener("click", modelSelftest);
  $("model-save-default").addEventListener("click", modelSaveDefault);

  // Trace Acquisition tab: Generate Dataset (Step 1, offline).
  genUpdateFieldVisibility();
  await genRefreshInputSets();
  $("gen-mode").addEventListener("change", genUpdateFieldVisibility);
  $("gen-start").addEventListener("click", genStart);
  $("gen-refresh-input-sets").addEventListener("click", genRefreshInputSets);

  // Trace Acquisition tab: Acquire Real Traces (Step 2, hardware only).
  acqRefreshInfo();
  await acqRefreshDatasets();
  updateChip("chip-acq", "idle", "Acquisition idle");
  $("acq-start").addEventListener("click", acqStart);
  $("acq-stop").addEventListener("click", acqStop);
  $("acq-refresh-datasets").addEventListener("click", acqRefreshDatasets);
  $("acq-resume-file").addEventListener("change", acqUpdateResumeVisibility);
  $("acq-capture-one").addEventListener("click", acqCaptureOne);

  await anRefreshDatasets();
  await anRefreshPresets();
  renumberSteps("tab-analysis");
  $("an-refresh-datasets").addEventListener("click", anRefreshDatasets);
  $("an-dataset").addEventListener("change", anShowDatasetInfo);
  $("an-preset").addEventListener("change", anUpdateModelVisibility);
  $("an-target").addEventListener("change", () => { if (!$("an-preset").value) anUpdateModelVisibility(); });
  $("an-run-cpa").addEventListener("click", anRunCPA);
  $("an-run-tvla").addEventListener("click", anRunTVLA);
  $("an-download-results").addEventListener("click", () => anDownload("an-download-results"));
  $("an-download-curve").addEventListener("click", () => anDownload("an-download-curve"));
  $("cc-run").addEventListener("click", ccRun);

  // Results Viewer.
  await rvRefreshList();
  $("rv-refresh").addEventListener("click", () => rvRefreshList());
  $("rv-load").addEventListener("click", () => rvLoad($("rv-select").value));
  $("sr-run").addEventListener("click", () => srRun());
  $("raw-load").addEventListener("click", rawLoad);
  $("raw-reset-zoom").addEventListener("click", rawResetZoom);
  $("raw-export-svg").addEventListener("click", rawExportSvg);
  $("raw-export-png").addEventListener("click", rawExportPng);
  $("raw-export-csv").addEventListener("click", rawExportCsv);
  ["raw-roi-from", "raw-roi-to"].forEach((id) => $(id).addEventListener("change", rawRenderChart));

  renumberSteps("tab-acquire");
  pollLog();
}

init();
