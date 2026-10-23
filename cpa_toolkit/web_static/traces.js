// Parse & View page: open any trace H5 file (a campaign of this toolkit, or a per-channel capture
// folder such as C2_traces.h5 + C4_traces.h5), find the Decaps start of every trace from the trig_out
// marker (cpa_toolkit/trace_parse.py), and view the traces. Loaded after app.js and uses its helpers.

let trFiles = [], trFile = null, trDesc = null, trMethod = null, trJob = null, tvData = null;

// The request fields that name the power and the marker data of the open file.
function trSource() {
  const m = $("tr-marker").value ? JSON.parse($("tr-marker").value) : null;
  const src = { file: trFile, dataset: $("tr-dataset").value };
  if (m) { src.marker_dataset = m.dataset; if (m.file !== trFile) src.marker_file = m.file; }
  return src;
}
const trName = (p) => String(p || "").split("/").pop();
const trFolderOf = (p) => String(p || "").split("/").slice(0, -1).join("/");

function trEnter() {
  if (!$("tr-folder").value) {
    let saved = null;
    try { saved = localStorage.getItem("tr-folder"); } catch (e) { /* no storage */ }
    $("tr-folder").value = saved || S?.defaults?.traces_dir || "";
  }
  if (!trFiles.length) trScan(true);
}

async function trScan(quiet) {
  const folder = $("tr-folder").value.trim();
  try {
    const r = await api("POST", "/api/h5/scan", { folder });
    $("tr-folder").value = r.folder;
    try { localStorage.setItem("tr-folder", r.folder); } catch (e) { /* no storage */ }
    trFiles = r.files;
    trDrawFiles();
    if (!quiet) toast(`${plural(trFiles.length, "H5 file")} found`);
  } catch (e) { trFiles = []; trDrawFiles(); if (!quiet) err(e); }
}
function trDrawFiles() {
  const tb = $("tr-files").querySelector("tbody");
  tb.innerHTML = trFiles.length ? trFiles.map((f) => `<tr data-p="${esc(f.path)}" class="${f.path === trFile ? "sel" : ""}">` +
    `<td><div class="name mono">${esc(f.name)}</div>${f.folder ? `<div class="sub">${esc(f.folder)}</div>` : ""}</td>` +
    `<td class="num">${fmtBytes(f.size_bytes)}</td><td>${fmtAgo(f.mtime)}</td></tr>`).join("")
    : '<tr><td colspan="3"><div class="empty sm">No H5 files in this folder.</div></td></tr>';
  tb.querySelectorAll("tr[data-p]").forEach((tr) => tr.addEventListener("click", () => trOpen(tr.dataset.p)));
}

// Open a file from another page (Trace Library: View traces / Parse).
async function trOpenFile(path) {
  showPage("traces");
  const folder = trFolderOf(path);
  if (folder && $("tr-folder").value.replace(/\/$/, "") !== folder) { $("tr-folder").value = folder; await trScan(true); }
  await trOpen(path);
}

async function trOpen(path) {
  if (tvPlay) tvPlayReset();
  trFile = path;
  trDrawFiles();
  try {
    trDesc = await api("POST", "/api/h5/describe", { path });
  } catch (e) { err(e); return; }
  const d = trDesc;
  $("tr-src").classList.remove("hidden");
  const first = d.datasets.find((x) => x.name === d.power) || d.datasets[0];
  kv("tr-info", [["File", d.name], ["Kind", d.kind === "campaign" ? `campaign · ${String(d.attrs.mode || "cpa").toUpperCase()}` : "capture file"],
    ["Traces", first ? `${d.n_rows ?? first.shape[0]} × ${first.shape[1]} samples` : "—"], ["Sample rate", rateTxt(d.sample_rate_hz)]]);
  $("tr-dataset").innerHTML = d.datasets.map((x) => `<option value="${esc(x.name)}">${esc(x.name)} · ${x.shape.join(" × ")}</option>`).join("");
  $("tr-dataset").value = d.power || "";
  // marker candidates: other datasets of this file, then the trace datasets of the other files in the folder
  const opts = [{ label: "none", value: "" }];
  d.datasets.filter((x) => x.name !== d.power).forEach((x) => opts.push({ label: `${x.name} (this file)`, value: JSON.stringify({ file: d.path, dataset: x.name }), name: x.name }));
  d.siblings.forEach((sb) => sb.datasets.forEach((x) => opts.push({ label: `${sb.name} › ${x.name}${sb.channel ? ` (${sb.channel})` : ""}`,
    value: JSON.stringify({ file: sb.path, dataset: x.name }), file: sb.name, channel: sb.channel })));
  $("tr-marker").innerHTML = opts.map((o) => `<option value="${esc(o.value)}">${esc(o.label)}</option>`).join("");
  const src = d.parse?.marker_source || "", trig = String(d.attrs.trigger_channel || "");
  const pick = opts.find((o) => o.value && src && src === `${o.file || d.name}:${JSON.parse(o.value).dataset}`)
    || opts.find((o) => o.name === "marker")
    || opts.find((o) => o.value && trig && (o.channel === trig || (o.file || "").startsWith(trig + "_")))
    || (d.kind === "external" ? opts.find((o) => /marker|trig|C4_/i.test(o.label)) : null);
  $("tr-marker").value = pick ? pick.value : "";
  trMethod.set(d.parse?.method || ($("tr-marker").value ? "marker" : "pattern"));
  trMethodChanged(trMethod.get());
  $("tv-cls").disabled = !(d.kind === "campaign" && d.attrs.mode === "tvla");
  $("tv-sub").textContent = d.name;
  trShowParse(d.parse, d.parse_hist);
  tvLoad();
}

function trMethodChanged(v) {
  $("tr-marker-opts").classList.toggle("hidden", v !== "marker");
  $("tr-pattern-opts").classList.toggle("hidden", v !== "pattern");
  $("tr-method-hint").textContent = v === "marker"
    ? "Finds the rising edge of trig_out in each trace. This edge is the Decaps start of the trace."
    : "No marker: aligns each trace to the reference trace by correlation. The trigger stays the time origin.";
}

async function trParse() {
  if (!trFile || trJob) return;
  const m = trMethod.get();
  if (m === "marker" && !$("tr-marker").value) return toast("Select the marker channel first", "warn");
  const body = { ...trSource(), method: m, smooth: $("tr-smooth").value, threshold: $("tr-threshold").value.trim() || "auto",
                 reference: $("tr-ref").value, max_shift: $("tr-maxshift").value };
  await busy($("tr-parse"), async () => {
    try {
      const { job_id } = await api("POST", "/api/h5/parse", body);
      trJob = job_id;
      const j = await pollJob(job_id, (x) => progress("tr-progress", x.progress || 0, x.total || 0, x.message || "parsing…"), 300);
      if (j.status !== "done") throw new Error(j.error);
      progress("tr-progress", 1, 1, j.message);
      trShowParse(j.result, j.result.histogram);
      toast(`Parsed: ${j.result.n_ok} of ${j.result.n} traces`, j.result.n_ok === j.result.n ? "ok" : "warn");
      trDesc.parse = j.result;
      tvLoad();
      if (typeof libRefresh === "function") libRefresh(true);
    } catch (e) { progress("tr-progress", 0, 1, "failed"); err(e); }
    trJob = null;
  });
}
async function trClear() {
  if (!trFile) return;
  try { await api("POST", "/api/h5/parse_clear", { path: trFile }); toast("Parse removed"); await trOpen(trFile); } catch (e) { err(e); }
}

function trShowParse(p, hist) {
  $("tr-clear").disabled = !p;
  if (!p) {
    $("tr-result-sub").textContent = "not parsed";
    $("tr-kpis").innerHTML = "";
    $("tr-failures").textContent = "";
    chartEmpty("tr-hist", trFile ? "Not parsed. Select the marker channel and click Parse." : "Select a file.");
    return;
  }
  const rate = p.sample_rate_hz || trDesc?.sample_rate_hz || 0;
  const ns = (samples) => (rate ? `${(samples / rate * 1e9).toFixed(1)} ns` : `${samples} samples`);
  const spread = p.rise_max - p.rise_min;
  $("tr-result-sub").textContent = `${p.method === "pattern" ? "pattern match" : "marker edge"} · ${fmtAgo(p.created_unix)}${p.live ? " · during capture" : ""}`;
  const mlkem = trDesc?.kind === "campaign";
  kpis("tr-kpis", [
    ["parsed", `${p.n_ok} / ${p.n}`, p.n_ok === p.n ? "all traces" : `${p.n - p.n_ok} without an edge`, p.n_ok === p.n ? "good" : p.n_ok ? "warn" : "bad"],
    [mlkem ? "Decaps start" : "marker edge", rate ? `${(p.rise_median / rate * 1e6).toFixed(3)} µs` : `#${p.rise_median}`, `sample ${p.rise_median} of the record`],
    ["spread", ns(spread), `σ ${ns(p.rise_std).replace(" ns", "")}${rate ? " ns" : ""}`, spread <= 2 ? "good" : ""],
    mlkem ? ["core clock", `${(p.clock_mhz || 0).toFixed(3)} MHz`, p.spc_source === "measured" ? "measured from the pulse width" : "nominal (no falling edge)"]
          : ["marker high", p.width_median > 0 ? ns(p.width_median) : "—", p.n_fall ? `${p.n_fall} pulses ended in the record` : "longer than the record"],
    mlkem ? ["samples / cycle", (p.samples_per_cycle || 0).toFixed(3), p.n_fall ? `${p.n_fall} pulses ended in the record` : ""] : null,
  ]);
  const fails = Object.entries(p.failures || {});
  $("tr-failures").textContent = fails.length ? "Not parsed: " + fails.map(([k, n]) => `${n} × ${k}`).join(" · ") : "";
  if (hist && hist.x && hist.x.length) {
    const x = rate ? hist.x.map((s) => s / rate * 1e6) : hist.x;
    chart("tr-hist", [{ type: "bar", x, y: hist.y, marker: { color: C.s[0] }, hovertemplate: `%{x}${rate ? " µs" : ""}: %{y} traces<extra></extra>` }],
      { height: 170, xTitle: `${trDesc?.kind === "campaign" ? "Decaps start" : "marker edge"} (${rate ? "µs after the record start" : "sample"})`, yTitle: "traces", showlegend: false,
        hovermode: "closest", margin: { l: 48, r: 10, t: 6, b: 38 } });
  } else chartEmpty("tr-hist", "Parsed. Parse again to see the distribution of the Decaps start.");
}

// ---- viewer -------------------------------------------------------------------------------
const tvH = () => vh(0.5, 300, 640);
function tvBody(extra) {
  return { ...trSource(), max_points: 4000, aligned: $("tv-aligned").checked, marker: $("tv-marker").checked, ...extra };
}
async function tvLoad() {
  if (!trFile) return;
  if (tvPlay) tvPlayReset();
  const idx = $("tv-indices").value.trim();
  const body = tvBody({ stats: $("tv-stats").checked, stats_n: 500 });
  if (idx) body.indices = idx.split(/[ ,]+/).map((s) => parseInt(s, 10)).filter((n) => !isNaN(n));
  else Object.assign(body, { start: $("tv-start").value, count: $("tv-count").value, cls: $("tv-cls").value });
  await busy($("tv-load"), async () => {
    try { tvData = await api("POST", "/api/h5/view", body); tvDraw(); } catch (e) { tvData = null; chartEmpty("tv-chart", e.message); }
  });
}
function tvParseBadge(r) {
  const b = $("tv-parse-badge");
  const parsed = !!r?.parse;
  b.className = "badge " + (parsed ? "ok" : "warn");
  b.textContent = parsed ? (r.aligned ? "parsed · aligned" : "parsed") : "not parsed";
  $("tv-aligned").disabled = !parsed;
  $("tv-bands").disabled = !(r?.axis?.bmul_windows?.length);
}
function tvMarkerTraces(list) {
  return list.filter((t) => t.marker).map((t, i) => line(`marker #${t.index}`, "#fbbf24", t.marker.x, t.marker.y,
    { yaxis: "y2", width: 1, opacity: i ? 0.35 : 0.8 }));
}
function tvDraw() {
  const r = tvData;
  if (!r) return;
  tvParseBadge(r);
  const tr = [], col = { all: ["#38bdf826", C.s[0]], fixed: ["#4ade8026", C.s[1]], random: ["#f8717126", C.s[2]] };
  Object.entries(r.stats || {}).forEach(([n, s]) => { const [f, c] = col[n] || col.all; tr.push(...band(`${n} ±σ (n=${s.n})`, f, s.x, s.lo, s.hi), line(`${n} mean`, c, s.x, s.mean, { width: 1.8 })); });
  r.traces.forEach((t, i) => tr.push(line(`#${t.index}${t.class === null ? "" : t.class ? " random" : " fixed"}${t.ok ? "" : " ✗"}`, C.s[(i + 3) % 8], t.x, t.y, { width: 1, opacity: 0.85 })));
  const mk = tvMarkerTraces(r.traces);
  tr.push(...mk);
  const wb = $("tv-bands").checked ? axisBands(r.axis) : { shapes: [], annotations: [] };
  chart("tv-chart", tr, { height: tvH(), xTitle: xTitle(r.axis), yTitle: "amplitude", y2: mk.length > 0, y2Title: "marker",
    shapes: wb.shapes, annotations: wb.annotations });
  $("tv-note").textContent = `${r.traces.length} of ${r.n_completed} traces · ${rateTxt(r.axis.sample_rate_hz)} · min/max envelope` +
    (r.aligned ? " · each trace from its own Decaps start" : r.parse ? " · not aligned" : " · time from the trigger") +
    (r.traces.some((t) => t.cycles !== null) ? " · " + r.traces.map((t) => `#${t.index}: ${t.cycles} cycles${t.ok ? "" : " (failed check)"}`).join(", ") : "");
}
function tvStep(d) {
  const n = Math.max(1, parseInt($("tv-count").value, 10) || 4);
  $("tv-indices").value = "";
  $("tv-start").value = Math.max(0, (parseInt($("tv-start").value, 10) || 0) + d * n);
  tvLoad();
}
function tvCsv() {
  if (!tvData) return toast("Plot traces first", "warn");
  const r = tvData, n = r.traces[0].x.length;
  const rows = [["x_" + r.axis.unit].concat(r.traces.map((t) => "trace_" + t.index)).join(",")];
  for (let j = 0; j < n; j++) rows.push([r.traces[0].x[j]].concat(r.traces.map((t) => t.y[j])).join(","));
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([rows.join("\n")], { type: "text/csv" }));
  a.download = "traces_envelope.csv"; document.body.appendChild(a); a.click(); a.remove();
}

// --- Player: one trace per frame, in file order. Traces are fetched in batches ahead of the
// play position; the y range only grows, so the plot does not jump between frames.
const TV_BATCH = 20;
let tvPlay = null;
function tvPlayReset() {
  if (tvPlay) clearTimeout(tvPlay.timer);
  tvPlay = null;
  $("tv-play").textContent = "▶ Play";
  $("tv-scrub").classList.add("hidden");
}
function tvPlayState() {
  const cls = $("tv-cls").value, key = JSON.stringify(tvBody({ cls }));
  if (!tvPlay || tvPlay.key !== key) {
    tvPlayReset();
    tvPlay = { key, file: trFile, cls, pos: Math.max(0, parseInt($("tv-start").value, 10) || 0), end: null,
               buf: new Map(), stats: null, axis: null, n: trDesc?.n_rows || 0, yr: null, running: false, pending: new Set(), parse: null };
  }
  return tvPlay;
}
async function tvFetch(p, start) {
  if (p.pending.has(start) || (p.end !== null && start >= p.end)) return;
  p.pending.add(start);
  try {
    const wantStats = !p.stats && $("tv-stats").checked;
    const r = await api("POST", "/api/h5/view", tvBody({ start, count: TV_BATCH, cls: p.cls, stats: wantStats, stats_n: 500 }));
    if (tvPlay !== p) return;
    p.axis = r.axis; p.parse = r.parse; p.aligned = r.aligned;
    if (wantStats) p.stats = r.stats || {};
    r.traces.forEach((t, k) => { p.buf.set(start + k, t); tvGrowRange(p, t.y); });
    if (p.stats) Object.values(p.stats).forEach((s) => { tvGrowRange(p, s.lo); tvGrowRange(p, s.hi); });
    if (r.traces.length < TV_BATCH) p.end = start + r.traces.length;
    if (!p.cls) p.n = r.n_completed;
  } catch (e) {
    if (/no traces match/.test(e.message)) p.end = start; else throw e;
  } finally { p.pending.delete(start); }
}
function tvGrowRange(p, ys) {
  let lo = Infinity, hi = -Infinity;
  for (const v of ys) { if (v < lo) lo = v; if (v > hi) hi = v; }
  if (!isFinite(lo)) return;
  const pad = 0.04 * Math.max(hi - lo, 1e-9);
  p.yr = p.yr ? [Math.min(p.yr[0], lo - pad), Math.max(p.yr[1], hi + pad)] : [lo - pad, hi + pad];
}
function tvFrame(p) {
  const t = p.buf.get(p.pos);
  if (!t) return;
  tvParseBadge({ parse: p.parse, aligned: p.aligned, axis: p.axis });
  const tr = [], col = { all: ["#38bdf826", C.s[1]], fixed: ["#4ade8026", C.s[1]], random: ["#f8717126", C.s[2]] };
  if ($("tv-stats").checked && p.stats) Object.entries(p.stats).forEach(([n, s]) => { const [f, c] = col[n] || col.all;
    tr.push(...band(`${n} ±σ (n=${s.n})`, f, s.x, s.lo, s.hi), line(`${n} mean`, c, s.x, s.mean, { width: 1.4, opacity: 0.8 })); });
  const prev = p.buf.get(p.pos - 1);   // the previous frame, faint: a little persistence, like a scope
  if (prev) tr.push(line(`#${prev.index}`, C.s[0], prev.x, prev.y, { width: 1, opacity: 0.18 }));
  tr.push(line(`#${t.index}${t.class === null ? "" : t.class ? " random" : " fixed"}${t.ok ? "" : " ✗"}`, C.s[0], t.x, t.y, { width: 1.1 }));
  const mk = tvMarkerTraces([t]);
  tr.push(...mk);
  const wb = $("tv-bands").checked ? axisBands(p.axis) : { shapes: [], annotations: [] };
  chart("tv-chart", tr, { height: tvH(), xTitle: xTitle(p.axis), yTitle: "amplitude", yRange: p.yr, y2: mk.length > 0, y2Title: "marker",
    shapes: wb.shapes, annotations: wb.annotations, uirevision: "tv-play:" + p.file });
  const n = p.end !== null ? p.end : p.n;
  $("tv-pos").max = Math.max(0, n - 1); $("tv-pos").value = p.pos;
  $("tv-pos-txt").textContent = `#${t.index} · ${p.pos + 1} / ${n || "?"}`;
  $("tv-note").textContent = `trace #${t.index}${t.cycles !== null ? `: ${t.cycles} cycles` : ""}${t.ok ? "" : " (failed check)"}` +
    `${t.rise !== null ? ` · Decaps start at sample ${t.rise}${t.parse_ok ? "" : " (not found)"}` : ""} · ` +
    `${rateTxt(p.axis.sample_rate_hz)}${p.running ? ` · playing ${$("tv-speed").value} traces/s` : ""}`;
}
async function tvShowPos(p, pos) {
  p.pos = Math.max(0, pos);
  if (!p.buf.has(p.pos)) await tvFetch(p, p.pos);
  if (tvPlay !== p) return false;
  if (p.end !== null && p.pos >= p.end) return false;
  tvFrame(p);
  for (const k of p.buf.keys()) if (k < p.pos - 5 || k > p.pos + 3 * TV_BATCH) p.buf.delete(k);   // bounded memory
  return true;
}
async function tvTick() {
  const p = tvPlay;
  if (!p || !p.running) return;
  const t0 = performance.now();
  try {
    if (!(await tvShowPos(p, p.pos))) {
      tvPause();
      if (tvPlay === p) { toast("End of the file. Click Play to start again."); p.pos = 0; }
      return;
    }
    let ahead = p.pos + 1;                                  // keep half a batch buffered ahead
    while (ahead < p.pos + TV_BATCH / 2 && p.buf.has(ahead)) ahead++;
    if (ahead < p.pos + TV_BATCH / 2) tvFetch(p, ahead).catch(() => {});
    p.pos += 1;
  } catch (e) { tvPause(); err(e); return; }
  if (!p.running) return;
  const period = 1000 / (parseFloat($("tv-speed").value) || 5);
  p.timer = setTimeout(tvTick, Math.max(0, period - (performance.now() - t0)));
}
function tvPlayToggle() {
  if (!trFile) return toast("Select a file first", "warn");
  const p = tvPlayState();
  if (p.running) return tvPause();
  p.running = true;
  $("tv-indices").value = "";
  $("tv-play").textContent = "⏸ Pause";
  $("tv-scrub").classList.remove("hidden");
  tvTick();
}
function tvPause() {
  if (!tvPlay) return;
  tvPlay.running = false;
  clearTimeout(tvPlay.timer);
  $("tv-play").textContent = "▶ Play";
  tvShowPos(tvPlay, Math.max(0, tvPlay.pos - 1)).catch(() => {});   // the frame on screen, with the note updated
}
async function tvScrub() {
  const p = tvPlayState();
  p.pos = parseInt($("tv-pos").value, 10) || 0;
  if (!p.running) await tvShowPos(p, p.pos).catch((e) => err(e));
}

// ---- wiring --------------------------------------------------------------------------------
(function trInit() {
  trMethod = seg("tr-method", trMethodChanged);
  trMethodChanged("marker");
  $("tr-scan").addEventListener("click", () => trScan(false));
  $("tr-folder").addEventListener("keydown", (e) => e.key === "Enter" && trScan(false));
  $("tr-parse").addEventListener("click", trParse);
  $("tr-clear").addEventListener("click", trClear);
  $("tr-dataset").addEventListener("change", () => tvLoad());
  $("tr-marker").addEventListener("change", () => { if ($("tr-marker").value) trMethod.set("marker"); trMethodChanged(trMethod.get()); if ($("tv-marker").checked) tvLoad(); });
  $("tv-load").addEventListener("click", tvLoad);
  $("tv-prev").addEventListener("click", () => tvStep(-1));
  $("tv-next").addEventListener("click", () => tvStep(1));
  $("tv-play").addEventListener("click", tvPlayToggle);
  $("tv-pos").addEventListener("input", tvScrub);
  $("tv-cls").addEventListener("change", () => { if (tvPlay) tvPlayReset(); });
  ["tv-aligned", "tv-marker", "tv-stats"].forEach((id) => $(id).addEventListener("change", () => tvLoad()));
  $("tv-bands").addEventListener("change", () => (tvPlay ? tvFrame(tvPlay) : tvDraw()));
  $("tv-export-csv").addEventListener("click", tvCsv);
  document.addEventListener("keydown", (e) => {   // Space: play / pause on this page
    if (e.code !== "Space" || currentPage !== "traces" || !trFile) return;
    if (/^(INPUT|TEXTAREA|SELECT|BUTTON)$/.test(document.activeElement?.tagName || "")) return;
    e.preventDefault(); tvPlayToggle();
  });
  chartEmpty("tr-hist", "Select a file.");
})();
