// CPA Toolkit front-end. Vanilla JS (no build step) on web_gui.py's JSON API; charts with Plotly.

// =====================================================================================
// core helpers
// =====================================================================================
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function api(method, path, body) {
  const opts = { method };
  if (body !== undefined) { opts.headers = { "Content-Type": "application/json" }; opts.body = JSON.stringify(body); }
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || "HTTP " + res.status);
  return data;
}
function download(path, params) {
  const a = document.createElement("a");
  a.href = path + "?" + new URLSearchParams(params).toString();
  document.body.appendChild(a); a.click(); a.remove();
}
const fmtDate = (u) => (u ? new Date(u * 1000).toLocaleString() : "—");
function fmtAgo(u) {
  if (!u) return "—";
  const s = Date.now() / 1000 - u;
  if (s < 60) return "just now";
  if (s < 3600) return Math.round(s / 60) + " min ago";
  if (s < 86400) return Math.round(s / 3600) + " h ago";
  return new Date(u * 1000).toLocaleDateString();
}
function fmtBytes(n) {
  if (n === null || n === undefined) return "—";
  const u = ["B", "kB", "MB", "GB", "TB"]; let i = 0;
  while (n >= 1000 && i < u.length - 1) { n /= 1000; i++; }
  return (i ? n.toFixed(1) : n) + " " + u[i];
}
const pct = (x) => (x === null || x === undefined ? "—" : (100 * x).toFixed(x === 1 ? 0 : 1) + "%");
const short = (h, n = 16) => (h ? (h.length > n ? h.slice(0, n) + "…" : h) : "—");
const rateTxt = (hz) => (hz ? (hz >= 1e9 ? (hz / 1e9).toFixed(2) + " GSa/s" : (hz / 1e6).toFixed(1) + " MSa/s") : "unknown");

function toast(msg, kind = "info", ms = 4500) {
  const el = document.createElement("div");
  el.className = "toast " + kind;
  el.textContent = msg;
  $("toasts").appendChild(el);
  setTimeout(() => el.remove(), ms);
}
function err(e) { const m = e && e.message ? e.message : String(e); toast(m, "bad", 7000); appendLog("[error] " + m); }

function dialog({ title, body, ok = "OK", danger = false, cancel = true }) {
  return new Promise((resolve) => {
    $("modal-title").textContent = title;
    $("modal-body").innerHTML = body;
    const okb = $("modal-ok"), cb = $("modal-cancel");
    okb.textContent = ok;
    okb.className = "btn " + (danger ? "danger" : "primary");
    cb.classList.toggle("hidden", !cancel);
    $("modal-back").classList.add("open");
    const first = $("modal-body").querySelector("input,textarea");
    if (first) setTimeout(() => first.focus(), 30);
    const done = (v) => { $("modal-back").classList.remove("open"); okb.onclick = cb.onclick = null; resolve(v); };
    okb.onclick = () => done(true);
    cb.onclick = () => done(false);
  });
}

async function busy(btn, fn) {
  btn.classList.add("loading");
  try { return await fn(); } finally { btn.classList.remove("loading"); }
}

function progress(id, done, total, text) {
  const el = $(id);
  el.classList.remove("hidden");
  el.querySelector(".fill").style.width = (total ? Math.min(100, (100 * done) / total) : 0) + "%";
  el.querySelector(".txt").textContent = text ?? (total ? `${done} / ${total}` : "");
}

function kpis(id, items) {
  $(id).innerHTML = items.filter(Boolean).map(([l, v, s, cls]) =>
    `<div class="kpi ${cls || ""}"><div class="l">${esc(l)}</div><div class="v" title="${esc(v)}">${esc(v ?? "—")}</div>` +
    (s ? `<div class="s" title="${esc(s)}">${esc(s)}</div>` : "") + `</div>`).join("");
}
function kv(id, pairs) {
  $(id).innerHTML = pairs.map(([k, v]) => `<span class="k">${esc(k)}</span><span class="v">${esc(v ?? "—")}</span>`).join("");
}
function seg(id, onChange) {
  const el = $(id);
  el.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
    if (b.disabled) return;
    el.querySelectorAll("button").forEach((x) => x.classList.toggle("active", x === b));
    onChange && onChange(b.dataset.v);
  }));
  return { get: () => el.querySelector("button.active").dataset.v,
           set: (v) => el.querySelectorAll("button").forEach((x) => x.classList.toggle("active", x.dataset.v === v)) };
}

// Sortable table. columns: [{key, label, num, sortable, value(row), render(row)}]
// opts.checks: a Set of row keys -> a tick-box column (with select-all over the
// visible rows); opts.onCheck() after every change. opts.onTag(tag): a click on
// any [data-tag] chip in the table.
function makeTable(tableId, columns, opts = {}) {
  const t = $(tableId), tb = t.querySelector("tbody"), checks = opts.checks;
  let rows = [], list = [], sortKey = opts.sortKey || null, dir = opts.dir || -1, selected = null;
  t.querySelector("thead").innerHTML = "<tr>" + (checks ? '<th class="chk-col"><input type="checkbox" class="chk-all" title="select all shown"></th>' : "") +
    columns.map((c) => `<th data-k="${c.key}" class="${c.sortable === false ? "" : "sort"} ${c.num ? "num" : ""}">${esc(c.label)}</th>`).join("") + "</tr>";
  t.querySelectorAll("th.sort").forEach((th) => th.addEventListener("click", () => {
    const k = th.dataset.k;
    if (sortKey === k) dir = -dir; else { sortKey = k; dir = 1; }
    draw();
  }));
  if (checks) t.querySelector(".chk-all").addEventListener("change", (e) => {
    list.forEach((r) => (e.target.checked ? checks.add(opts.key(r)) : checks.delete(opts.key(r))));
    draw(); opts.onCheck && opts.onCheck();
  });
  tb.addEventListener("click", (e) => {
    const chip = e.target.closest("[data-tag]");
    if (chip && opts.onTag) { e.stopPropagation(); opts.onTag(chip.dataset.tag); return; }
    const box = e.target.closest("input.chk");
    if (box) {
      box.checked ? checks.add(box.dataset.key) : checks.delete(box.dataset.key);
      syncAll(); opts.onCheck && opts.onCheck();
    }
  });
  function val(r, c) { return c.value ? c.value(r) : r[c.key]; }
  function syncAll() {
    if (!checks) return;
    const all = t.querySelector(".chk-all"), n = list.filter((r) => checks.has(opts.key(r))).length;
    all.checked = list.length > 0 && n === list.length;
    all.indeterminate = n > 0 && n < list.length;
  }
  function draw() {
    const col = columns.find((c) => c.key === sortKey);
    list = col ? rows.slice().sort((a, b) => {
      const x = val(a, col), y = val(b, col);
      if (x === y) return 0;
      if (x === null || x === undefined) return 1;
      if (y === null || y === undefined) return -1;
      return (x < y ? -1 : 1) * dir;
    }) : rows;
    t.querySelectorAll("th").forEach((th) => th.classList.remove("asc", "desc"));
    if (col) t.querySelector(`th[data-k="${sortKey}"]`).classList.add(dir > 0 ? "asc" : "desc");
    const span = columns.length + (checks ? 1 : 0);
    if (!list.length) {
      const empty = typeof opts.empty === "function" ? opts.empty() : opts.empty;
      tb.innerHTML = `<tr><td colspan="${span}">${empty || '<div class="empty">Nothing here.</div>'}</td></tr>`; syncAll(); return;
    }
    tb.innerHTML = list.map((r) => {
      const k = opts.key ? opts.key(r) : "";
      return `<tr data-key="${esc(k)}" class="${k === selected ? "sel" : ""} ${checks && checks.has(k) ? "checked" : ""}">` +
        (checks ? `<td class="chk-col"><input type="checkbox" class="chk" data-key="${esc(k)}" ${checks.has(k) ? "checked" : ""}></td>` : "") +
        columns.map((c) => `<td class="${c.num ? "num" : ""} ${c.cls || ""}">${c.render ? c.render(r) : esc(val(r, c) ?? "—")}</td>`).join("") + "</tr>";
    }).join("");
    if (opts.onRow) tb.querySelectorAll("tr[data-key]").forEach((tr) => tr.addEventListener("click", (e) => {
      if (e.target.closest("button,a,input,label,[data-tag]")) return;
      opts.onRow(list.find((r) => opts.key(r) === tr.dataset.key));
    }));
    syncAll();
  }
  return { set(r) { rows = r; draw(); }, select(k) { selected = k; draw(); }, redraw: draw, rows: () => rows, visible: () => list };
}

// ---- tags: chips, an inline editor, the filter list, bulk actions ----
// Chips; clickable ones (inside a makeTable with onTag) filter the list.
const tagChips = (tags, click = true) => (tags || []).map((t) =>
  `<span class="tag${click ? " click" : ""}"${click ? ` data-tag="${esc(t)}" title="show only #${esc(t)}"` : ""}>${esc(t)}</span>`).join("");
const knownTags = new Set();          // every tag seen on campaigns and vector sets: editor suggestions
function learnTags(rows) { rows.forEach((r) => (r?.tags || []).forEach((t) => knownTags.add(t))); }
const tagSuggestions = () => [...knownTags].sort((a, b) => a.localeCompare(b));
const splitTags = (v) => String(v).split(",").map((x) => x.trim()).filter(Boolean);
// Count every tag over rows -> [[tag, n], ...] sorted by name.
function tagCounts(rows) {
  const m = new Map();
  rows.forEach((r) => (r.tags || []).forEach((t) => m.set(t, (m.get(t) || 0) + 1)));
  return [...m].sort((a, b) => a[0].localeCompare(b[0]));
}
function fillTagFilter(id, rows) {
  const sel = $(id), keep = sel.value, counts = tagCounts(rows);
  sel.innerHTML = `<option value="">all tags</option>` + counts.map(([t, n]) => `<option value="${esc(t)}">#${esc(t)} (${n})</option>`).join("");
  sel.value = counts.some(([t]) => t === keep) ? keep : "";
}
// Chips with x plus an input: Enter, comma, leaving the field or picking a
// suggestion adds; Backspace on an empty input removes the last tag.
// onChange(newTags) saves and returns the tags as stored.
function tagEditor(el, tags, suggestions, onChange) {
  let cur = (tags || []).slice();
  const listId = el.id + "-dl";
  function render(focus) {
    el.innerHTML = `<div class="tag-editor">${cur.map((t, i) => `<span class="tag">${esc(t)}<button data-i="${i}" title="remove #${esc(t)}">×</button></span>`).join("")}` +
      `<input list="${listId}" maxlength="32" placeholder="${cur.length ? "add tag…" : "add tags: Enter or comma…"}"><datalist id="${listId}">` +
      suggestions.filter((s) => !cur.some((c) => c.toLowerCase() === s.toLowerCase())).map((s) => `<option value="${esc(s)}">`).join("") + "</datalist></div>";
    const inp = el.querySelector("input");
    el.querySelectorAll("button[data-i]").forEach((b) => b.addEventListener("click", () => { cur.splice(+b.dataset.i, 1); commit(true); }));
    inp.addEventListener("keydown", (e) => {
      if ((e.key === "Enter" || e.key === ",") && inp.value.trim()) { e.preventDefault(); take(inp, true); }
      else if (e.key === "Backspace" && !inp.value && cur.length) { cur.pop(); commit(true); }
    });
    // a picked suggestion, or a comma arriving without a keydown (paste, IME)
    inp.addEventListener("input", (e) => { if (e.inputType === "insertReplacementText" || !e.inputType || inp.value.includes(",")) take(inp, true); });
    inp.addEventListener("blur", () => inp.value.trim() && take(inp, false));
    if (focus) inp.focus();
  }
  // Empty the input first: re-rendering removes it, and the blur that may fire
  // then must not add (and re-render) a second time.
  function take(inp, focus) {
    const v = inp.value;
    inp.value = "";
    splitTags(v).forEach((t) => { if (!cur.some((c) => c.toLowerCase() === t.toLowerCase())) cur.push(t.slice(0, 32)); });
    commit(focus);
  }
  async function commit(focus) {
    try { const saved = await onChange(cur.slice()); if (Array.isArray(saved)) cur = saved; } catch (e) { err(e); }
    render(focus);
  }
  render(false);
}
// Dialog: tags to add to / remove from several items -> {add, remove} or null.
async function bulkTagDialog(rows) {
  const present = tagCounts(rows);
  const ok = await dialog({ title: `Tags of ${rows.length} item${rows.length > 1 ? "s" : ""}`, ok: "Apply", body:
    `<label class="f">Add tags (comma-separated)</label><input id="dlg-add" type="text" list="dlg-add-dl" placeholder="e.g. em-probe, baseline">` +
    `<datalist id="dlg-add-dl">${tagSuggestions().map((t) => `<option value="${esc(t)}">`).join("")}</datalist>` +
    (present.length ? `<label class="f" style="margin-top:14px">Remove tags</label><div class="tag-pick">` +
      present.map(([t, n]) => `<label class="tag"><input type="checkbox" value="${esc(t)}">${esc(t)} <span class="dim">${n}</span></label>`).join("") + "</div>" : "") });
  if (!ok) return null;
  const add = splitTags($("dlg-add").value), remove = [...$("modal-body").querySelectorAll(".tag-pick input:checked")].map((x) => x.value);
  return add.length || remove.length ? { add, remove } : null;
}
// The bar above a table while rows are ticked: "n selected", actions [label, btn class, fn], Clear.
function bulkBar(id, n, actions, onClear) {
  const el = $(id);
  el.classList.toggle("hidden", !n);
  if (!n) return;
  el.innerHTML = `<span><b>${n}</b> selected</span>` + actions.map(([label, cls], i) => `<button class="btn ${cls} sm" data-i="${i}">${label}</button>`).join("") +
    `<span class="grow"></span><button class="btn ghost sm" data-clear>Clear</button>`;
  el.querySelectorAll("button[data-i]").forEach((b) => b.addEventListener("click", actions[+b.dataset.i][2]));
  el.querySelector("[data-clear]").addEventListener("click", onClear);
}
// Drop ticks of rows that are no longer listed (filtered out, moved, deleted).
function pruneChecks(checks, rows, key) {
  const keep = new Set(rows.map(key));
  [...checks].forEach((k) => keep.has(k) || checks.delete(k));
}
const tagSuffix = (r) => (r.tags?.length ? "  #" + r.tags.join(" #") : "");     // for <option> text
const plural = (n, one, many) => `${n} ${n === 1 ? one : many || one + "s"}`;

// =====================================================================================
// console drawer
// =====================================================================================
let logCursor = 0;
function appendLog(line) {
  const pre = $("log");
  pre.textContent += (pre.textContent ? "\n" : "") + line;
  pre.scrollTop = pre.scrollHeight;
  $("drawer-last").textContent = line;
}
async function pollLog() {
  try {
    const { lines, next } = await api("GET", "/api/log?since=" + logCursor);
    if (lines.length) {
      lines.forEach(appendLog);
      logCursor = next;
      if (lines.some((l) => l.includes("[error]")) && !$("drawer").classList.contains("open")) $("drawer").classList.add("err");
    }
  } catch (e) { /* server restarting */ }
  setTimeout(pollLog, 1000);
}
$("drawer-bar").addEventListener("click", () => { $("drawer").classList.toggle("open"); $("drawer").classList.remove("err"); });

// =====================================================================================
// navigation
// =====================================================================================
const PAGES = ["connect", "vectors", "acquire", "library", "analysis", "advanced"];
let currentPage = "connect";
function showPage(name, push = true) {
  if (!PAGES.includes(name)) name = "connect";
  if (name !== "library" && tvPlay && tvPlay.running) tvPause();   // the player only runs while visible
  currentPage = name;
  document.querySelectorAll(".page").forEach((p) => p.classList.toggle("active", p.id === "page-" + name));
  document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.page === name));
  const page = $("page-" + name);
  $("page-title").textContent = page.dataset.title;
  $("page-desc").textContent = page.dataset.desc;
  $("mobile-nav").value = name;
  if (push && location.hash !== "#" + name) history.pushState(null, "", "#" + name);
  $("content").scrollTop = 0;
  if (name === "library") libRefresh();
  if (name === "analysis") anRefreshDatasets();
  if (name === "acquire") acqRefreshResume();
  window.dispatchEvent(new Event("resize"));
}
document.querySelectorAll("#nav a").forEach((a) => a.addEventListener("click", () => showPage(a.dataset.page)));
document.querySelectorAll("[data-goto]").forEach((el) => el.addEventListener("click", () => showPage(el.dataset.goto)));
// Back/forward (popstate) and a typed or bookmarked #page (hashchange only); one of them may fire for both.
const onHash = () => { if (location.hash.slice(1) !== currentPage) showPage(location.hash.slice(1), false); };
window.addEventListener("popstate", onHash);
window.addEventListener("hashchange", onHash);
document.addEventListener("click", (e) => document.querySelectorAll(".dropdown").forEach((d) => { if (!d.contains(e.target)) d.classList.remove("open"); }));

// =====================================================================================
// Plotly helpers
// =====================================================================================
const C = { grid: "#24324d", text: "#e2e8f0", sub: "#94a3b8",
            s: ["#38bdf8", "#4ade80", "#f87171", "#fbbf24", "#a78bfa", "#2dd4bf", "#f472b6", "#cbd5e1"],
            band: ["#38bdf81f", "#a78bfa1f", "#fbbf241f"] };
// No scrollZoom: the mouse wheel scrolls the page; zoom with a drag box, reset by double-click.
const PLOTLY_CONFIG = { displaylogo: false, responsive: true, scrollZoom: false,
  modeBarButtonsToRemove: ["lasso2d", "select2d"], toImageButtonOptions: { format: "png", filename: "cpa_toolkit", scale: 2 } };
function layout(o = {}) {
  const ax = { gridcolor: C.grid, zerolinecolor: C.grid, color: C.sub, linecolor: C.grid };
  const l = { paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
    font: { color: C.text, family: "SF Mono, Menlo, Consolas, monospace", size: 11 },
    margin: o.margin || { l: 56, r: o.y2 ? 60 : 16, t: 10, b: 42 }, showlegend: o.showlegend !== false,
    legend: { orientation: "h", y: -0.22, font: { size: 10, color: C.sub } },
    xaxis: Object.assign({ title: { text: o.xTitle || "", font: { size: 10 } } }, ax, o.xaxis || {}),
    yaxis: Object.assign({ title: { text: o.yTitle || "", font: { size: 10 } }, range: o.yRange }, ax, o.yaxis || {}),
    hovermode: o.hovermode || "x unified", shapes: o.shapes || [], annotations: o.annotations || [],
    height: o.height || 260, uirevision: o.uirevision };
  if (o.y2) l.yaxis2 = Object.assign({}, ax, { overlaying: "y", side: "right", gridcolor: "transparent", title: { text: o.y2Title || "", font: { size: 10 } } });
  return l;
}
// Plots go into an inner div with an explicit CSS height: with responsive: true, Plotly's resize
// handler drops layout.height and autosizes to the div, so the div itself must carry the height.
// Chart height follows the window (a fraction of its height, clamped), so a 1080p screen
// uses its height and a laptop does not need a scroll to see a whole card.
const vh = (frac, min, max) => Math.round(Math.max(min, Math.min(max, window.innerHeight * frac)));
function plotDiv(el) {
  if (typeof el === "string") el = $(el);
  let gd = el.querySelector(":scope > .plot");
  if (!gd) { el.innerHTML = ""; gd = document.createElement("div"); gd.className = "plot"; el.appendChild(gd); }
  return gd;
}
function chart(el, traces, o = {}) {
  const gd = plotDiv(el);
  gd.style.height = (o.height || 260) + "px";
  return Plotly.react(gd, traces, layout(o), PLOTLY_CONFIG);   // resolves to gd
}
function chartEmpty(el, msg) {
  if (typeof el === "string") el = $(el);
  const gd = el.querySelector(":scope > .plot");
  try { if (gd) Plotly.purge(gd); } catch (e) { /* not a plot */ }
  el.innerHTML = `<div class="chart-empty">${esc(msg)}</div>`;
}
function line(name, color, x, y, o = {}) {
  return { type: "scattergl", mode: "lines", name, x, y, yaxis: o.yaxis || "y", line: { color, width: o.width || 1.4 },
           opacity: o.opacity ?? 1, hovertemplate: "%{y:.4g}<extra>" + name + "</extra>", legendgroup: o.group };
}
function band(name, fill, x, lo, hi) {
  return [{ type: "scatter", mode: "lines", x, y: lo, line: { color: "transparent" }, showlegend: false, hoverinfo: "skip", legendgroup: name },
          { type: "scatter", mode: "lines", name, x, y: hi, line: { color: "transparent" }, fill: "tonexty", fillcolor: fill, hoverinfo: "skip", legendgroup: name }];
}
function peak(name, color, x, y) {
  let bi = 0;
  for (let i = 1; i < y.length; i++) if (Math.abs(y[i]) > Math.abs(y[bi])) bi = i;
  return { type: "scatter", mode: "markers", name: name + " peak", showlegend: false, x: [x[bi]], y: [y[bi]],
           marker: { color, size: 9, line: { color: "#0b1220", width: 1 } } };
}
const hLine = (y, color) => ({ type: "line", xref: "paper", x0: 0, x1: 1, yref: "y", y0: y, y1: y, line: { color, width: 1, dash: "dash" } });
const hLabel = (y, color, text) => ({ xref: "paper", x: 1, y, text, showarrow: false, xanchor: "right", yanchor: "bottom", font: { size: 9, color } });
function windowBands(windows) {
  const shapes = [], annotations = [];
  (windows || []).forEach((w, i) => {
    shapes.push({ type: "rect", xref: "x", x0: w[0], x1: w[1], yref: "paper", y0: 0, y1: 1, fillcolor: C.band[i % 3], line: { width: 0 }, layer: "below" });
    annotations.push({ x: (w[0] + w[1]) / 2, y: 1, yref: "paper", text: "BMUL " + i, showarrow: false, yanchor: "top", font: { size: 9, color: C.sub } });
  });
  return { shapes, annotations };
}
// Decaps phases (axis.phases from the server): faint dotted dividers + a label
// along the bottom, so a feature outside the BMUL windows can be named (the
// decryption BMULs are the CPA target; a TVLA |t| peak falls in J(z‖c) / the
// re-encryption Keccak). `key` is "x" (the axis unit: µs, or samples when the rate
// is unknown) or "samples" (TVLA charts, whose x is the sample index).
function phaseBands(phases, key) {
  const shapes = [], annotations = [];
  const span = Math.max(0, ...(phases || []).map((p) => p[key][1]));   // the visible record
  (phases || []).forEach((p) => {
    const a = p[key][0], b = p[key][1];
    shapes.push({ type: "line", xref: "x", x0: a, x1: a, yref: "paper", y0: 0, y1: 1, line: { color: C.grid, width: 1, dash: "dot" }, layer: "below" });
    // a phase too narrow to hold its name on this record keeps its divider but not its label
    if (b - a >= 0.05 * span)
      annotations.push({ x: (a + b) / 2, y: 0.015, yref: "paper", text: p.label, showarrow: false, yanchor: "bottom", xanchor: "center", font: { size: 8.5, color: C.sub } });
  });
  return { shapes, annotations };
}
// BMUL windows + Decaps phases of a trace axis, for charts whose x is the axis unit.
const axisBands = (axis) => mergeBands(windowBands(axis.bmul_windows), phaseBands(axis.phases, "x"));
// Merge any number of {shapes, annotations}.
function mergeBands(...bs) {
  return { shapes: bs.flatMap((b) => b.shapes || []), annotations: bs.flatMap((b) => b.annotations || []) };
}
// The phase (axis.phases label) a sample index falls in, or "".
function phaseAt(phases, sample) {
  const p = (phases || []).find((q) => sample >= q.samples[0] && sample <= q.samples[1]);
  return p ? p.label : "";
}
const xTitle = (axis) => (axis && axis.unit === "us" ? "time after trigger (µs)" : "sample");
// First sample relative to the trigger (seconds) -> "+0.0 ns" / "−1.000 µs".
function startTxt(s) {
  const a = Math.abs(s), sign = s < 0 ? "−" : "+";
  return a < 1e-6 ? `${sign}${(a * 1e9).toFixed(1)} ns` : `${sign}${(a * 1e6).toFixed(3)} µs`;
}
function coverageKpi(axis) {
  if (!axis || !axis.duration_us) return ["trace length", axis ? `${axis.n_samples} samples` : "—", "sample rate unknown"];
  const ok = !axis.bmul_outside;
  return ["trace length", axis.duration_us.toFixed(1) + " µs", ok ? `covers all BMULs (≥ ${axis.needed_us} µs)` :
          `${axis.bmul_outside} BMUL window(s) cut off — lengthen the timebase to ≥ ${axis.needed_us} µs`, ok ? "good" : "bad"];
}

// =====================================================================================
// global state (polled) -- connection cards, pills, checklist
// =====================================================================================
let S = null;        // last /api/state
let ports = [];
let lastAuto = 0;
const STATE_TXT = { disconnected: "Not connected", connecting: "Connecting…", connected: "Connected", lost: "Link lost", error: "Connection failed" };
const STATE_DOT = { disconnected: "", connecting: "warn pulse", connected: "ok", lost: "bad", error: "bad" };
const STATE_BTN = { disconnected: ["Connect", "primary"], connecting: ["Cancel", "secondary"], connected: ["Disconnect", "danger"],
                    lost: ["Reconnect", "primary"], error: ["Retry", "primary"] };

function renderConn(prefix, snap, describe) {
  $(prefix + "-dot").className = "dot " + STATE_DOT[snap.state];
  $(prefix + "-state-txt").textContent = describe(snap);
  const [lbl, cls] = STATE_BTN[snap.state];
  const b = $(prefix + "-conn-btn");
  b.textContent = lbl; b.className = "btn " + cls; b.dataset.state = snap.state;
  $(prefix + "-error").textContent = snap.error || "";
  $(prefix + "-error").classList.toggle("hidden", !snap.error);
}

function applyState(s) {
  const prev = S;
  S = s;
  const b = s.board, sc = s.scope;
  renderConn("board", b, (x) => x.state === "connected" ? `Connected · ${x.params.port.split("/").pop()} @ ${x.params.baud}`
    : x.state === "connecting" ? `Connecting to ${x.params.port.split("/").pop()}…` : STATE_TXT[x.state]);
  renderConn("scope", sc, (x) => x.state === "connected" ? `Connected · ${x.params.ip}` : x.state === "connecting" ? `Connecting to ${x.params.ip}…` : STATE_TXT[x.state]);
  ["board-port", "board-baud", "board-refresh-ports"].forEach((id) => ($(id).disabled = ["connected", "connecting"].includes(b.state)));
  ["scope-ip", "scope-channel", "scope-dialect", "scope-transport", "scope-points", "scope-timeout"].forEach((id) => ($(id).disabled = ["connected", "connecting"].includes(sc.state)));
  kv("board-info", b.info ? [["Algorithm", b.info.alg_id === 1 ? "ML-KEM (ALG_ID 1)" : b.info.alg_id], ["Core version", b.info.version],
    ["Core clock", b.info.core_mhz + " MHz"], ["0x05 Run", b.info.has_run ? "supported" : "MISSING — rebuild the bitstream; operations are disabled"], ["Port", b.info.port], ["Baud", b.info.baud]] : [["Status", "connect the board to read the core"]]);
  const si = sc.info;
  kv("scope-info", si ? [["Instrument", si.id || si.idn], ["Resource", si.resource], ["Channel", si.channel],
    ["Samples", si.samples || (si.wave_array_count ? `${si.wave_array_count} (last record)` : si.samples)], ["Sample rate", si.sample_rate],
    ...(si.trigger ? [["Trigger", si.trigger + (si.trigger_level_v ? " @ " + si.trigger_level_v : "")]] : []),
    ...(si.timebase_s_div ? [["Timebase", si.timebase_s_div + " s/div"]] : []),
    ...(si.record_start_s !== undefined ? [["Record start", startTxt(si.record_start_s) + " from the trigger"]] : []),
    ...(si.nominal_bits ? [["ADC", si.nominal_bits + " bit"]] : [])] : [["Status", "connect to read the instrument"]]);

  const dotCls = (st) => "dot " + STATE_DOT[st];
  $("pill-board").className = dotCls(b.state); $("pill-board-txt").textContent = b.state === "connected" ? "Board " + (b.info?.version || "") : "Board";
  $("pill-scope").className = dotCls(sc.state); $("pill-scope-txt").textContent = "Scope";
  $("mini-board").className = dotCls(b.state); $("mini-board-txt").textContent = b.state;
  $("mini-scope").className = dotCls(sc.state); $("mini-scope-txt").textContent = sc.state;

  const ready = b.state === "connected" && sc.state === "connected", busyJob = !!s.busy;
  $("acq-checklist").innerHTML = [
    ["Board connected", b.state === "connected", "connect"], ["Oscilloscope connected", sc.state === "connected", "connect"],
    ["Test-vector set chosen", !!$("acq-vector-set").value || !!$("acq-resume").value, "vectors"],
  ].map(([t, ok, page]) => `<li class="${ok ? "done" : ""}"><span class="ic">${ok ? "✓" : ""}</span>${t}${ok ? "" : `<a data-goto="${page}">set up →</a>`}</li>`).join("") +
    `<li class="${trigOk === true ? "done" : trigOk === false ? "fail" : ""}"><span class="ic">${trigOk === true ? "✓" : trigOk === false ? "✗" : ""}</span>` +
    `Trigger checked${trigOk === true ? "" : trigOk === false ? " — failed" : " (recommended)"}${trigOk === true ? "" : '<a id="acq-trig-link">check →</a>'}</li>`;
  $("acq-checklist").querySelectorAll("[data-goto]").forEach((a) => a.addEventListener("click", () => showPage(a.dataset.goto)));
  if ($("acq-trig-link")) $("acq-trig-link").addEventListener("click", trigCheck);
  $("acq-start").disabled = !ready || busyJob || !($("acq-vector-set").value || $("acq-resume").value);
  $("cap-one").disabled = !ready || busyJob;
  $("trig-check").disabled = !ready || busyJob;
  if (prev && (prev.board.state !== b.state || prev.scope.state !== sc.state)) trigOk = null;  // new link: check again
  $("vb-run").disabled = b.state !== "connected" || busyJob;

  if (lsReady) lsInfo();
  if (prev && prev.board.state !== b.state) {
    if (b.state === "connected") toast("Board connected", "ok");
    if (b.state === "connected" && b.info && !b.info.has_run)
      toast("This bitstream has no 0x05 Run: operations are disabled until it is rebuilt (register / buffer access works).", "bad", 12000);
    if (b.state === "lost") toast("Board link lost: " + b.error, "bad", 8000);
    if (b.state === "error" && prev.board.state === "connecting") toast("Board: " + b.error, "bad", 8000);
  }
  if (prev && prev.scope.state !== sc.state) {
    if (sc.state === "connected") toast("Oscilloscope connected", "ok");
    if (sc.state === "error" && prev.scope.state === "connecting") toast("Scope: " + sc.error, "bad", 8000);
  }
  if ($("board-auto").checked && ["lost", "error"].includes(b.state) && Date.now() - lastAuto > 3000) {
    const port = b.params.port;
    if (port && ports.some((p) => p.device === port)) {
      lastAuto = Date.now();
      api("POST", "/api/fpga/connect", { port, baud: b.params.baud }).catch(() => {});
    }
  }
}
async function pollState() {
  try { applyState(await api("GET", "/api/state")); } catch (e) { /* server restarting */ }
  setTimeout(pollState, 1000);
}

// =====================================================================================
// Connection page
// =====================================================================================
async function refreshPorts(quiet) {
  try {
    ({ ports } = await api("GET", "/api/fpga/ports"));
    const sel = $("board-port"), keep = sel.value || localStorage.getItem("board-port");
    sel.innerHTML = ports.map((p) => `<option value="${esc(p.device)}">${esc(p.device.split("/").pop())}${p.hint ? "  —  " + esc(p.hint) : ""}</option>`).join("")
      || `<option value="">no serial ports found</option>`;
    const pick = ports.find((p) => p.device === keep) || ports.find((p) => p.recommended) || ports[0];
    if (pick) sel.value = pick.device;
    portHint();
    if (!quiet) toast(ports.length ? `${ports.length} serial port(s) found` : "No serial ports found", ports.length ? "info" : "warn");
  } catch (e) { err(e); }
}
function portHint() {
  const p = ports.find((x) => x.device === $("board-port").value);
  const h = $("board-port-hint");
  h.textContent = p ? [p.description, p.hint].filter(Boolean).join(" · ") : "";
  h.className = "hint" + (p && p.hint.startsWith("JTAG") ? " warn" : p && p.recommended ? " good" : "");
}
setInterval(() => { if (S && !["connected", "connecting"].includes(S.board.state) && currentPage === "connect") refreshPorts(true); }, 3000);

async function boardConnBtn() {
  const st = $("board-conn-btn").dataset.state || "disconnected";
  try {
    if (st === "connected" || st === "connecting") {
      if (S && S.busy && !(await dialog({ title: "Disconnect the board?", body: "A job is using the board; it will stop after the current trace (the file stays resumable).", ok: "Disconnect", danger: true }))) return;
      await api("POST", "/api/fpga/disconnect", {});
    } else {
      const port = $("board-port").value;
      if (!port) return toast("Choose a serial port", "warn");
      localStorage.setItem("board-port", port);
      await api("POST", "/api/fpga/connect", { port, baud: $("board-baud").value });
    }
    applyState(await api("GET", "/api/state"));
  } catch (e) { err(e); }
}
async function boardPreflight() {
  await busy($("board-preflight"), async () => {
    try {
      const { checks } = await api("POST", "/api/fpga/preflight", {});
      $("board-preflight-wrap").classList.remove("hidden");
      $("board-preflight-table").querySelector("tbody").innerHTML = checks.map((c) =>
        `<tr><td>${esc(c.name)}</td><td class="${c.ok ? "pass" : "fail"}">${c.ok ? "PASS" : "FAIL"}</td><td>${esc(c.detail)}</td></tr>`).join("");
      const ok = checks.every((c) => c.ok);
      $("board-preflight-summary").textContent = ok ? "all checks passed" : "some checks failed";
      $("board-preflight-summary").className = ok ? "good" : "bad";
    } catch (e) { err(e); }
  });
}
const SCOPE_CHANNELS = { lecroy: ["C1", "C2", "C3", "C4"], default: ["CHANnel1", "CHANnel2", "CHANnel3", "CHANnel4"],
                         tektronix: ["CH1", "CH2", "CH3", "CH4"] };
const SCOPE_REMOTE = { vicp: "TCPIP (VICP)", vxi11: "LXI (VXI11)" };  // LeCroy Utilities › Remote › Control from
function scopeHint() {
  const ip = $("scope-ip").value.trim(), h = $("scope-hint"), tr = $("scope-transport").value, d = $("scope-dialect").value;
  const tpl = S && S.defaults.scope_resources ? S.defaults.scope_resources[tr] : "";
  if (S && ip === S.defaults.scope_placeholder_ip) { h.textContent = "This is config.py's placeholder address — enter the scope's real IP."; h.className = "hint warn"; }
  else if (d === "lecroy" && !SCOPE_REMOTE[tr]) { h.textContent = "LeCroy scopes are controlled over VICP or LXI (VXI-11), not a raw socket."; h.className = "hint warn"; }
  else if (d !== "lecroy" && tr === "vicp") { h.textContent = "VICP is Teledyne LeCroy's protocol — use VXI-11 or a raw socket for this scope."; h.className = "hint warn"; }
  else {
    const res = ip && tpl ? tpl.replace("{ip}", ip) : "";
    const remote = d === "lecroy" ? ` · scope: Utilities › Remote › ${SCOPE_REMOTE[tr]}` : "";
    h.textContent = res ? `VISA resource: ${res}${remote}${tr === "vicp" ? " · needs pyvicp (uv sync --extra scope)" : ""}` : "";
    h.className = "hint";
  }
}
// Channel suggestions per scope family; when the user switches family, keep
// the channel number but respell it (CHANnel2 <-> C2 <-> CH2).
function scopeDialectChanged(fromUser) {
  const d = $("scope-dialect").value;
  $("channel-options").innerHTML = (SCOPE_CHANNELS[d] || []).map((c) => `<option value="${c}">`).join("");
  if (fromUser) {
    const m = $("scope-channel").value.trim().match(/(\d+)\s*$/);
    $("scope-channel").value = (SCOPE_CHANNELS[d] || SCOPE_CHANNELS.default)[0].replace(/\d+$/, m ? m[1] : "1");
    if (d === "lecroy" && !SCOPE_REMOTE[$("scope-transport").value]) $("scope-transport").value = "vicp";
    if (d !== "lecroy" && $("scope-transport").value === "vicp") $("scope-transport").value = "vxi11";  // VICP is LeCroy-only
  }
  scopeHint();
}
async function scopeConnBtn() {
  const st = $("scope-conn-btn").dataset.state || "disconnected";
  try {
    if (st === "connected" || st === "connecting") await api("POST", "/api/scope/disconnect", {});
    else await api("POST", "/api/scope/connect", { ip: $("scope-ip").value, channel: $("scope-channel").value,
      dialect: $("scope-dialect").value, transport: $("scope-transport").value, points: $("scope-points").value,
      timeout_ms: $("scope-timeout").value });
    applyState(await api("GET", "/api/state"));
  } catch (e) { err(e); }
}
async function scopeFind() {
  await busy($("scope-list-resources"), async () => {
    try {
      const { resources } = await api("GET", "/api/scope/resources");
      $("scope-resources-wrap").classList.remove("hidden");
      const tb = $("scope-resources-table").querySelector("tbody");
      tb.innerHTML = resources.length ? resources.map((r) => `<tr data-r="${esc(r.resource)}"><td class="mono">${esc(r.resource)}</td><td>${esc(r.idn)}</td></tr>`).join("")
        : `<tr><td colspan=2 class="muted">no instruments found</td></tr>`;
      tb.querySelectorAll("tr[data-r]").forEach((tr) => tr.addEventListener("click", () => {
        const m = tr.dataset.r.match(/^(TCPIP\d*|VICP)::([^:]+)::/i);
        if (m) {
          $("scope-ip").value = m[2];
          if (/^VICP/i.test(m[1])) $("scope-transport").value = "vicp";
          else if (/::INSTR$/i.test(tr.dataset.r)) $("scope-transport").value = "vxi11";
          scopeHint(); toast("IP filled in from " + tr.dataset.r);
        }
      }));
    } catch (e) { err(e); }
  });
}
async function scopeSave() {
  try { await api("POST", "/api/scope/save_config", { ip: $("scope-ip").value, channel: $("scope-channel").value,
                                                       dialect: $("scope-dialect").value, transport: $("scope-transport").value });
        toast("Saved as the default scope settings (config.py)", "ok"); } catch (e) { err(e); }
}

// =====================================================================================
// Test Vectors page
// =====================================================================================
let vgMode, vsSets = [], vsTrashSets = [], vsTrash = false, vsChecks = new Set(), vsSel = null, vpOffset = 0, vpRow = null, vsTable;
const vsRows = () => (vsTrash ? vsTrashSets : vsSets);
const VP_PAGE = 50;
const kindLabel = (m) => (m.mode === "tvla" ? (m.strategy === "invalid-random" ? "fixed vs random bytes" : "fixed vs random valid")
  : m.strategy === "structured" ? `structured (poly ${m.poly}, pair ${m.pair})` : m.strategy === "invalid" ? "random bytes" : "valid encapsulations");

function vgVisibility() {
  const cpa = vgMode.get() === "cpa";
  $("vg-cpa-fields").classList.toggle("hidden", !cpa);
  $("vg-tvla-fields").classList.toggle("hidden", cpa);
  $("vg-structured-fields").classList.toggle("hidden", !(cpa && $("vg-strategy").value === "structured"));
}
async function pollJob(id, tick, every = 400) {
  for (;;) {
    const j = await api("GET", "/api/jobs/status?job_id=" + id);
    tick && tick(j);
    if (j.status !== "running") return j;
    await sleep(every);
  }
}
async function vgGenerate() {
  const body = { mode: vgMode.get(), n: $("vg-n").value, seed: $("vg-seed").value.trim(), strategy: $("vg-strategy").value,
                 poly: $("vg-poly").value, pair: $("vg-pair").value, invalid_random: $("vg-tvla-random").value === "invalid" };
  await busy($("vg-generate"), async () => {
    try {
      const { job_id, seed } = await api("POST", "/api/vectors/generate", body);
      if (!body.seed) appendLog("vector-set seed: " + seed);
      const j = await pollJob(job_id, (x) => progress("vg-progress", x.progress, x.total, x.status === "running" ? `${x.message} · ${x.progress}/${x.total}` : ""));
      if (j.status !== "done") throw new Error(j.error);
      progress("vg-progress", 1, 1, `${j.result.n} vectors · expected K verified in software`);
      toast(`Vector set ${j.result.id} created`, "ok");
      await vsRefresh(j.result.path);
    } catch (e) { progress("vg-progress", 0, 1, "failed"); err(e); }
  });
}
async function vsRefresh(selectPath) {
  try {
    const r = await api("GET", "/api/vectors/list"), st = r.storage;
    vsSets = r.sets;
    vsTrashSets = vsTrash ? (await api("GET", "/api/vectors/list?trash=1")).sets : [];
    learnTags(vsSets); learnTags(vsTrashSets);
    $("vs-title").textContent = vsTrash ? "Vector-set trash" : "Vector sets";
    $("vs-count").textContent = vsTrash ? plural(vsTrashSets.length, "set") : vsSets.length ? `${plural(vsSets.length, "set")} · ${fmtBytes(st.bytes)}` : "";
    $("vs-trash-toggle").textContent = vsTrash ? "← Back to sets" : `Trash${st.n_trash ? ` (${st.n_trash})` : ""}`;
    $("vs-empty-trash").disabled = !vsTrashSets.length;
    $("nav-vectors").textContent = vsSets.length || "";
    fillTagFilter("vs-tag", vsRows());
    vsFilter();
    const rows = vsRows(), want = [selectPath, vsSel?.path, rows[0]?.path].find((x) => x && rows.some((m) => m.path === x)) || null;
    if (want && want === vsSel?.path && !selectPath) { vsSel = rows.find((m) => m.path === want); vsTable.select(want); vsShowMeta(); }
    else vsSelect(want);
    acqFillSets();
  } catch (e) { err(e); }
}
function vsFilter() {
  const q = $("vs-search").value.trim().toLowerCase(), tag = $("vs-tag").value;
  const rows = vsRows().filter((m) => (!tag || (m.tags || []).includes(tag)) &&
    (!q || [m.id, m.label, m.mode, m.strategy, kindLabel(m), ...(m.tags || [])].join(" ").toLowerCase().includes(q)));
  pruneChecks(vsChecks, rows, (m) => m.path);
  vsTable.set(rows);
  vsBulk();
}
function vsBulk() {
  const sel = vsTable.visible().filter((m) => vsChecks.has(m.path));
  bulkBar("vs-bulk", sel.length, vsTrash ? [
    ["Restore", "secondary", () => vsRestore(sel)],
    ["Delete permanently", "danger", () => vsPurge(sel)],
  ] : [
    ["Tags…", "secondary", async () => {
      const t = await bulkTagDialog(sel);
      if (!t) return;
      try { await api("POST", "/api/vectors/tag", { paths: sel.map((m) => m.path), ...t }); toast(`Tags updated on ${plural(sel.length, "set")}`, "ok"); } catch (e) { err(e); }
      vsRefresh();
    }],
    ["Move to trash", "ghost", () => vsDelete(sel)],
  ], () => { vsChecks.clear(); vsTable.redraw(); vsBulk(); });
}
function vsSelect(path) {
  vsSel = vsRows().find((m) => m.path === path) || null;
  vsTable.select(path);
  vsShowMeta();
  vpOffset = 0; vpRow = null; $("vp-detail").innerHTML = "";
  vpLoad();
}
// Under the list: what is selected, its actions, its name and tags.
function vsShowMeta() {
  const m = vsSel;
  $("vs-selected").textContent = $("vs-trash-selected").textContent = m ? `${m.filename} · ${fmtBytes(m.size_bytes)}` : "no set selected";
  $("vs-actions").classList.toggle("hidden", vsTrash);
  $("vs-trash-actions").classList.toggle("hidden", !vsTrash);
  ["vs-verify-sw", "vs-download-btn", "vs-delete", "vs-use", "vs-purge", "vs-restore"].forEach((id) => ($(id).disabled = !m));
  const el = $("vs-meta");
  if (!m) { el.innerHTML = ""; return; }
  if (vsTrash) { el.innerHTML = m.label || m.tags?.length ? `<div>${esc(m.label)} ${tagChips(m.tags, false)}</div>` : ""; return; }
  el.innerHTML = `<div class="row"><input id="vs-label" type="text" class="grow" maxlength="120" placeholder="Name this set (optional)" value="${esc(m.label || "")}">` +
    `<button class="btn secondary sm" id="vs-label-save">Save name</button></div><div id="vs-tags"></div>`;
  const saveName = async () => {
    try {
      const a = await api("POST", "/api/vectors/update", { path: m.path, label: $("vs-label").value.trim() });
      m.label = a.label; toast("Name saved", "ok"); vsAfterEdit();
    } catch (e) { err(e); }
  };
  $("vs-label-save").addEventListener("click", saveName);
  $("vs-label").addEventListener("keydown", (e) => e.key === "Enter" && saveName());
  tagEditor($("vs-tags"), m.tags, tagSuggestions(), async (tags) => {
    const a = await api("POST", "/api/vectors/update", { path: m.path, tags });
    m.tags = a.tags; learnTags([m]); vsAfterEdit();
    return a.tags;
  });
}
// After a name/tag change of the selected set: list, tag filter, acquisition choices.
function vsAfterEdit() { fillTagFilter("vs-tag", vsRows()); vsFilter(); acqFillSets(); }
async function vsDelete(sets) {
  if (!sets.length) return;
  let used = 0;
  try {        // only for the message: campaigns record the set by path (or id)
    const { files } = await api("GET", "/api/traces/list"), names = new Set(sets.flatMap((m) => [m.filename, m.id]));
    used = files.filter((f) => names.has(String(f.vector_set || "").split("/").pop())).length;
  } catch (e) { /* ignore */ }
  const what = sets.length === 1 ? `<b>${esc(sets[0].label || sets[0].id)}</b> moves` : `<b>${sets.length} vector sets</b> move`;
  if (!(await dialog({ title: "Move to trash?", ok: "Move to trash", danger: true, body:
    `${what} to <code>data/vectors/.trash/</code> together with ${sets.length === 1 ? "its" : "their"} name and tags; restore from the Trash view.` +
    (used ? `<p class="help" style="margin:10px 0 0">${plural(used, "campaign")} in the library ${used === 1 ? "was" : "were"} captured with ${sets.length === 1 ? "this set" : "these sets"}. ` +
      `They keep working: each campaign file stores its own ciphertexts, expected keys and the seed.</p>` : "") }))) return;
  try { await api("POST", "/api/vectors/delete", { paths: sets.map((m) => m.path) }); toast(`Moved ${plural(sets.length, "vector set")} to the trash`, "ok"); } catch (e) { err(e); }
  vsRefresh();
}
async function vsRestore(sets) {
  if (!sets.length) return;
  try { await api("POST", "/api/vectors/restore", { paths: sets.map((m) => m.path) }); toast(`Restored ${plural(sets.length, "vector set")}`, "ok"); } catch (e) { err(e); }
  vsRefresh();
}
async function vsPurge(sets) {
  if (!sets.length) return;
  const what = sets.length === 1 ? `<b>${esc(sets[0].label || sets[0].id)}</b> is` : `<b>${sets.length} vector sets</b> are`;
  if (!(await dialog({ title: "Delete permanently?", ok: "Delete permanently", danger: true, body:
    `${what} deleted from disk. This cannot be undone, but a set can be generated again: the same settings and seed give the same vectors.` }))) return;
  try { const r = await api("POST", "/api/vectors/purge", { paths: sets.map((m) => m.path) }); toast(`Deleted ${plural(r.deleted, "vector set")}`, "ok"); } catch (e) { err(e); }
  vsRefresh();
}
async function vsEmptyTrash() {
  if (!vsTrashSets.length) return;
  if (!(await dialog({ title: "Empty the vector-set trash?", ok: "Delete all", danger: true,
    body: `${vsTrashSets.length === 1 ? "The vector set" : `All ${vsTrashSets.length} vector sets`} in <code>data/vectors/.trash/</code> ${vsTrashSets.length === 1 ? "is" : "are"} deleted from disk. This cannot be undone.` }))) return;
  try { const r = await api("POST", "/api/vectors/empty_trash", {}); toast(`Deleted ${plural(r.deleted, "vector set")}`, "ok"); } catch (e) { err(e); }
  vsRefresh();
}
async function vpLoad() {
  if (!vsSel) {
    $("vp-title").textContent = ""; $("vp-kpis").innerHTML = ""; $("vp-pager").textContent = "—";
    $("vp-table").querySelector("tbody").innerHTML = "";
    return;
  }
  try {
    const r = await api("POST", "/api/vectors/view", { path: vsSel.path, offset: vpOffset, limit: VP_PAGE, class: $("vp-class").value });
    const m = r.meta;
    $("vp-title").textContent = m.id;
    kpis("vp-kpis", [["purpose", m.mode.toUpperCase(), kindLabel(m)], ["vectors", m.n, m.mode === "tvla" ? `${m.n_fixed} fixed / ${m.n_random} random` : ""],
      ["FO accept", `${m.n_valid}/${m.n}`, "re-encryption check"], ["seed", short(m.seed, 12), "reproducible"], ["dk SHA3-256", short(m.dk_sha3_256, 12), fmtDate(m.created_unix)]]);
    $("vp-class").disabled = m.mode !== "tvla";
    const tb = $("vp-table").querySelector("tbody");
    tb.innerHTML = r.rows.map((x) => `<tr data-i="${x.index}"><td class="num">${x.index}</td><td>${m.mode === "tvla" ? (x.class ? '<span class="badge tvla">random</span>' : '<span class="badge">fixed</span>') : "—"}</td>` +
      `<td>${esc(x.ct_short)}</td><td>${esc(short(x.m, 14))}</td><td>${esc(short(x.k, 14))}</td><td class="${x.valid ? "pass" : "dim"}">${x.valid ? "accept" : "reject"}</td></tr>`).join("");
    tb.querySelectorAll("tr").forEach((tr) => tr.addEventListener("click", () => vpShow(+tr.dataset.i, tr)));
    $("vp-pager").textContent = r.total ? `${vpOffset + 1}–${Math.min(vpOffset + VP_PAGE, r.total)} of ${r.total}` : "0";
    $("vp-prev").disabled = vpOffset === 0;
    $("vp-next").disabled = vpOffset + VP_PAGE >= r.total;
  } catch (e) { err(e); }
}
async function vpShow(i, tr) {
  $("vp-table").querySelectorAll("tr").forEach((x) => x.classList.toggle("sel", x === tr));
  try {
    vpRow = await api("POST", "/api/vectors/row", { path: vsSel.path, index: i });
    const f = (label, hex) => `<div class="field-title">${label} <span class="dim">${hex.length / 2} B</span><button class="btn ghost sm" data-copy="${hex}">Copy</button></div><div class="hexbox">${hex}</div>`;
    $("vp-detail").innerHTML = `<div class="section-title">Vector #${i} — ${vpRow.valid ? "valid encapsulation" : "rejected (implicit rejection)"}</div>` +
      f("Ciphertext c", vpRow.ct) + (vpRow.m ? f("Message m", vpRow.m) : "") + f("Expected shared secret K", vpRow.k);
    $("vp-detail").querySelectorAll("[data-copy]").forEach((b) => b.addEventListener("click", () =>
      navigator.clipboard.writeText(b.dataset.copy).then(() => toast("Copied to the clipboard"))));
  } catch (e) { err(e); }
}
async function vsVerifySw() {
  if (!vsSel) return toast("Select a vector set first", "warn");
  await busy($("vs-verify-sw"), async () => {
    try {
      const r = await api("POST", "/api/vectors/verify_software", { path: vsSel.path });
      toast(r.ok ? `Software Decaps reproduces all ${r.checked} expected keys` : `Mismatch at rows ${r.mismatches.join(", ")}`, r.ok ? "ok" : "bad");
    } catch (e) { err(e); }
  });
}
let vbJob = null;
async function vbRun() {
  if (!vsSel) return toast("Select a vector set first", "warn");
  $("vb-run").classList.add("hidden"); $("vb-stop").classList.remove("hidden");
  $("vb-table").querySelector("tbody").innerHTML = ""; $("vb-verdict").className = "verdict";
  try {
    const { job_id } = await api("POST", "/api/vectors/verify_board", { path: vsSel.path, count: $("vb-count").value });
    vbJob = job_id;
    const j = await pollJob(job_id, (x) => progress("vb-progress", x.progress, x.total, x.message), 300);
    if (j.status !== "done") throw new Error(j.error);
    const r = j.result;
    $("vb-table").querySelector("tbody").innerHTML = r.rows.map((x) => `<tr><td class="num">${x.index}</td><td>${vsSel.mode === "tvla" ? (x.class ? "random" : "fixed") : "—"}</td>` +
      `<td class="${x.k_match ? "pass" : "fail"}">${x.k_match ? "✓ match" : "✗ " + esc(x.describe)}</td><td class="num">${x.cycles}</td></tr>`).join("");
    const all = r.passed === r.checked;
    $("vb-verdict").className = "verdict " + (all ? "good" : "bad");
    $("vb-verdict").textContent = `${r.passed}/${r.checked} outputs equal the FIPS 203 expected K · ` +
      (r.constant_time ? `constant time (${r.cycles_distinct[0]} cycles)` : `CYCLE_COUNT varied: ${r.cycles_distinct.join(", ")}`);
  } catch (e) { err(e); }
  vbJob = null;
  $("vb-run").classList.remove("hidden"); $("vb-stop").classList.add("hidden");
}

// =====================================================================================
// Acquisition page
// =====================================================================================
let acqJob = null, acqT0 = 0, liveSeq = -1, liveLast = null, captures = [], acqLastFile = null;
function acqFillSets() {
  const sel = $("acq-vector-set"), keep = sel.value || vsSel?.path;
  sel.innerHTML = vsSets.length ? vsSets.map((m) => `<option value="${esc(m.path)}">${esc(m.label || m.id)} · ${m.mode.toUpperCase()} · ${m.n}${tagSuffix(m)}</option>`).join("")
    : `<option value="">— generate a vector set first —</option>`;
  if (keep && vsSets.some((m) => m.path === keep)) sel.value = keep;
  acqSetHint();
}
function acqSetHint() {
  const m = vsSets.find((x) => x.path === $("acq-vector-set").value);
  $("acq-vs-hint").textContent = m ? `${m.label ? m.id + " · " : ""}${m.mode.toUpperCase()} · ${kindLabel(m)} · ${m.n} traces will be captured` : "";
}
async function acqRefreshResume() {
  try {
    const { files } = await api("GET", "/api/traces/list");
    const sel = $("acq-resume"), keep = sel.value;
    sel.innerHTML = `<option value="">— start a new campaign —</option>` + files.filter((f) => f.status === "resumable").map((f) =>
      `<option value="${esc(f.path)}">${esc(f.label || f.filename)} (${f.n_completed}/${f.n_traces_planned})${tagSuffix(f)}</option>`).join("");
    if ([...sel.options].some((o) => o.value === keep)) sel.value = keep;
  } catch (e) { /* ignore */ }
}
async function acqStart() {
  const resume = $("acq-resume").value;
  if (!resume && trigOk === false && !(await dialog({ title: "The trigger check failed",
      body: `Failed: <b>${trigFails.map(esc).join(", ") || "see the trigger check table"}</b>.<br><br>With the record cut short or ` +
            `not starting at the trigger, the traces miss or misplace the BMUL windows and cannot be used for CPA. ` +
            `Fix the scope's timebase / trigger position and run <i>Check trigger</i> again.`,
      ok: "Start anyway", danger: true }))) return;
  try {
    const { job_id } = await api("POST", "/api/acquire/start", { vector_set: resume ? "" : $("acq-vector-set").value,
      resume_file: resume, samples: $("acq-samples").value, resend_dk_every: $("acq-resend-dk").value });
    acqJob = job_id; acqT0 = Date.now(); liveSeq = -1; liveLast = null;
    $("acq-start").classList.add("hidden"); $("acq-stop").classList.remove("hidden"); $("acq-done-actions").classList.add("hidden");
    $("acq-kpis").innerHTML = "";
    chartEmpty("live-chart", "Waiting for the first trace…");
    toast("Acquisition started");
    acqPoll(); liveLoop();
  } catch (e) { err(e); }
}
async function acqPoll() {
  if (!acqJob) return;
  try {
    const j = await api("GET", "/api/jobs/status?job_id=" + acqJob);
    const el = (Date.now() - acqT0) / 1000, rate = j.progress && el > 0 ? j.progress / el : 0;
    const eta = rate ? Math.round((j.total - j.progress) / rate) : null;
    progress("acq-progress", j.progress, j.total, `${j.progress}/${j.total || "?"}${rate ? ` · ${rate.toFixed(1)} traces/s` : ""}${eta !== null ? ` · ${eta} s left` : ""} · ${j.message}`);
    $("pill-job").classList.toggle("hidden", j.status !== "running");
    $("pill-job-txt").textContent = `Acquiring ${j.progress}/${j.total}`;
    $("pill-job-bar").style.width = (j.total ? (100 * j.progress) / j.total : 0) + "%";
    $("nav-acq").classList.toggle("hidden", j.status !== "running");
    $("nav-acq").textContent = j.total ? Math.round((100 * j.progress) / j.total) + "%" : "…";
    if (j.status === "running") { setTimeout(acqPoll, 500); return; }
    const finished = acqJob;
    acqJob = null;
    liveFetch(finished);  // the live loop stops with the job: show the final trace and means
    $("acq-start").classList.remove("hidden"); $("acq-stop").classList.add("hidden");
    if (j.status === "done") {
      const r = j.result;
      acqLastFile = r.path;
      kpis("acq-kpis", [["traces", `${r.n_completed}/${r.n_traces_planned}`, j.message, r.stopped_early ? "warn" : "good"],
        ["K correct", pct(r.k_match_fraction), `${r.k_mismatches || 0} mismatch(es)`, r.k_mismatches ? "bad" : "good"],
        ["integrity", pct(r.ok_fraction), "per-trace checks"],
        ["CYCLE_COUNT", r.cycle_changes ? `${r.cycle_changes} changes` : "constant", "", r.cycle_changes ? "bad" : "good"]]);
      $("acq-done-actions").classList.remove("hidden");
      toast(`Acquisition ${j.message}: ${r.n_completed} traces`, r.stopped_early ? "warn" : "ok");
    } else {
      kpis("acq-kpis", [["error", "failed", j.error, "bad"]]);
      err(j.error);
    }
    acqRefreshResume();
    libRefresh(true);
  } catch (e) { err(e); setTimeout(acqPoll, 1500); }
}
// Live preview: a long poll -- the server answers as soon as a newer trace is captured --
// so every trace is drawn as it arrives, at most once per LIVE_MIN_MS (~10 redraws/s).
const LIVE_MIN_MS = 100, LIVE_WAIT_MS = 1500;
async function liveFetch(job, waitMs = 0) {
  try {
    const r = await api("GET", `/api/acquire/live?job_id=${job}&since=${liveSeq}&max_points=3000&wait_ms=${waitMs}`);
    if (r.ready && !r.unchanged) { liveSeq = r.seq; liveDraw(r); }
    return true;
  } catch (e) { return false; /* transient */ }
}
async function liveLoop() {
  if (!acqJob) return;
  const t0 = performance.now(), hidden = document.hidden;   // background tab: a slow, cheap refresh
  const ok = await liveFetch(acqJob, hidden ? 0 : LIVE_WAIT_MS);
  setTimeout(liveLoop, hidden || !ok ? 1000 : Math.max(0, LIVE_MIN_MS - (performance.now() - t0)));
}
function liveDraw(r) {
  liveLast = r || liveLast;
  r = liveLast;
  if (!r) return;
  const tvla = r.mode === "tvla", tr = [];
  if ($("live-show-mean").checked) {
    if (tvla) {
      if (r.means["0"]) tr.push(line(`fixed mean (n=${r.means["0"].n})`, C.s[1], r.means["0"].x, r.means["0"].y));
      if (r.means["1"]) tr.push(line(`random mean (n=${r.means["1"].n})`, C.s[2], r.means["1"].x, r.means["1"].y));
    } else if (r.means["1"]) tr.push(line(`mean (n=${r.means["1"].n})`, C.s[1], r.means["1"].x, r.means["1"].y, { width: 1.8 }));
  }
  if ($("live-show-last").checked) tr.push(line(`trace #${r.index}`, C.s[0], r.last.x, r.last.y, { width: 1, opacity: 0.8 }));
  const diff = tvla && $("live-show-diff").checked && r.mean_diff;
  if (diff) tr.push(line("fixed − random", C.s[3], r.mean_diff.x, r.mean_diff.y, { yaxis: "y2" }));
  const wb = axisBands(r.axis);
  $("live-sub").textContent = `trace #${r.index}${r.n_bad ? ` · ${r.n_bad} failed check(s)` : ""}`;
  chart("live-chart", tr, { height: vh(0.36, 280, 480), xTitle: xTitle(r.axis), yTitle: "amplitude", y2: !!diff, y2Title: "mean difference",
    shapes: wb.shapes, annotations: wb.annotations, uirevision: "live" });
}
let trigOk = null;   // last trigger check of this session: true / false / null (not run)
let trigFails = [];  // names of the checks that failed in that run
async function trigCheck() {
  if ($("trig-check").disabled) return toast("Connect the board and the oscilloscope first", "warn");
  await busy($("trig-check"), async () => {
    try {
      const r = await api("POST", "/api/acquire/trigger_check", {});
      trigOk = r.ok;
      trigFails = r.checks.filter((c) => c.ok === false).map((c) => c.name);
      $("trig-wrap").classList.remove("hidden");
      $("trig-table").innerHTML = r.checks.map((c) => `<tr><td>${esc(c.name)}</td><td class="${c.ok === true ? "pass" : c.ok === false ? "fail" : "dim"}">${c.ok === true ? "PASS" : c.ok === false ? "FAIL" : "skipped"}</td><td>${esc(c.detail)}</td></tr>`).join("");
      if (r.trace) { captures = captures.concat([{ trace: r.trace, axis: r.axis, captured_unix: Date.now() / 1000 }]).slice(-5); capDraw(); }
      toast(r.ok ? "Trigger OK: fires on the Decaps start and not otherwise" : "Trigger check failed — see the table", r.ok ? "ok" : "bad", 7000);
      applyState(S);
    } catch (e) { trigOk = false; applyState(S); err(e); }
  });
}
async function capOne() {
  await busy($("cap-one"), async () => {
    try {
      const r = await api("POST", "/api/acquire/capture_one", { max_points: 4000 });
      captures = captures.concat([r]).slice(-5);
      capDraw();
      const s = r.stats;
      const rs = r.record_start || {};
      kpis("cap-kpis", [["Decaps", r.k_match ? "K ✓" : "K ✗", r.describe, r.k_match ? "good" : "bad"], coverageKpi(r.axis),
        ["sample rate", rateTxt(r.axis.sample_rate_hz), `${s.n} samples`], ["range", `${s.min.toPrecision(3)} … ${s.max.toPrecision(3)}`, `mean ${s.mean.toPrecision(3)} ± ${s.std.toPrecision(2)}`],
        rs.s === null || rs.s === undefined ? null : ["trigger position", startTxt(rs.s), rs.ok ? "sample 0 = Decaps start" : rs.detail, rs.ok ? "good" : "bad"]]);
    } catch (e) { err(e); }
  });
}
function capDraw() {
  if (!captures.length) { $("cap-kpis").innerHTML = ""; return chartEmpty("cap-chart", "No capture yet. The last 5 captures are overlaid for comparison."); }
  const last = captures[captures.length - 1];
  const tr = captures.map((c, i) => line(`capture ${i + 1} · ${new Date(c.captured_unix * 1000).toLocaleTimeString()}`, C.s[(i + 4) % 8],
    c.trace.x, c.trace.y, { width: c === last ? 1.5 : 1, opacity: c === last ? 1 : 0.5 }));
  const wb = axisBands(last.axis);
  chart("cap-chart", tr, { height: vh(0.34, 260, 440), xTitle: xTitle(last.axis), yTitle: "amplitude", shapes: wb.shapes, annotations: wb.annotations, uirevision: "cap" });
}

// =====================================================================================
// Trace Library page
// =====================================================================================
let libFiles = [], libSel = null, libTrash = false, libChecks = new Set(), libTable, libMode, libDetail = null, tvData = null;
const statusBadge = (st) => ({ complete: '<span class="badge ok">complete</span>', resumable: '<span class="badge warn">resumable</span>',
  recording: '<span class="badge info">● recording</span>', partial: '<span class="badge warn">partial</span>', unreadable: '<span class="badge bad">unreadable</span>' }[st] || esc(st));
const modeBadge = (m) => `<span class="badge ${m === "cpa" ? "cpa" : m === "tvla" ? "tvla" : ""}">${esc((m || "?").toUpperCase())}</span>`;

async function libRefresh(quiet) {
  try {
    const r = await api("GET", "/api/traces/list" + (libTrash ? "?trash=1" : ""));
    libFiles = r.files;
    const st = r.storage;
    $("lib-storage").textContent = `${st.n_files} campaign(s) · ${fmtBytes(st.bytes)} · ${fmtBytes(st.disk_free)} free`;
    $("lib-trash-toggle").textContent = libTrash ? "← Back to library" : `Trash${st.n_trash ? ` (${st.n_trash})` : ""}`;
    $("lib-empty-trash").classList.toggle("hidden", !libTrash || !libFiles.length);
    $("nav-library").textContent = st.n_files || "";
    learnTags(libFiles);
    fillTagFilter("lib-tag", libFiles);
    libFilter();
    if (libSel && !libFiles.some((f) => f.path === libSel)) { libSel = null; libDetailEmpty(); }
    else if (libSel && !quiet) libShow(libSel);
  } catch (e) { if (!quiet) err(e); }
}
function libFilter() {
  const q = $("lib-search").value.trim().toLowerCase(), m = libMode.get(), st = $("lib-status").value, tag = $("lib-tag").value;
  const rows = libFiles.filter((f) => (!m || f.mode === m) && (!st || f.status === st) && (!tag || (f.tags || []).includes(tag)) &&
    (!q || [f.filename, f.label, f.notes, f.vector_set, f.strategy, ...(f.tags || [])].join(" ").toLowerCase().includes(q)));
  pruneChecks(libChecks, rows, (f) => f.path);
  libTable.set(rows);
  libBulk();
}
function libBulk() {
  // a campaign being recorded cannot be tagged or moved
  const sel = libTable.visible().filter((f) => libChecks.has(f.path) && f.status !== "recording");
  bulkBar("lib-bulk", sel.length, libTrash ? [
    ["Restore", "secondary", () => libRestore(sel)],
    ["Delete permanently", "danger", () => libPurge(sel)],
  ] : [
    ["Tags…", "secondary", async () => {
      const ok = sel.filter((f) => f.status !== "unreadable"), t = await bulkTagDialog(ok);
      if (!t) return;
      try { await api("POST", "/api/traces/tag", { paths: ok.map((f) => f.path), ...t }); toast(`Tags updated on ${plural(ok.length, "campaign")}`, "ok"); } catch (e) { err(e); }
      libRefresh();
    }],
    ["Move to trash", "ghost", () => libDelete(sel)],
  ], () => { libChecks.clear(); libTable.redraw(); libBulk(); });
}
const libWhat = (rows, verb) => rows.length === 1 ? `<b>${esc(rows[0].label || rows[0].filename)}</b> ${verb}s` : `<b>${rows.length} campaigns</b> ${verb}`;
async function libDelete(rows) {
  if (!rows.length) return;
  if (!(await dialog({ title: "Move to trash?", ok: "Move to trash", danger: true, body:
    `${libWhat(rows, "move")} to <code>data/traces/.trash/</code> with ${rows.length === 1 ? "its" : "their"} name, notes and tags. Restore from the Trash view; analysis reports stay.` }))) return;
  try { await api("POST", "/api/traces/delete", { paths: rows.map((f) => f.path) }); toast(`Moved ${plural(rows.length, "campaign")} to the trash`, "ok"); } catch (e) { err(e); }
  if (rows.some((f) => f.path === libSel)) { libSel = null; libDetailEmpty(); }
  libRefresh(); acqRefreshResume();
}
async function libRestore(rows) {
  if (!rows.length) return;
  try { await api("POST", "/api/traces/restore", { paths: rows.map((f) => f.path) }); toast(`Restored ${plural(rows.length, "campaign")}`, "ok"); } catch (e) { err(e); }
  if (rows.some((f) => f.path === libSel)) { libSel = null; libDetailEmpty(); }
  libRefresh(); acqRefreshResume();
}
function purgeText(rows) {
  const bytes = rows.reduce((a, f) => a + (f.size_bytes || 0), 0), reps = rows.reduce((a, f) => a + (f.n_reports || 0), 0);
  return ` (${fmtBytes(bytes)}) ${rows.length === 1 ? "is" : "are"} deleted from disk` +
    (reps ? `, together with the ${plural(reps, "analysis report")} made from ${rows.length === 1 ? "it" : "them"}` : "") + ". This cannot be undone.";
}
async function libPurge(rows) {
  if (!rows.length) return;
  if (!(await dialog({ title: "Delete permanently?", ok: "Delete permanently", danger: true,
    body: (rows.length === 1 ? `<b>${esc(rows[0].label || rows[0].filename)}</b>` : `<b>${rows.length} campaigns</b>`) + purgeText(rows) }))) return;
  try {
    const r = await api("POST", "/api/traces/purge", { paths: rows.map((f) => f.path) });
    toast(`Deleted ${plural(r.deleted, "campaign")}${r.reports ? ` and ${plural(r.reports, "report")}` : ""}`, "ok");
  } catch (e) { err(e); }
  if (rows.some((f) => f.path === libSel)) { libSel = null; libDetailEmpty(); }
  libRefresh();
}
async function libEmptyTrash() {
  if (!libTrash || !libFiles.length) return;
  if (!(await dialog({ title: "Empty the trash?", ok: "Delete all", danger: true,
    body: `${libFiles.length === 1 ? "The campaign" : `All ${libFiles.length} campaigns`} in <code>data/traces/.trash/</code>` + purgeText(libFiles) }))) return;
  try {
    const r = await api("POST", "/api/traces/empty_trash", {});
    toast(`Deleted ${plural(r.deleted, "campaign")}${r.reports ? ` and ${plural(r.reports, "report")}` : ""}`, "ok");
  } catch (e) { err(e); }
  libSel = null; libDetailEmpty(); libRefresh();
}
function libDetailEmpty() {
  $("lib-detail").innerHTML = `<div class="empty"><div class="t">No campaign selected</div>Pick a campaign on the left to see its details.</div>`;
  $("lib-viewer").classList.add("hidden");
}
async function libShow(path) {
  if (tvPlay && tvPlay.file !== path) tvPlayReset();
  libSel = path;
  libTable.select(path);
  const f = libFiles.find((x) => x.path === path);
  if (!f) return;
  if (libTrash) {
    $("lib-detail").innerHTML = `<div class="detail-head"><div style="flex:1;min-width:0"><div class="title">${esc(f.label || f.filename)}</div><div class="file">${esc(f.filename)} · in trash</div></div>` +
      `<div>${modeBadge(f.mode)}</div></div>` + (f.tags?.length ? `<div style="margin-top:8px">${tagChips(f.tags, false)}</div>` : "") +
      `<div class="kv" style="margin-top:12px"><span class="k">Traces</span><span class="v">${f.n_completed ?? "—"}</span><span class="k">Size</span><span class="v">${fmtBytes(f.size_bytes)}</span>` +
      `<span class="k">Captured</span><span class="v">${fmtDate(f.created_unix)}</span><span class="k">Analysis reports</span><span class="v">${f.n_reports || 0}</span></div>` +
      `<div class="btn-row"><button class="btn primary" id="lib-restore">Restore to library</button><span class="grow"></span><button class="btn danger sm" id="lib-purge">Delete permanently</button></div>`;
    $("lib-restore").addEventListener("click", () => libRestore([f]));
    $("lib-purge").addEventListener("click", () => libPurge([f]));
    $("lib-viewer").classList.add("hidden");
    return;
  }
  if (f.status === "recording" || f.status === "unreadable") {
    $("lib-detail").innerHTML = `<div class="empty"><div class="t">${esc(f.filename)}</div>${f.status === "recording" ? "Being recorded right now — details appear when the acquisition stops." : "This file cannot be read."}</div>`;
    return;
  }
  try {
    const d = await api("POST", "/api/traces/detail", { path });
    libDetail = d;
    const rc = d.recipe || {}, bd = d.board || {}, sc = d.scope || {};
    $("lib-detail").innerHTML = `
      <div class="detail-head"><div style="flex:1;min-width:0"><div class="title">${esc(d.label || d.filename)}</div><div class="file">${esc(d.filename)}</div></div>
        <div>${modeBadge(d.mode)} ${statusBadge(d.status)}</div></div>
      <div class="btn-row">
        <button class="btn primary sm" id="ld-analyse">Analyse</button>
        <button class="btn secondary sm" id="ld-view">View traces</button>
        ${d.status === "resumable" ? '<button class="btn secondary sm" id="ld-resume">Resume capture</button>' : ""}
        <div class="dropdown"><button class="btn secondary sm" id="ld-dl">Export ▾</button><div class="dropdown-menu">
          <a id="ld-dl-h5">Full campaign (.hdf5, ${fmtBytes(d.size_bytes)})</a><a id="ld-dl-npz">Slice of traces (.npz)…</a></div></div>
        <span class="grow"></span><button class="btn ghost sm" id="ld-delete">Move to trash</button></div>
      <div class="kpis compact" id="ld-kpis" style="margin-top:14px"></div>
      <div class="section-title">Name, tags &amp; notes</div>
      <div class="row"><input id="ld-label" type="text" class="grow" maxlength="120" placeholder="Name this campaign" value="${esc(d.label)}"><button class="btn secondary sm" id="ld-save">Save</button></div>
      <div id="ld-tags" style="margin-top:8px"></div>
      <textarea id="ld-notes" rows="2" placeholder="Notes: probe position, scope settings, observations…" style="margin-top:8px">${esc(d.notes)}</textarea>
      <div class="section-title">Mean ± σ of the first traces</div>
      <div class="chart" id="ld-preview"><div class="chart-empty">loading…</div></div>
      <div class="section-title">Provenance</div>
      <div class="kv" id="ld-prov"></div>
      ${d.cycles_distinct > 1 ? `<div class="section-title">CYCLE_COUNT distribution</div><div class="table-wrap short"><table class="t"><thead><tr><th class="num">cycles</th><th class="num">traces</th></tr></thead><tbody>${d.cycles.map((c) => `<tr><td class="num">${c.cycles}</td><td class="num">${c.count}</td></tr>`).join("")}</tbody></table></div>` : ""}
      <div class="section-title">Analysis reports</div>
      <div class="list" id="ld-reports">${d.reports.length ? d.reports.map((r, i) => `<div class="list-item" data-i="${i}">${modeBadge(r.kind)}<span>${esc(r.headline || r.kind)}</span><span class="meta">${fmtAgo(r.analysis_timestamp_unix)}</span></div>`).join("") : '<div class="muted" style="font-size:12px">none yet</div>'}</div>`;
    kpis("ld-kpis", [["traces", `${d.n_completed}`, d.n_completed < d.n_traces_planned ? `of ${d.n_traces_planned} planned` : "complete"],
      ["K correct", pct(d.k_match_fraction), d.k_match_fraction === null || d.k_match_fraction === undefined ? "no expected K stored" : "vs FIPS 203", d.k_match_fraction === 1 ? "good" : d.k_match_fraction === null || d.k_match_fraction === undefined ? "" : "bad"],
      ["CYCLE_COUNT", d.cycles_distinct === 1 ? d.cycles[0].cycles : `${d.cycles_distinct} values`, d.cycles_distinct === 1 ? "constant" : "varies!", d.cycles_distinct === 1 ? "good" : "bad"],
      ["integrity", pct(d.ok_fraction), d.n_failed_checks ? `${d.n_failed_checks} failed` : "all checks passed"],
      coverageKpi(d.axis), ["size", fmtBytes(d.size_bytes), `${d.n_samples} samples/trace`]]);
    kv("ld-prov", [["Captured", fmtDate(d.created_unix)], ["Vector set", d.vector_set ? String(d.vector_set).split("/").pop() : "—"],
      ["Vectors", rc.mode ? `${rc.mode.toUpperCase()} · ${kindLabel(rc)}` : d.strategy], ["Seed", short(rc.seed, 32)],
      ["Board", bd.version ? `core ${bd.version} · ${bd.port} @ ${bd.baud}` : "—"], ["Scope", sc.id ? `${sc.id}${sc.channel ? " · " + sc.channel : ""}` : "—"],
      ["Sample rate", rateTxt(d.sample_rate_hz)], ["Core clock", d.core_mhz ? d.core_mhz + " MHz" : "—"], ["dk hash", d.dk_hash]]);
    $("ld-analyse").addEventListener("click", () => { showPage("analysis"); anSelectDataset(path); });
    $("ld-view").addEventListener("click", () => { tvOpen(); $("lib-viewer").scrollIntoView({ behavior: "smooth" }); });
    if ($("ld-resume")) $("ld-resume").addEventListener("click", () => { showPage("acquire"); acqRefreshResume().then(() => { $("acq-resume").value = path; $("acq-resume").closest("details").open = true; applyState(S); }); });
    $("ld-dl").addEventListener("click", (e) => { e.stopPropagation(); $("ld-dl").parentElement.classList.toggle("open"); });
    $("ld-dl-h5").addEventListener("click", () => download("/api/traces/download", { path }));
    $("ld-dl-npz").addEventListener("click", async () => {
      const ok = await dialog({ title: "Export a slice of traces", ok: "Export", body:
        `<div class="cols-2"><div><label class="f">First trace</label><input id="dlg-start" type="text" value="0"></div><div><label class="f">Number of traces</label><input id="dlg-count" type="text" value="${Math.min(1000, d.n_completed)}"></div></div>` +
        `<p class="help" style="margin-top:10px">.npz with traces, time axis, ciphertexts, outputs, expected K, cycles, classes and the campaign metadata.</p>` });
      if (ok) download("/api/traces/export", { path, start: $("dlg-start").value, count: $("dlg-count").value });
    });
    $("ld-delete").addEventListener("click", () => libDelete([f]));
    tagEditor($("ld-tags"), d.tags, tagSuggestions(), async (tags) => {
      const r = await api("POST", "/api/traces/update", { path, tags });
      const row = libFiles.find((x) => x.path === path);       // the list may have been reloaded meanwhile
      d.tags = r.tags; if (row) row.tags = r.tags; learnTags([d]);
      fillTagFilter("lib-tag", libFiles); libFilter();
      return r.tags;
    });
    $("ld-save").addEventListener("click", async () => {
      try {
        await api("POST", "/api/traces/update", { path, label: $("ld-label").value, notes: $("ld-notes").value });
        d.label = $("ld-label").value.trim(); d.notes = $("ld-notes").value;
        $("lib-detail").querySelector(".detail-head .title").textContent = d.label || d.filename;
        $("tv-sub").textContent = d.label || d.filename;
        toast("Saved", "ok"); libRefresh(true);
      } catch (e) { err(e); }
    });
    $("ld-reports").querySelectorAll(".list-item").forEach((el) => el.addEventListener("click", () => {
      showPage("analysis"); anSelectDataset(path, d.reports[+el.dataset.i].path);
    }));
    libPreview(path);
    if (!$("lib-viewer").classList.contains("hidden")) tvOpen();
  } catch (e) { err(e); }
}
async function libPreview(path) {
  try {
    const r = await api("POST", "/api/analysis/raw_traces", { file: path, count: 2, stats: true, stats_n: 300, max_points: 1500 });
    const tr = [];
    const col = { all: ["#38bdf82e", C.s[0]], fixed: ["#4ade802e", C.s[1]], random: ["#f871712e", C.s[2]] };
    Object.entries(r.stats || {}).forEach(([n, s]) => { const [f, c] = col[n] || col.all; tr.push(...band(`${n} ±σ`, f, s.x, s.lo, s.hi), line(`${n} mean (n=${s.n})`, c, s.x, s.mean)); });
    const wb = axisBands(r.axis);
    chart("ld-preview", tr, { height: 220, xTitle: xTitle(r.axis), shapes: wb.shapes, annotations: wb.annotations, margin: { l: 48, r: 10, t: 6, b: 38 } });
  } catch (e) { chartEmpty("ld-preview", e.message); }
}
function tvOpen() {
  if (!libDetail) return;
  $("lib-viewer").classList.remove("hidden");
  $("tv-sub").textContent = libDetail.label || libDetail.filename;
  $("tv-cls").disabled = libDetail.mode !== "tvla";
  tvLoad();
}
async function tvLoad() {
  if (!libSel) return;
  if (tvPlay) tvPlayReset();
  const idx = $("tv-indices").value.trim();
  const body = { file: libSel, max_points: 4000, stats: $("tv-stats").checked, stats_n: 500 };
  if (idx) body.indices = idx.split(/[ ,]+/).map((s) => parseInt(s, 10)).filter((n) => !isNaN(n));
  else Object.assign(body, { start: $("tv-start").value, count: $("tv-count").value, cls: $("tv-cls").value });
  await busy($("tv-load"), async () => {
    try { tvData = await api("POST", "/api/analysis/raw_traces", body); tvDraw(); } catch (e) { chartEmpty("tv-chart", e.message); }
  });
}
function tvDraw() {
  const r = tvData;
  if (!r) return;
  const tr = [], col = { all: ["#38bdf826", C.s[0]], fixed: ["#4ade8026", C.s[1]], random: ["#f8717126", C.s[2]] };
  Object.entries(r.stats || {}).forEach(([n, s]) => { const [f, c] = col[n] || col.all; tr.push(...band(`${n} ±σ (n=${s.n})`, f, s.x, s.lo, s.hi), line(`${n} mean`, c, s.x, s.mean, { width: 1.8 })); });
  r.traces.forEach((t, i) => tr.push(line(`#${t.index}${t.class === null ? "" : t.class ? " random" : " fixed"}${t.ok ? "" : " ✗"}`, C.s[(i + 3) % 8], t.x, t.y, { width: 1, opacity: 0.85 })));
  const wb = $("tv-bands").checked ? axisBands(r.axis) : { shapes: [], annotations: [] };
  chart("tv-chart", tr, { height: vh(0.44, 300, 560), xTitle: xTitle(r.axis), yTitle: "amplitude", shapes: wb.shapes, annotations: wb.annotations });
  $("tv-note").textContent = `${r.traces.length} trace(s) of ${r.n_completed} · ${rateTxt(r.axis.sample_rate_hz)} · each line is a min/max envelope (narrow peaks are kept) · ` +
    r.traces.map((t) => `#${t.index}: ${t.cycles} cycles${t.ok ? "" : " (failed check)"}`).join(", ");
}
function tvStep(d) {
  const n = Math.max(1, parseInt($("tv-count").value, 10) || 4);
  $("tv-indices").value = "";
  $("tv-start").value = Math.max(0, (parseInt($("tv-start").value, 10) || 0) + d * n);
  tvLoad();
}
function tvCsv() {
  if (!tvData) return toast("Plot something first", "warn");
  const r = tvData, n = r.traces[0].x.length;
  const rows = [["x_" + r.axis.unit].concat(r.traces.map((t) => "trace_" + t.index)).join(",")];
  for (let j = 0; j < n; j++) rows.push([r.traces[0].x[j]].concat(r.traces.map((t) => t.y[j])).join(","));
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([rows.join("\n")], { type: "text/csv" }));
  a.download = "traces_envelope.csv"; document.body.appendChild(a); a.click(); a.remove();
}

// --- Trace player: one trace per frame, in capture order --------------------------------
// The x axis is time after the trigger, the same for every trace (each trace is a triggered
// snapshot of the same window), so it stays put; only the waveform changes from frame to
// frame. Traces are fetched in batches ahead of the play position; the y range only ever
// grows, so the plot does not jump between frames. Positions count within the class filter.
const TV_BATCH = 20;
let tvPlay = null;   // {file, cls, pos, end, buf: Map pos -> trace, stats, axis, n, yr, running, timer, pending}
function tvPlayReset() {
  if (tvPlay) clearTimeout(tvPlay.timer);
  tvPlay = null;
  $("tv-play").textContent = "▶ Play";
  $("tv-scrub").classList.add("hidden");
}
function tvPlayState() {
  const cls = $("tv-cls").value;
  if (!tvPlay || tvPlay.file !== libSel || tvPlay.cls !== cls) {
    tvPlayReset();
    tvPlay = { file: libSel, cls, pos: Math.max(0, parseInt($("tv-start").value, 10) || 0), end: null,
               buf: new Map(), stats: null, axis: null, n: libDetail?.n_completed || 0, yr: null, running: false, pending: new Set() };
  }
  return tvPlay;
}
async function tvFetch(p, start) {
  if (p.pending.has(start) || (p.end !== null && start >= p.end)) return;
  p.pending.add(start);
  try {
    const wantStats = !p.stats && $("tv-stats").checked;
    const r = await api("POST", "/api/analysis/raw_traces", { file: p.file, start, count: TV_BATCH, cls: p.cls,
      max_points: 4000, stats: wantStats, stats_n: 500 });
    if (tvPlay !== p) return;
    p.axis = r.axis;
    if (wantStats) p.stats = r.stats || {};
    r.traces.forEach((tr, k) => { p.buf.set(start + k, tr); tvGrowRange(p, tr.y); });
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
  const tr = [], col = { all: ["#38bdf826", C.s[1]], fixed: ["#4ade8026", C.s[1]], random: ["#f8717126", C.s[2]] };
  if ($("tv-stats").checked && p.stats) Object.entries(p.stats).forEach(([n, s]) => { const [f, c] = col[n] || col.all;
    tr.push(...band(`${n} ±σ (n=${s.n})`, f, s.x, s.lo, s.hi), line(`${n} mean`, c, s.x, s.mean, { width: 1.4, opacity: 0.8 })); });
  const prev = p.buf.get(p.pos - 1);   // the previous frame, faint: a little persistence, like a scope
  if (prev) tr.push(line(`#${prev.index}`, C.s[0], prev.x, prev.y, { width: 1, opacity: 0.18 }));
  tr.push(line(`#${t.index}${t.class === null ? "" : t.class ? " random" : " fixed"}${t.ok ? "" : " ✗"}`, C.s[0], t.x, t.y, { width: 1.1 }));
  const wb = $("tv-bands").checked ? axisBands(p.axis) : { shapes: [], annotations: [] };
  chart("tv-chart", tr, { height: vh(0.44, 300, 560), xTitle: xTitle(p.axis), yTitle: "amplitude", yRange: p.yr, shapes: wb.shapes,
    annotations: wb.annotations, uirevision: "tv-play:" + p.file });
  const n = p.end !== null ? p.end : p.n;
  $("tv-pos").max = Math.max(0, n - 1); $("tv-pos").value = p.pos;
  $("tv-pos-txt").textContent = `#${t.index} · ${p.pos + 1} / ${n || "?"}`;
  $("tv-note").textContent = `trace #${t.index}: ${t.cycles} cycles${t.ok ? "" : " (failed check)"} · ct ${t.ct_preview} · ` +
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
      if (tvPlay === p) toast("End of the campaign — press Play to start again from the first trace");
      if (tvPlay === p) p.pos = 0;
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
  if (!libSel) return toast("Pick a campaign first", "warn");
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

// =====================================================================================
// Analysis page
// =====================================================================================
let anFiles = [], anReports = [], anModels = [], anRprevDesc = {}, anOracle;
const anModelOf = (name) => anModels.find((m) => m.name === name);
let cur = null;          // report being shown (cpa or tvla)
let insp = null;         // {poly, coeff} selected in the CPA inspector
const ccCache = new Map();
let heatMetric = "corr";
let anJob = null;        // running analysis job
let anDrawn = new Set(); // tabs of the current report already drawn (Plotly needs a visible container)

const chip = (k, v, cls = "") => `<span class="chip ${cls}">${esc(k)} <b>${esc(v)}</b></span>`;
const fileOpt = (f) => `<option value="${esc(f.path)}">${esc(f.label || f.filename)} · ${f.mode.toUpperCase()} · ${f.n_completed} traces${tagSuffix(f)}</option>`;
function anFile() { return anFiles.find((f) => f.path === $("an-dataset").value); }

async function anLoadFiles() {
  const { files } = await api("GET", "/api/traces/list");
  anFiles = files.filter((f) => !["recording", "unreadable"].includes(f.status));
  learnTags(anFiles);
  const sel = $("an-dataset"), keep = sel.value;
  sel.innerHTML = anFiles.length ? anFiles.map(fileOpt).join("") : `<option value="">— no campaigns captured yet —</option>`;
  if (keep && anFiles.some((f) => f.path === keep)) sel.value = keep;
}
// Page entry: keep what is shown, pick up new campaigns and reports.
async function anRefreshDatasets() {
  try {
    await anLoadFiles();
    if (cur && cur.file === $("an-dataset").value) { anMeta(); await anReportsList(cur.report_path); }
    else await anDatasetChanged();
  } catch (e) { err(e); }
}
async function anSelectDataset(path, reportPath) {
  try { await anLoadFiles(); } catch (e) { return err(e); }
  if (anFiles.some((f) => f.path === path)) { $("an-dataset").value = path; await anDatasetChanged(reportPath); }
}
function anMeta() {
  const f = anFile();
  $("an-dataset-hint").innerHTML = f ? [modeBadge(f.mode), chip("traces", f.n_completed), chip("samples", f.n_samples),
    chip("K correct", pct(f.k_match_fraction), f.k_match_fraction === 1 ? "good" : f.k_match_fraction === null || f.k_match_fraction === undefined ? "" : "bad"),
    f.n_fixed !== null && f.n_fixed !== undefined ? chip("fixed / random", `${f.n_fixed} / ${f.n_random}`) : "",
    f.has_recipe ? "" : chip("true key", "not stored", "warn"), chip("captured", fmtAgo(f.created_unix)), tagChips(f.tags, false)].join("") : "";
}
async function anDatasetChanged(reportPath) {
  const f = anFile();
  anMeta();
  anSetupFor(f);
  const reports = await anReportsList(reportPath);
  if (!f) { anConfig(false); return anEmpty("No campaigns yet", 'Capture one on the <a data-goto="acquire">Acquisition</a> page first.'); }
  if (reportPath || reports.length) { anConfig(false); return anOpenReport(reportPath || reports[0].path); }
  anConfig(true);
  anEmpty("No analysis of this campaign yet", `Choose the settings above and press <b>${f.mode === "cpa" ? "Run CPA" : "Run TVLA"}</b>.`);
}
function anSetupFor(f) {
  const cpa = !f || f.mode === "cpa";
  $("an-cpa-setup").classList.toggle("hidden", !cpa);
  $("an-cpa-scoring").classList.toggle("hidden", !cpa);
  $("an-run-n").textContent = cpa ? "3" : "1";
  $("an-config-title").textContent = cpa ? "New CPA analysis" : "New TVLA analysis";
  $("an-config-sub").textContent = f ? (f.label || f.filename) : "";
  $("an-run").textContent = cpa ? "Run CPA" : "Run TVLA";
  $("an-run").disabled = !f || !!anJob;
  $("an-new").disabled = !f;
  if (f) {
    const b = $("an-oracle").querySelector('[data-v="vector_set"]');
    b.disabled = !f.has_recipe;
    b.title = f.has_recipe ? "" : "this campaign stores no vector-set recipe";
    if (!f.has_recipe && anOracle.get() === "vector_set") { anOracle.set(""); anOracleChanged(""); }
  }
}
function anConfig(open) {
  $("an-config").classList.toggle("hidden", !open);
  $("an-new").classList.toggle("hidden", open);
}
// "model / R_prev" of a CPA report (the server resolves older reports' preset / target names)
const modelTxt = (r) => (r.model || r.model_config?.model || r.model_config?.preset || "?") + (r.rprev ? " / " + r.rprev : "");
const reportLabel = (r) => r.kind === "cpa" ? `CPA · ${modelTxt(r)} · ${r.model_config?.oracle_assisted ? "scored" : "blind"}` : "TVLA · Welch t-test";
async function anReportsList(selectPath) {
  const f = anFile();
  anReports = f ? (await api("POST", "/api/results/list", { file: f.path }).catch(() => ({ results: [] }))).results : [];
  $("an-report").innerHTML = anReports.length
    ? anReports.map((r, i) => `<option value="${esc(r.path)}">${esc(reportLabel(r))} — ${fmtDate(r.analysis_timestamp_unix)}${i === 0 ? "  (latest)" : ""}</option>`).join("")
    : `<option value="">— no saved reports —</option>`;
  $("an-report").disabled = !anReports.length;
  $("an-more-btn").disabled = !anReports.length;
  const want = selectPath || cur?.report_path;
  if (want && anReports.some((r) => r.path === want)) $("an-report").value = want;
  return anReports;
}
function anEmpty(t, body) {
  cur = null; insp = null;
  $("an-results").innerHTML = `<div class="card"><div class="empty"><div class="t">${t}</div>${body}</div></div>`;
  $("an-results").querySelectorAll("[data-goto]").forEach((a) => a.addEventListener("click", () => showPage(a.dataset.goto)));
}
async function anOpenReport(path) {
  try {
    const rep = await api("POST", "/api/results/load", { path });
    rep.report_path = path;                       // where it was loaded from
    if (anReports.some((r) => r.path === path)) $("an-report").value = path;
    rep.kind === "cpa" ? renderCPA(rep) : renderTVLA(rep);
  } catch (e) { err(e); }
}
async function anReportAction(act) {
  $("an-more").classList.remove("open");
  if (!cur) return toast("Open a report first", "warn");
  if (act === "json") return download("/api/analysis/download", { path: cur.report_path });
  if (act === "csv") return cur.results_csv ? download("/api/analysis/download", { path: cur.results_csv }) : toast("This report has no CSV export", "warn");
  if (act === "rerun") { anPrefill(cur); anConfig(true); $("an-config").scrollIntoView({ behavior: "smooth", block: "nearest" }); return; }
  if (act === "delete") {
    if (!(await dialog({ title: "Delete this report?", body: `<b>${esc(reportLabel(cur))}</b> from ${esc(fmtDate(cur.analysis_timestamp_unix))} and its CSV files are removed from <code>data/analysis/</code>. The campaign itself is not touched.`, ok: "Delete", danger: true }))) return;
    try {
      await api("POST", "/api/results/delete", { path: cur.report_path });
      toast("Report deleted", "ok");
      cur = null;
      await anDatasetChanged();
    } catch (e) { err(e); }
  }
}
// Fill the configuration from a report ("Re-run with these settings").
function anPrefill(r) {
  const mc = r.model_config || {};
  $("an-max-traces").value = mc.max_traces_requested ?? 0;
  if (r.kind !== "cpa") return;
  if (anModelOf(r.model)) { $("an-model").value = r.model; anModel(r.rprev); }
  else toast(`The model of this report (${modelTxt(r)}) is not in cpa_toolkit/models/ any more`, "warn");
  const f = anFile();
  const v = r.oracle_assisted ? (f && f.has_recipe ? "vector_set" : "file") : "";
  anOracle.set(v); anOracleChanged(v);
  $("an-curve").checked = !!r.curve && !!v;
}

// model selection: the files in cpa_toolkit/models/, one per model
async function anLoadModels(quiet) {
  try {
    const r = await api("GET", "/api/model/list"), keep = $("an-model").value, keepRprev = $("an-rprev").value;
    anModels = r.models; anRprevDesc = r.rprevs;
    const fams = [...new Set(anModels.map((m) => m.family || "other"))];
    $("an-model").innerHTML = fams.map((f) => `<optgroup label="${esc(f)}">` + anModels.filter((m) => (m.family || "other") === f)
      .map((m) => `<option value="${esc(m.name)}">${esc(m.name)} — ${esc(m.title)}</option>`).join("") + "</optgroup>").join("");
    const name = anModelOf(keep) ? keep : r.default_model;
    $("an-model").value = name;
    anModel(name === keep ? keepRprev : r.default_rprev);
    $("model-status").textContent = `${anModels.length} models in cpa_toolkit/models/` + (r.errors.length ? ` · ${r.errors.length} file(s) failed to load` : "");
    r.errors.forEach((e) => toast(`${e.file}: ${e.error}`, "bad", 10000));
    if (!quiet) toast(`${anModels.length} leakage models loaded`, "ok");
  } catch (e) { err(e); }
}
function anModel(rprev) {
  const m = anModelOf($("an-model").value);
  $("model-selftest-wrap").classList.add("hidden");
  if (!m) { $("an-model-detail").innerHTML = ""; return; }
  $("an-model-detail").innerHTML = [["Equation", m.equation],
    [m.aligned ? "Flops" : "Register", m.aligned ? `${m.bits} across the pipeline${m.rtl_register ? " · " + m.rtl_register : ""}`
                                                 : `${m.register} (${m.bits} bit)${m.rtl_register ? " · " + m.rtl_register : ""}`],
    ["Stage", m.pipeline_stage], ["Needs", m.required_inputs], ["Limits", m.limitations], ["File", "cpa_toolkit/" + m.file]].filter(([, v]) => v)
    .map(([k, v]) => `<div class="md-row"><span class="md-k">${k}</span><span class="md-v${k === "Limits" || k === "Needs" ? " prose" : ""}">${esc(v)}</span></div>`).join("");
  $("an-rprev-wrap").classList.toggle("hidden", !m.rprevs.length);
  $("an-aligned-wrap").classList.toggle("hidden", !m.aligned);
  $("an-rprev").innerHTML = m.rprevs.map((x) => `<option value="${esc(x.name)}" title="${esc(x.desc)}">${esc(x.name)} — ${esc(x.desc.replace(/^R_prev = /, ""))}</option>`).join("");
  if (typeof rprev === "string" && m.rprevs.some((x) => x.name === rprev)) $("an-rprev").value = rprev;
}
const anRprev = () => (anModelOf($("an-model").value)?.rprevs.length ? $("an-rprev").value : "");
function anOracleChanged(v) {
  $("an-dkref").classList.toggle("hidden", v !== "file");
  $("an-oracle-warn").textContent = v ? "Oracle-assisted: the true key only ranks the attack's guesses (key rank, MTD); it never builds them."
    : "Blind: what a real attacker gets — the best guess per coefficient and its correlation.";
  if (!v) $("an-curve").checked = false;
  $("an-curve").disabled = !v;
  $("an-curve-wrap").title = v ? "" : "needs the true key";
}
const anDkRef = () => (anOracle.get() === "file" ? $("an-dkref").value.trim() : anOracle.get());

async function anRun() {
  const f = anFile();
  if (!f || anJob) return;
  const kind = f.mode === "cpa" ? "cpa" : "tvla";
  if (kind === "cpa" && anOracle.get() === "file" && !$("an-dkref").value.trim()) return toast("Enter the path of the dk file", "warn");
  const body = kind === "cpa" ? { file: f.path, model: $("an-model").value, rprev: anRprev(),
      max_traces: $("an-max-traces").value, dk_ref: anDkRef(), curve: $("an-curve").checked,
      coeffs: $("an-coeffs").value, features: $("an-features").value, offsets: $("an-offsets").value.trim() }
    : { file: f.path, max_traces: $("an-max-traces").value };
  const pr = $("an-progress"), t0 = Date.now();
  try {
    const { job_id } = await api("POST", `/api/analysis/${kind}/start`, body);
    anJob = job_id;
    $("an-run").disabled = true; $("an-status").textContent = "";
    pr.classList.remove("hidden"); pr.querySelector(".txt").textContent = "starting…";
    const j = await pollJob(job_id, (x) => { pr.querySelector(".txt").textContent = `${x.message} · ${Math.round((Date.now() - t0) / 1000)} s`; }, 600);
    if (j.status !== "done") throw new Error(j.error);
    const secs = Math.round((Date.now() - t0) / 1000);
    toast(`${kind.toUpperCase()} analysis finished in ${secs} s`, "ok");
    $("an-status").textContent = `last run: ${secs} s`;
    if ($("an-dataset").value === f.path) {     // still looking at this campaign
      anConfig(false);
      await anReportsList(j.result.report_path);
      await anOpenReport(j.result.report_path);   // the saved report (has kind, dataset, timestamp)
    }
  } catch (e) { $("an-status").textContent = "failed"; err(e); }
  finally {
    anJob = null;
    pr.classList.add("hidden");
    $("an-run").disabled = !anFile();
  }
}

// ---- report frame: header, KPIs, tabs ----
function tabsHtml(kind, defs) {
  let want = null;
  try { want = localStorage.getItem("an-tab-" + kind); } catch (e) { /* no storage */ }
  const active = defs.some((d) => d[0] === want) ? want : defs[0][0];
  return { active, html: `<div class="tabs" id="an-tabs">${defs.map(([id, l]) => `<button data-t="${id}" class="${id === active ? "active" : ""}">${l}</button>`).join("")}</div>` };
}
function showTab(kind, t, draw) {
  $("an-tabs").querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.t === t));
  $("an-results").querySelectorAll(".tab-panel").forEach((p) => p.classList.toggle("active", p.dataset.t === t));
  try { localStorage.setItem("an-tab-" + kind, t); } catch (e) { /* no storage */ }
  if (!anDrawn.has(t)) { anDrawn.add(t); draw(t); }
  else window.dispatchEvent(new Event("resize"));
}
// The cycle offset most coefficients agreed on, with how strong that agreement was: a real
// alignment piles up on one value, noise spreads over the whole search.
function bestOffset(f) {
  const h = Object.entries(f.offset_histogram || {}).sort((a, b) => b[1] - a[1]);
  if (!h.length) return "—";
  const total = h.reduce((a, [, n]) => a + n, 0);
  return `${+h[0][0] >= 0 ? "+" : ""}${h[0][0]} (${Math.round((100 * h[0][1]) / total)}% of coefficients)`;
}
function reportHead(r) {
  const mc = r.model_config || {};
  const chips = r.kind === "cpa"
    ? [chip("model", r.model || "?"),
       ...(r.features
         ? [chip("feature", `${r.features.mode} · ${r.features.rows} cycles/coefficient`),
            chip("cycle offset", bestOffset(r.features))]
         : [chip("register / R_prev", `${r.target} / ${r.rprev || "—"}`)]),
       chip("scoring", r.oracle_assisted ? "true key (oracle-assisted)" : "blind", r.oracle_assisted ? "warn" : ""), chip("traces", r.n_traces_used)]
    : [chip("traces", `${r.n_fixed} fixed + ${r.n_random} random`), chip("threshold", "|t| > " + r.leakage_threshold)];
  chips.push(chip("analysed", fmtDate(r.analysis_timestamp_unix)));
  return `<div class="report-head">${modeBadge(r.kind)}<span class="title">${r.kind === "cpa" ? "CPA key recovery" : "TVLA leakage assessment"}</span><div class="chips">${chips.join("")}</div></div>`;
}
function detailsKv(r) {
  const d = r.dataset || {}, mc = r.model_config || {};
  const m = anModelOf(r.model);
  const common = [["Campaign", String(r.file || d.path || "").split("/").pop()], ["Traces in file", d.n_completed], ["Samples / trace", d.n_samples],
    ["Sample rate", rateTxt(d.sample_rate_hz || r.sample_rate_hz)], ["K correct (acquisition)", pct(d.k_match_fraction)]];
  const specific = r.kind === "cpa"
    ? [["Leakage model", r.model || "?"], ["Equation", m ? m.equation : "—"],
       ...(r.features ? [["Trace feature", `${r.features.mode}, one per core clock cycle (${r.features.rows} cycles per coefficient)`],
                         ["Cycle offset", Object.entries(r.features.offset_histogram || {}).sort((a, b) => b[1] - a[1])
                           .slice(0, 3).map(([o, n]) => `${o >= 0 ? "+" : ""}${o} (${n})`).join(", ") +
                           ` of ${(r.features.offsets_searched || []).length} searched`]]
                      : [["Register / R_prev", `${r.target} / ${r.rprev || "—"}`]]),
       ["Scoring", r.oracle_assisted ? "oracle-assisted (true key)" : "blind"], ["Traces used", r.n_traces_used], ["Candidates", r.n_candidates],
       ["Coefficients", r.coefficients_attacked ?? (r.results || []).length], ["Chance rank (mean)", r.chance_rank_mean], ["Model version", r.model_version]]
    : [["Fixed / random", `${r.n_fixed} / ${r.n_random}`], ["Threshold", "|t| > " + r.leakage_threshold], ["max |t|", r.max_abs_t?.toFixed(3)]];
  return common.concat(specific, [["Analysed", fmtDate(r.analysis_timestamp_unix)], ["Job", r.job_id], ["Report file", String(r.report_path || "").split("/").pop()],
    ["Results CSV", r.results_csv ? String(r.results_csv).split("/").pop() : "—"], ["Curve CSV", r.curve?.curve_csv ? String(r.curve.curve_csv).split("/").pop() : "—"]]);
}

// ---- CPA results ----
function renderCPA(r) {
  cur = r; insp = null; ccCache.clear(); anDrawn = new Set();
  const oracle = r.oracle_assisted, rows = r.results || [];
  const best = rows.map((x) => x.best_corr);
  const mean = best.length ? best.reduce((a, b) => a + b, 0) / best.length : 0;
  heatMetric = oracle ? "rank" : "corr";
  const t = tabsHtml("cpa", [["map", "Key map"], ["conv", r.curve ? "Convergence" : "Distribution"], ["table", "Coefficients"], ["models", "Compare models"], ["details", "Details"]]);
  const pan = (id, html) => `<div class="tab-panel ${id === t.active ? "active" : ""}" data-t="${id}">${html}</div>`;
  $("an-results").innerHTML = reportHead(r) + `<div class="kpis" id="cpa-kpis"></div>` + t.html +
    pan("map", `<div class="card"><div class="card-head"><h2>ŝ coefficients</h2><span class="sub">3 polynomials × ${r.coefficients_per_poly ?? 256}${(r.coefficients_per_poly ?? 256) < 256 ? " of 256 (partial run)" : ""} — click a cell to inspect it</span>
        <div class="actions"><div class="seg" id="heat-metric"><button data-v="corr">peak |ρ|</button>${oracle ? '<button data-v="rank">true-key rank</button><button data-v="ok">recovered</button>' : ""}</div></div></div>
        <div class="chart" id="cpa-heat"></div></div>
      <div class="split">
        <div class="card"><div class="card-head"><h3>Per polynomial</h3></div><table class="t poly-sum" id="cpa-poly"></table></div>
        <div class="card" id="cpa-insp"><div class="empty"><div class="t">Coefficient inspector</div>Click a cell above (then use ← → ↑ ↓) to see that coefficient's correlation over time.</div></div>
      </div>`) +
    pan("conv", `<div class="card"><div class="card-head"><h2>${r.curve ? "Key rank vs trace count" : "Peak |ρ| of the best guess"}</h2>
        <span class="sub">${r.curve ? "how many traces the attack needs (oracle-assisted)" : "blind run: no key to rank against — enable scoring + MTD for a convergence curve"}</span>
        ${r.curve ? '<div class="actions"><button class="btn ghost sm" id="cpa-dl-curve">CSV</button></div>' : ""}</div><div class="chart" id="cpa-side"></div></div>
      <div class="card" id="cpa-sr-card"><div class="card-head"><h2>Success rate across campaigns</h2><span class="sub">independent campaigns, same model / R_prev</span></div>
        <div class="chart" id="cpa-sr"></div><div class="chart-note" id="cpa-sr-note"></div></div>`) +
    pan("table", `<div class="card"><div class="card-head"><h2>All coefficients</h2><span class="sub" id="cpa-table-sub"></span>
        <div class="actions">${oracle ? '<div class="seg" id="cpa-filter"><button data-v="" class="active">all</button><button data-v="wrong">not recovered</button><button data-v="right">recovered</button></div>' : ""}
        <button class="btn ghost sm" id="cpa-dl-results">CSV</button></div></div>
        <div class="table-wrap tall"><table class="t click" id="cpa-table"><thead></thead><tbody></tbody></table></div>
        <div class="chart-note">Click a row to open the coefficient in the Key map inspector.</div></div>`) +
    pan("models", `<div class="card"><div class="card-head"><h2>Leakage models on this campaign</h2><span class="sub" id="cpa-models-sub"></span></div>
        <div class="table-wrap"><table class="t click" id="cpa-models"><thead></thead><tbody></tbody></table></div>
        <div class="chart-note">Every CPA report of this campaign, one row per run. Run <b>New analysis</b> with another model (or R_prev) to add one; click a row to open it.
          Compare like with like: scored runs by rank-1 and median true rank, blind runs by peak |ρ|, and the same number of traces.</div></div>`) +
    pan("details", `<div class="card"><div class="kv" id="an-details-kv"></div></div>`);
  kpis("cpa-kpis", [["traces used", r.n_traces_used, modelTxt(r)],
    oracle ? ["recovered (rank 1)", pct(r.rank1_fraction), `${rows.filter((x) => x.true_rank === 1).length} of ${rows.length} coefficients`, r.rank1_fraction > 0 ? "bad" : "good"] : ["mode", "blind", "no key used"],
    oracle ? ["median true rank", Math.round(r.median_true_rank), `of ${r.n_candidates} (chance ≈ ${Math.round(r.chance_rank_mean || r.n_candidates / 2)})`] : ["mean peak |ρ|", mean.toFixed(3), "best guess per coefficient"],
    oracle ? ["mean |ρ| (true key)", r.mean_true_corr.toFixed(3)] : ["max peak |ρ|", best.length ? Math.max(...best).toFixed(3) : "—"],
    r.curve ? ["MTD", r.curve.mtd ?? "not reached", "traces to disclosure", r.curve.mtd ? "bad" : "good"] : null]);
  $("an-tabs").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => showTab("cpa", b.dataset.t, drawCPATab)));
  anDrawn.add(t.active); drawCPATab(t.active);
}
function drawCPATab(t) {
  const r = cur, oracle = r.oracle_assisted, rows = r.results || [];
  if (t === "map") {
    seg("heat-metric", (v) => { heatMetric = v; drawHeat(); }).set(heatMetric);
    drawHeat();
    const per = [0, 1, 2].map((p) => rows.filter((x) => x.poly === p));
    const med = (a) => { const s = a.slice().sort((x, y) => x - y); return s.length ? s[Math.floor(s.length / 2)] : null; };
    const avg = (a) => (a.length ? a.reduce((x, y) => x + y, 0) / a.length : 0);
    $("cpa-poly").innerHTML = (oracle ? "<thead><tr><th>polynomial</th><th class='num'>recovered</th><th class='num'>median true rank</th><th class='num'>mean |ρ| true key</th></tr></thead>"
      : "<thead><tr><th>polynomial</th><th class='num'>mean peak |ρ|</th><th class='num'>max peak |ρ|</th></tr></thead>") + "<tbody>" +
      per.map((a, p) => oracle
        ? `<tr><td>ŝ${p} (BMUL ${p})</td><td class="num">${a.filter((x) => x.true_rank === 1).length} / ${a.length}</td><td class="num">${med(a.map((x) => x.true_rank)) ?? "—"}</td><td class="num">${avg(a.map((x) => x.true_corr || 0)).toFixed(3)}</td></tr>`
        : `<tr><td>ŝ${p} (BMUL ${p})</td><td class="num">${avg(a.map((x) => x.best_corr)).toFixed(3)}</td><td class="num">${a.length ? Math.max(...a.map((x) => x.best_corr)).toFixed(3) : "—"}</td></tr>`).join("") + "</tbody>";
  } else if (t === "conv") {
    if (r.curve) {
      const ck = r.curve.checkpoints;
      const shapes = r.curve.mtd ? [{ type: "line", xref: "x", x0: r.curve.mtd, x1: r.curve.mtd, yref: "paper", y0: 0, y1: 1, line: { color: C.s[2], width: 1, dash: "dash" } }] : [];
      chart("cpa-side", [line("rank-1 %", C.s[0], ck.map((c) => c.n_traces), ck.map((c) => 100 * c.rank1_fraction)),
        line("mean |ρ| at true key", C.s[3], ck.map((c) => c.n_traces), ck.map((c) => c.mean_true_corr), { yaxis: "y2" })],
        { height: 300, yRange: [0, 100], xTitle: "trace count", yTitle: "rank-1 %", y2: true, y2Title: "mean |ρ|", shapes,
          annotations: r.curve.mtd ? [{ x: r.curve.mtd, y: 1, yref: "paper", text: "MTD " + r.curve.mtd, showarrow: false, yanchor: "bottom", font: { size: 10, color: C.s[2] } }] : [] });
      $("cpa-dl-curve").addEventListener("click", () => r.curve.curve_csv ? download("/api/analysis/download", { path: r.curve.curve_csv }) : toast("no CSV", "warn"));
    } else {
      chart("cpa-side", [{ type: "histogram", x: rows.map((x) => x.best_corr), marker: { color: C.s[0] }, nbinsx: 40, name: "peak |ρ|" }],
        { height: 280, xTitle: "peak |ρ| of the best guess", yTitle: "coefficients", showlegend: false, hovermode: "closest" });
    }
    srDraw(r.model, r.rprev);
  } else if (t === "table") {
    const tbl = makeTable("cpa-table", [
      { key: "poly", label: "poly", num: true }, { key: "coeff", label: "coeff", num: true }, { key: "best_guess", label: "best guess", num: true },
      { key: "best_corr", label: "peak |ρ|", num: true, render: (x) => x.best_corr.toFixed(4) },
      ...(oracle ? [{ key: "true_value", label: "true", num: true }, { key: "true_rank", label: "true rank", num: true },
        { key: "true_corr", label: "|ρ| true", num: true, render: (x) => (x.true_corr ?? 0).toFixed(4) },
        { key: "ok", label: "", sortable: false, value: (x) => x.true_rank === 1, render: (x) => (x.true_rank === 1 ? '<span class="bad">recovered</span>' : '<span class="dim">—</span>') }] : []),
    ], { key: (x) => `${x.poly}:${x.coeff}`, onRow: (x) => { showTab("cpa", "map", drawCPATab); selectCoef(x.poly, x.coeff); },
         sortKey: oracle ? "true_rank" : "best_corr", dir: oracle ? 1 : -1 });
    const setRows = (f) => {
      const list = f === "wrong" ? rows.filter((x) => x.true_rank !== 1) : f === "right" ? rows.filter((x) => x.true_rank === 1) : rows;
      tbl.set(list); $("cpa-table-sub").textContent = `${list.length} rows`;
    };
    setRows("");
    if (oracle) seg("cpa-filter", setRows);
    $("cpa-dl-results").addEventListener("click", () => r.results_csv ? download("/api/analysis/download", { path: r.results_csv }) : toast("no CSV", "warn"));
  } else if (t === "models") drawModelCompare();
  else if (t === "details") kv("an-details-kv", detailsKv(r));
}
function heatData() {
  const rows = cur.results || [], z = [[], [], []], text = [[], [], []];
  for (let p = 0; p < 3; p++) for (let j = 0; j < 256; j++) { z[p][j] = null; text[p][j] = ""; }
  rows.forEach((x) => {
    const v = heatMetric === "rank" ? Math.log10(Math.max(1, x.true_rank)) : heatMetric === "ok" ? (x.true_rank === 1 ? 1 : 0) : x.best_corr;
    z[x.poly][x.coeff] = v;
    text[x.poly][x.coeff] = `poly ${x.poly} · coeff ${x.coeff}<br>best guess ${x.best_guess} · |ρ| ${x.best_corr.toFixed(3)}` +
      (cur.oracle_assisted ? `<br>true ${x.true_value} · rank ${x.true_rank}` : "");
  });
  return { z, text };
}
function drawHeat() {
  const el = $("cpa-heat");
  if (!el || !cur || cur.kind !== "cpa") return;
  const { z, text } = heatData();
  const scale = heatMetric === "rank" ? [[0, "#4ade80"], [0.15, "#fbbf24"], [1, "#1c2840"]] : heatMetric === "ok" ? [[0, "#1c2840"], [1, "#f87171"]] : "Viridis";
  const cbar = heatMetric === "rank" ? { title: "rank", tickvals: [0, 1, 2, 3], ticktext: ["1", "10", "100", "1000"] } : heatMetric === "ok" ? { tickvals: [0, 1], ticktext: ["no", "yes"] } : { title: "|ρ|" };
  const shapes = insp ? [{ type: "rect", x0: insp.coeff - 0.5, x1: insp.coeff + 0.5, y0: insp.poly - 0.5, y1: insp.poly + 0.5, line: { color: "#fff", width: 2 } }] : [];
  chart(el, [{ type: "heatmap", z, text, hovertemplate: "%{text}<extra></extra>", colorscale: scale, zmin: heatMetric === "rank" ? 0 : undefined,
    zmax: heatMetric === "rank" ? Math.log10(3329) : undefined, colorbar: Object.assign({ thickness: 10, len: 0.9, tickfont: { size: 9 } }, cbar), xgap: 1, ygap: 3 }],
    { height: 190, hovermode: "closest", showlegend: false, xTitle: "coefficient", margin: { l: 56, r: 10, t: 6, b: 38 },
      yaxis: { tickvals: [0, 1, 2], ticktext: ["poly 0", "poly 1", "poly 2"], autorange: "reversed" }, xaxis: { range: [-0.5, 255.5] }, shapes, uirevision: "heat" })
    .then((gd) => { if (!gd._clickBound) { gd.on("plotly_click", (e) => { const p = e.points[0]; selectCoef(p.y, p.x); }); gd._clickBound = true; } });
}
async function selectCoef(poly, coeff) {
  if (!cur || cur.kind !== "cpa" || !cur.results || !$("cpa-insp")) return;
  poly = Math.max(0, Math.min(2, poly)); coeff = Math.max(0, Math.min(255, coeff));
  insp = { poly, coeff };
  drawHeat();
  const row = cur.results.find((x) => x.poly === poly && x.coeff === coeff);
  const el = $("cpa-insp");
  el.innerHTML = `<div class="card-head inspector-head"><button class="btn ghost sm" id="in-prev" title="previous coefficient (←)">‹</button><span class="coef">ŝ${poly}[${coeff}]</span><button class="btn ghost sm" id="in-next" title="next coefficient (→)">›</button>
      <span class="sub">← → coefficient · ↑ ↓ polynomial</span></div>
    <div class="kpis compact" id="in-kpis"></div><div class="chart" id="in-chart"><div class="chart-empty">computing correlation…</div></div><div class="chart-note" id="in-note"></div>`;
  $("in-prev").addEventListener("click", () => selectCoef(poly, coeff - 1));
  $("in-next").addEventListener("click", () => selectCoef(poly, coeff + 1));
  if (row) kpis("in-kpis", [["best guess", row.best_guess, `peak |ρ| ${row.best_corr.toFixed(4)}`],
    ...(cur.oracle_assisted ? [["true value", row.true_value, row.true_rank === 1 ? "recovered" : "not recovered", row.true_rank === 1 ? "bad" : "good"],
      ["true-key rank", row.true_rank, `|ρ| ${(row.true_corr ?? 0).toFixed(4)}`]] : [])]);
  const key = `${poly}:${coeff}`;
  try {
    let c = ccCache.get(key);
    if (!c) {
      const f = anFiles.find((x) => x.path === cur.file) || {};
      c = await api("POST", "/api/analysis/correlation_curve", { file: cur.file, model: cur.model, target: cur.target, rprev: cur.rprev || "", poly, coeff,
        max_traces: cur.model_config?.max_traces_requested || 0, dk_ref: cur.oracle_assisted && f.has_recipe ? "vector_set" : "",
        features: cur.model_config?.features || "auto", offsets: (cur.model_config?.offsets || []).join(",") });
      ccCache.set(key, c);
    }
    if (!insp || insp.poly !== poly || insp.coeff !== coeff) return;
    // a pipeline model gives one correlation per clock cycle of the word's own schedule, a register
    // model one per sample of the whole BMUL window
    const rows = c.unit === "row", xs = rows ? c.rows : c.best_curve.map((v, i) => i);
    const tr = [line(`best guess ${c.best_guess}`, C.s[0], xs, c.best_curve), peak("best", C.s[0], xs, c.best_curve)];
    if (c.oracle_assisted) tr.push(line(`true key ${c.true_value}`, C.s[1], xs, c.true_curve), peak("true", C.s[1], xs, c.true_curve));
    chart("in-chart", tr, { height: vh(0.30, 240, 380), yRange: [-1, 1], yTitle: "ρ",
      xTitle: rows ? `core clock cycle after the read of word ${coeff >> 1} (ŝ${poly}[${coeff}])` : "sample within the BMUL " + poly + " window",
      xaxis: rows ? { dtick: 1 } : {} });
    $("in-note").textContent = `${c.n_traces} traces · ` +
      (rows ? `${c.n_samples} clock cycles carry this coefficient · cycle offset ${c.offset >= 0 ? "+" : ""}${c.offset}`
            : `${c.n_samples} samples in the window`) +
      (c.oracle_assisted ? ` · true key rank ${c.true_rank} of 3329` : "");
  } catch (e) { chartEmpty("in-chart", e.message); }
}
document.addEventListener("keydown", (e) => {
  if (currentPage !== "analysis" || !insp || e.target.closest("input,textarea,select")) return;
  if (!document.querySelector('#an-results .tab-panel[data-t="map"].active')) return;
  const m = { ArrowLeft: [0, -1], ArrowRight: [0, 1], ArrowUp: [-1, 0], ArrowDown: [1, 0] }[e.key];
  if (m) { e.preventDefault(); selectCoef(insp.poly + m[0], insp.coeff + m[1]); }
});
// The CPA reports of the open campaign, one row per model run (anReports carries their summary numbers).
function drawModelCompare() {
  const rows = anReports.filter((x) => x.kind === "cpa");
  $("cpa-models-sub").textContent = `${rows.length} CPA report(s) · ${new Set(rows.map((x) => modelTxt(x))).size} model / R_prev combination(s)`;
  const num = (v, d) => (v === null || v === undefined ? "—" : v.toFixed(d));
  makeTable("cpa-models", [
    { key: "model", label: "model", render: (x) => `<div class="name mono">${esc(x.model || "?")}</div><div class="sub">${esc(anModelOf(x.model)?.equation || "")}</div>` },
    { key: "rprev", label: "R_prev", render: (x) => esc(x.rprev || "—") },
    { key: "oracle_assisted", label: "scoring", value: (x) => (x.oracle_assisted ? 1 : 0), render: (x) => (x.oracle_assisted ? '<span class="badge warn">scored</span>' : '<span class="badge">blind</span>') },
    { key: "n_traces_used", label: "traces", num: true },
    { key: "rank1_fraction", label: "rank-1", num: true, render: (x) => (x.oracle_assisted ? pct(x.rank1_fraction) : "—") },
    { key: "median_true_rank", label: "median rank", num: true, render: (x) => (x.oracle_assisted ? num(x.median_true_rank, 0) : "—") },
    { key: "mean_true_corr", label: "|ρ| true key", num: true, render: (x) => (x.oracle_assisted ? num(x.mean_true_corr, 4) : "—") },
    { key: "mean_best_corr", label: "mean peak |ρ|", num: true, render: (x) => num(x.mean_best_corr, 4) },
    { key: "mtd", label: "MTD", num: true, render: (x) => x.mtd ?? "—" },
    { key: "analysis_timestamp_unix", label: "analysed", render: (x) => fmtAgo(x.analysis_timestamp_unix) },
  ], { key: (x) => x.path, onRow: (x) => anOpenReport(x.path), sortKey: rows.some((x) => x.oracle_assisted) ? "rank1_fraction" : "mean_best_corr", dir: -1,
       empty: '<div class="empty">No CPA reports for this campaign yet.</div>' }).set(rows);
  const t = $("cpa-models").querySelector(`tr[data-key="${CSS.escape(cur.report_path || "")}"]`);
  if (t) t.classList.add("sel");
}
async function srDraw(model, rprev) {
  try {
    const r = await api("POST", "/api/analysis/success_rate", { model, rprev: rprev || "" });
    const what = esc(model) + (rprev ? " / " + esc(rprev) : "");
    if (!r.available) {
      $("cpa-sr").innerHTML = `<div class="chart-empty">Needs at least two independent campaigns analysed with scoring + MTD with ${what}.</div>`;
      return;
    }
    const b = r.checkpoints.filter((c) => c.min_rank1_fraction !== null), m = r.checkpoints.filter((c) => c.mean_rank1_fraction !== null);
    chart("cpa-sr", [...band("min–max", "#38bdf82e", b.map((c) => c.n_traces), b.map((c) => 100 * c.min_rank1_fraction), b.map((c) => 100 * c.max_rank1_fraction)),
      line("mean rank-1 %", C.s[0], m.map((c) => c.n_traces), m.map((c) => 100 * c.mean_rank1_fraction))], { height: 240, yRange: [0, 100], xTitle: "trace count", yTitle: "rank-1 %" });
    $("cpa-sr-note").textContent = `${r.n_campaigns} independent campaigns with ${r.model}${r.rprev ? " / " + r.rprev : ""}`;
  } catch (e) { chartEmpty("cpa-sr", e.message); }
}

// ---- TVLA results ----
async function renderTVLA(r) {
  cur = r; insp = null; anDrawn = new Set();
  const t = tabsHtml("tvla", [["t", "|t| over time"], ["means", "Class means"], ["det", "Detection"], ["details", "Details"]]);
  const pan = (id, html) => `<div class="tab-panel ${id === t.active ? "active" : ""}" data-t="${id}">${html}</div>`;
  $("an-results").innerHTML = reportHead(r) + `<div class="kpis" id="tv-kpis"></div>` + t.html +
    pan("t", `<div class="card"><div class="card-head"><h2>Welch |t| over time</h2><span class="sub">click anywhere in the plot to read that sample</span>
        <div class="actions"><div class="seg" id="tvla-zoom"></div></div></div>
        <div class="chart" id="tvla-t"></div><div class="chart-note" id="tvla-readout">—</div></div>`) +
    pan("means", `<div class="card"><div class="card-head"><h2>Class means</h2><span class="sub">fixed and random averages; their difference on the right axis</span></div><div class="chart" id="tvla-means"></div></div>`) +
    pan("det", `<div class="card"><div class="card-head"><h2>Detection vs trace count</h2><span class="sub">max |t| as traces accumulate</span></div><div class="chart" id="tvla-det"></div></div>`) +
    pan("details", `<div class="card"><div class="kv" id="an-details-kv"></div></div>`);
  const th = r.leakage_threshold, leak = r.leakage_detected;
  const toUs = (s) => (r.sample_rate_hz ? (s / r.sample_rate_hz) * 1e6 : null);
  try { r._axis = (await api("POST", "/api/analysis/raw_traces", { file: r.file, count: 1, max_points: 500 })).axis; } catch (e) { r._axis = null; }
  if (cur !== r) return;   // another report was opened meanwhile
  r._peakPhase = r._axis ? phaseAt(r._axis.phases, r.max_abs_t_sample) : "";
  kpis("tv-kpis", [["max |t|", r.max_abs_t.toFixed(2), `threshold ${th}`, leak ? "bad" : "good"],
    ["leakage", leak ? "detected" : "not detected", leak ? "first-order leakage present" : "at this trace count", leak ? "bad" : "good"],
    ["peak at", `sample ${r.max_abs_t_sample}`, (toUs(r.max_abs_t_sample) !== null ? toUs(r.max_abs_t_sample).toFixed(3) + " µs" : "") + (r._peakPhase ? " · " + r._peakPhase : "")],
    ["traces to detection", r.traces_to_detection ?? "not reached"], ["classes", `${r.n_fixed} / ${r.n_random}`, "fixed / random"]]);
  $("an-tabs").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => showTab("tvla", b.dataset.t, drawTVLATab)));
  anDrawn.add(t.active); drawTVLATab(t.active);
}
function drawTVLATab(t) {
  const r = cur, th = r.leakage_threshold;
  const wins = r._axis ? r._axis.bmul_windows_samples || [] : [];
  const phases = r._axis ? r._axis.phases || [] : [];
  const wb = mergeBands(windowBands(wins), phaseBands(phases, "samples"));
  const toUs = (s) => (r.sample_rate_hz ? (s / r.sample_rate_hz) * 1e6 : null);
  if (t === "t") {
    const tEl = $("tvla-t"), absT = r.t_curve.map(Math.abs);
    // zoom targets: the whole record, each BMUL window (the CPA target), or a Decaps phase
    const zr = { all: null };
    wins.forEach((w, i) => { zr["b" + i] = w; });
    const zooms = [["all", "All"]].concat(wins.map((w, i) => ["b" + i, "BMUL " + i]));
    phases.filter((p) => /^(J|re-enc)/.test(p.label)).forEach((p, i) => { zr["p" + i] = p.samples; zooms.push(["p" + i, p.label]); });
    $("tvla-zoom").innerHTML = zooms.map(([v, l], i) => `<button data-v="${v}" class="${i === 0 ? "active" : ""}">${esc(l)}</button>`).join("");
    seg("tvla-zoom", (v) => Plotly.relayout(plotDiv(tEl), zr[v] ? { "xaxis.range": zr[v] } : { "xaxis.autorange": true }));
    chart(tEl, [line("|t|", C.s[0], r.sample_indices, absT), peak("|t|", C.s[2], r.sample_indices, absT)],
      { height: vh(0.44, 300, 600), xTitle: "sample", yTitle: "|t|", showlegend: false, hovermode: "x",
        shapes: wb.shapes.concat([hLine(th, C.s[3])]), annotations: wb.annotations.concat([hLabel(th, C.s[3], "|t| = " + th)]) })
      .then((gd) => gd.on("plotly_click", (e) => {
        const pt = e.points.find((p) => p.curveNumber === 0);
        if (!pt) return;
        const i = pt.pointIndex, s = r.sample_indices[i], fm = r.fixed_mean_curve[i], rm = r.random_mean_curve[i];
        if (fm === undefined) return;
        const inWin = wins.findIndex((w) => s >= w[0] && s <= w[1]);
        const ph = phaseAt(phases, s);
        $("tvla-readout").textContent = `sample ${s}${toUs(s) !== null ? ` (${toUs(s).toFixed(3)} µs)` : ""}${inWin >= 0 ? ` · inside BMUL ${inWin}` : ph ? ` · in ${ph}` : ""} · t = ${r.t_curve[i].toFixed(2)}` +
          ` · fixed mean ${fm.toPrecision(4)} · random mean ${rm.toPrecision(4)} · difference ${(fm - rm).toPrecision(3)}`;
      }));
  } else if (t === "means") {
    const diff = r.fixed_mean_curve.map((v, i) => v - r.random_mean_curve[i]);
    chart("tvla-means", [line("fixed", C.s[1], r.sample_indices, r.fixed_mean_curve), line("random", C.s[2], r.sample_indices, r.random_mean_curve),
      line("fixed − random", C.s[3], r.sample_indices, diff, { yaxis: "y2" })],
      { height: vh(0.44, 300, 600), xTitle: "sample", y2: true, y2Title: "difference", shapes: wb.shapes, annotations: wb.annotations });
  } else if (t === "det") {
    const det = r.detection_curve || [];
    chart("tvla-det", [line("max |t|", C.s[0], det.map((c) => c.n_traces), det.map((c) => c.max_abs_t))],
      { height: vh(0.40, 280, 520), xTitle: "trace count", yTitle: "max |t|", showlegend: false, shapes: [hLine(th, C.s[3])], annotations: [hLabel(th, C.s[3], "threshold")] });
  } else if (t === "details") kv("an-details-kv", detailsKv(r));
}

// model self-test and CLI default
async function modelSelftest() {
  const name = $("an-model").value;
  await busy($("model-selftest"), async () => {
    try {
      const r = await api("POST", "/api/model/selftest", { model: name });
      $("model-selftest-table").innerHTML = r.checks.map((c) => `<tr><td>${esc(c.name)}</td><td class="${c.ok ? "pass" : "fail"}">${c.ok ? "PASS" : "FAIL"}</td><td>${esc(c.detail)}</td></tr>`).join("");
      $("model-selftest-wrap").classList.remove("hidden");
      toast(r.ok ? `${name}: all self-test checks pass` : `${name}: self-test FAILED — do not trust CPA results from it`, r.ok ? "ok" : "bad", 7000);
    } catch (e) { err(e); }
  });
}
async function modelSaveDefault() {
  try { await api("POST", "/api/model/save_default", { model: $("an-model").value, rprev: anRprev() }); toast("Saved as the cpa_attack.py default (config.py)", "ok"); }
  catch (e) { err(e); }
}

// =====================================================================================
// Advanced page
// =====================================================================================
let lsReady = false, lsLayout = null;
const hx = (n) => "0x" + n.toString(16).toUpperCase().padStart(4, "0");
function hexDump(hex, base) {
  const out = [];
  for (let i = 0; i < hex.length; i += 32) out.push((base + i / 2).toString(16).padStart(4, "0") + "  " + hex.slice(i, i + 32).match(/../g).join(" "));
  return out.join("\n") || "(empty)";
}
function opLog(line) {
  const el = $("op-log");
  el.textContent = (el.textContent === "—" ? "" : el.textContent + "\n") + `[${new Date().toLocaleTimeString()}] ${line}`;
  el.scrollTop = el.scrollHeight;
}

async function lsLoad() {
  try {
    const s = await api("GET", "/api/fpga/settings");
    $("ls-timeout").value = s.op_timeout;
    lsLayout = s.layout;
    const L = s.layout, dec = L.ops.find((o) => o.name === "Decaps");
    const rows = [["dk — Decaps secret key (s)", L.in_base, L.dk_len], ["c — ciphertext (c)", L.in_base + L.dk_len, L.ct_len],
      ["K — shared secret (a)", L.out_base, dec.out_len]].concat(L.ops.filter((o) => o.name !== "Decaps").flatMap((o) => [
      [`${o.name} input`, L.in_base, o.in_len], [`${o.name} output`, L.out_base, o.out_len]]));
    $("ls-layout").innerHTML = rows.map(([n, off, len]) => `<tr data-off="${off}" data-len="${len}"><td>${esc(n)}</td><td class="num mono">${hx(off)}</td><td class="num">${len}</td><td class="dim">read →</td></tr>`).join("");
    $("ls-layout").querySelectorAll("tr").forEach((tr) => tr.addEventListener("click", () => {
      $("buf-read-offset").value = hx(+tr.dataset.off); $("buf-read-len").value = Math.min(+tr.dataset.len, 8192); bufRead();
    }));
    lsInfo();
  } catch (e) { err(e); }
}
function lsInfo() {
  const b = S && S.board, i = b && b.info;
  kv("ls-info", i ? [["Board", `core ${i.version} · ${String(i.port).split("/").pop()} @ ${i.baud}`],
    ["0x05 Run", i.has_run ? "supported — operations enabled" : "MISSING — rebuild the bitstream; operations are disabled"],
    ["Timeout", (i.op_timeout ?? "—") + " s"]] : [["Board", b ? STATE_TXT[b.state] : "—"]]);
}
async function lsSave() {
  try {
    await api("POST", "/api/fpga/settings", { op_timeout: $("ls-timeout").value });
    toast("Link settings applied", "ok");
    applyState(await api("GET", "/api/state"));
  } catch (e) { err(e); }
}
async function lsReprobe() {
  await busy($("ls-reprobe"), async () => {
    try {
      const r = await api("POST", "/api/fpga/reprobe", {});
      toast(r.has_run ? "0x05 Run is supported by this bitstream" : "This bitstream has no 0x05 Run — rebuild it; operations are disabled", r.has_run ? "ok" : "bad", 8000);
      applyState(await api("GET", "/api/state"));
    } catch (e) { err(e); }
  });
}

const OP_OUT = { 0: 3584, 1: 1120, 2: 32 };
function opModeChanged() {
  const op = +$("op-mode").value;
  $("op-out-len").value = OP_OUT[op];
  $("op-run-sub").textContent = `START, reply when done with ${OP_OUT[op]} output bytes`;
}
function opResult(r, expected) {
  const flags = (r.flags || []).map((f) => `<span class="badge ${f === "done" ? "ok" : f === "error" ? "bad" : "warn"}">${f}</span>`).join(" ") || '<span class="badge">idle</span>';
  const k = r.output_hex || "";
  const match = expected && k ? (k === expected ? '<span class="badge ok">K = expected (FIPS 203)</span>' : '<span class="badge bad">K ≠ expected</span>') : "";
  const block = r.status === undefined ? "" : `<div class="kpis compact">
      <div class="kpi"><div class="l">STATUS</div><div class="v">0x${(r.status ?? 0).toString(16)}</div><div class="s">${flags}</div></div>
      <div class="kpi"><div class="l">CYCLE_COUNT</div><div class="v">${r.cycles ?? "—"}</div><div class="s">${r.us ? r.us.toFixed(1) + " µs" : ""}</div></div>
      <div class="kpi"><div class="l">ERROR_CODE</div><div class="v">${r.error_code ?? "—"}</div><div class="s">${esc(r.error || "")}</div></div>
      <div class="kpi"><div class="l">round trip</div><div class="v">${r.wall_time ? (1000 * r.wall_time).toFixed(1) + " ms" : "—"}</div><div class="s">one 0x05 Run frame</div></div>
    </div>`;
  $("op-result").innerHTML = block + (k ? `<div class="field-title">output (${k.length / 2} B${r.status === undefined ? ", read from the buffer" : ""}) ${match}<button class="btn ghost sm" data-copy="${k}">Copy</button></div><div class="hexbox">${k}</div>` : "");
  $("op-result").querySelectorAll("[data-copy]").forEach((b) => b.addEventListener("click", () => navigator.clipboard.writeText(b.dataset.copy).then(() => toast("Copied"))));
}
async function opStep(step, btn) {
  const body = { step };
  if (step === "configure") body.op = $("op-mode").value;
  if (step === "load_sk") body.hex = $("op-dk").value;
  if (step === "load_ct") body.hex = $("op-ct").value;
  if (step === "run") body.out_len = $("op-out-len").value;
  if (step === "read_output") body.length = $("op-out-len").value;
  if ((step === "load_sk" && !body.hex.trim()) || (step === "load_ct" && !body.hex.trim()))
    return toast("Paste the hex first, or load the selected test vector", "warn");
  await busy(btn, async () => {
    try {
      const r = await api("POST", "/api/fpga/step", body);
      opLog(`${step.padEnd(11)} ${r.message}`);
      const expected = vpRow && +$("op-mode").value === 2 ? vpRow.k : null;
      if (step === "run") opResult(r, expected);
      if (step === "read_output") opResult({ output_hex: r.output_hex }, expected);
    } catch (e) { opLog(`${step.padEnd(11)} ERROR ${e.message}`); err(e); }
  });
}
function opFill() {
  if (!vpRow) return toast("Select a row in Test Vectors → Contents first", "warn");
  $("op-dk").value = vpRow.dk; $("op-ct").value = vpRow.ct;
  $("op-mode").value = "2"; opModeChanged();
  $("op-expected").textContent = `test vector #${vpRow.index} loaded · expected K ${short(vpRow.k, 16)}`;
  toast(`Loaded vector #${vpRow.index}`);
}
async function opRun() {
  await busy($("op-run"), async () => {
    try {
      const r = await api("POST", "/api/fpga/decaps", { dk_hex: $("op-dk").value, ct_hex: $("op-ct").value, resend_dk: $("op-resend-dk").checked });
      opLog(`decaps      ${r.describe} · K ${r.k_match ? "matches" : "DIFFERS from"} FIPS 203 · ${(1000 * r.wall_time).toFixed(1)} ms`);
      const st = r.status ?? 0;
      opResult({ status: st, flags: ["busy", "done", "error"].filter((n, i) => st & (1 << i)), cycles: r.cycles, us: r.cycles / (S?.board?.info?.core_mhz || 206.25),
        error_code: r.error_code, error: r.describe, output_hex: r.output_hex, wall_time: r.wall_time }, r.expected_hex);
    } catch (e) { err(e); }
  });
}
async function regReadAll() {
  await busy($("reg-read-all"), async () => {
    try {
      const { registers } = await api("POST", "/api/fpga/read_all", {});
      $("reg-table").querySelector("tbody").innerHTML = registers.map((g) => `<tr data-n="${g.name}" title="${esc(g.note)}"><td><b>${g.name}</b></td><td>${g.addr}</td><td class="num">${g.hex}</td><td>${esc(g.decoded)}</td></tr>`).join("");
      $("reg-table").querySelectorAll("tbody tr").forEach((tr) => tr.addEventListener("click", () => {
        $("reg-name").value = tr.dataset.n; $("reg-value").value = tr.children[2].textContent;
      }));
    } catch (e) { err(e); }
  });
}
async function regRead() { try { const r = await api("POST", "/api/fpga/read_reg", { name: $("reg-name").value }); $("reg-result").textContent = `${r.name} = ${r.hex}  (${r.value})`; $("reg-value").value = r.hex; } catch (e) { err(e); } }
async function regWrite() {
  const name = $("reg-name").value;
  if (["CTRL", "SEC_LEVEL", "OP_MODE"].includes(name) && !(await dialog({ title: `Write ${name}?`, body: `Writing <b>${name}</b> changes what the core does next. The next Decaps from the GUI rewrites the configuration it needs.`, ok: "Write" }))) return;
  try { const r = await api("POST", "/api/fpga/write_reg", { name, value: $("reg-value").value }); $("reg-result").textContent = `${r.name} ← 0x${r.value.toString(16)}`; opLog(`write_reg   ${r.name} <- 0x${r.value.toString(16)}`); } catch (e) { err(e); }
}
async function bufRead() { try { const r = await api("POST", "/api/fpga/read_data", { offset: $("buf-read-offset").value, length: $("buf-read-len").value }); $("buf-read-result").textContent = hexDump(r.hex, r.offset); } catch (e) { err(e); } }
async function bufWrite() { try { const r = await api("POST", "/api/fpga/write_data", { offset: $("buf-write-offset").value, hex: $("buf-write-data").value }); $("buf-write-result").textContent = `wrote ${r.length} bytes at 0x${r.offset.toString(16)}`; } catch (e) { err(e); } }

// =====================================================================================
// init
// =====================================================================================
async function init() {
  $("mobile-nav").innerHTML = PAGES.map((p) => `<option value="${p}">${$("page-" + p).dataset.title}</option>`).join("");
  $("mobile-nav").addEventListener("change", () => showPage($("mobile-nav").value));
  const mq = window.matchMedia("(max-width: 760px)");
  const mqApply = () => $("mobile-nav").classList.toggle("hidden", !mq.matches);
  mq.addEventListener("change", mqApply); mqApply();

  try {
    const s = await api("GET", "/api/state");
    $("board-baud").value = s.defaults.baud;
    $("scope-ip").value = s.defaults.scope_ip; $("scope-channel").value = s.defaults.scope_channel;
    $("scope-dialect").value = s.defaults.scope_dialect; $("scope-timeout").value = s.defaults.scope_timeout_ms;
    if (s.defaults.scope_transport) $("scope-transport").value = s.defaults.scope_transport;
    $("reg-name").innerHTML = s.registers.map((r) => `<option ${r === "STATUS" ? "selected" : ""}>${r}</option>`).join("");
    S = s;
  } catch (e) { err(e); }
  scopeDialectChanged(false);
  await refreshPorts(true);

  $("board-port").addEventListener("change", portHint);
  $("board-refresh-ports").addEventListener("click", () => refreshPorts(false));
  $("board-conn-btn").addEventListener("click", boardConnBtn);
  $("board-preflight").addEventListener("click", boardPreflight);
  $("board-auto").checked = localStorage.getItem("board-auto") === "1";
  $("board-auto").addEventListener("change", () => localStorage.setItem("board-auto", $("board-auto").checked ? "1" : "0"));
  $("scope-ip").addEventListener("input", scopeHint);
  $("scope-transport").addEventListener("change", scopeHint);
  $("scope-dialect").addEventListener("change", () => scopeDialectChanged(true));
  $("scope-conn-btn").addEventListener("click", scopeConnBtn);
  $("scope-list-resources").addEventListener("click", scopeFind);
  $("scope-save-config").addEventListener("click", scopeSave);

  // vectors
  const info = await api("GET", "/api/vectors/info").catch(() => ({ strategies: {} }));
  const STRAT = { valid: "Valid encapsulations (accept path)", invalid: "Random bytes (implicit rejection)", structured: "Structured: isolate one BaseMul pair" };
  $("vg-strategy").innerHTML = Object.keys(info.strategies).map((k) => `<option value="${k}">${STRAT[k] || k}</option>`).join("");
  vgMode = seg("vg-mode", vgVisibility);
  $("vg-strategy").addEventListener("change", vgVisibility);
  vgVisibility();
  $("vg-seed-random").addEventListener("click", () => {
    const b = new Uint8Array(64); crypto.getRandomValues(b);
    $("vg-seed").value = Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
  });
  $("vg-generate").addEventListener("click", vgGenerate);
  vsTable = makeTable("vs-table", [
    { key: "id", label: "set", value: (m) => (m.label || m.id).toLowerCase(),
      render: (m) => (m.label ? `<div class="name">${esc(m.label)}</div><div class="sub">${esc(m.id)}</div>` : `<div class="name mono">${esc(m.id)}</div>`) +
        (m.tags?.length ? `<div>${tagChips(m.tags)}</div>` : "") },
    { key: "mode", label: "purpose", render: (m) => modeBadge(m.mode) + ` <span class="muted" style="font-size:12px">${esc(kindLabel(m))}</span>` },
    { key: "n", label: "vectors", num: true }, { key: "created_unix", label: "created", render: (m) => fmtAgo(m.created_unix) },
  ], { key: (m) => m.path, onRow: (m) => vsSelect(m.path), sortKey: "created_unix", dir: -1, checks: vsChecks, onCheck: vsBulk,
       onTag: (t) => { $("vs-tag").value = t; vsFilter(); },
       empty: () => vsTrash ? '<div class="empty"><div class="t">Trash is empty</div>Deleted vector sets wait here until you delete them permanently.</div>'
         : '<div class="empty"><div class="t">Nothing here</div>Generate a vector set with the form on the left (or clear the filters).</div>' });
  $("vs-search").addEventListener("input", vsFilter);
  $("vs-tag").addEventListener("change", vsFilter);
  $("vs-trash-toggle").addEventListener("click", () => { vsTrash = !vsTrash; vsChecks.clear(); vsSel = null; vsRefresh(); });
  $("vs-delete").addEventListener("click", () => vsSel && vsDelete([vsSel]));
  $("vs-restore").addEventListener("click", () => vsSel && vsRestore([vsSel]));
  $("vs-purge").addEventListener("click", () => vsSel && vsPurge([vsSel]));
  $("vs-empty-trash").addEventListener("click", vsEmptyTrash);
  $("vs-use").addEventListener("click", () => {
    if (!vsSel) return toast("Select a vector set first", "warn");
    acqFillSets(); $("acq-vector-set").value = vsSel.path; $("acq-resume").value = ""; acqSetHint(); showPage("acquire"); applyState(S);
  });
  $("vs-download-btn").addEventListener("click", (e) => { e.stopPropagation(); $("vs-download-btn").parentElement.classList.toggle("open"); });
  $("vs-download-menu").querySelectorAll("a").forEach((a) => a.addEventListener("click", () => {
    $("vs-download-btn").parentElement.classList.remove("open");
    if (!vsSel) return toast("Select a vector set first", "warn");
    download("/api/vectors/download", { path: vsSel.path, format: a.dataset.f });
  }));
  $("vs-verify-sw").addEventListener("click", vsVerifySw);
  $("vp-class").addEventListener("change", () => { vpOffset = 0; vpLoad(); });
  $("vp-prev").addEventListener("click", () => { vpOffset = Math.max(0, vpOffset - VP_PAGE); vpLoad(); });
  $("vp-next").addEventListener("click", () => { vpOffset += VP_PAGE; vpLoad(); });
  $("vb-run").addEventListener("click", vbRun);
  $("vb-stop").addEventListener("click", () => vbJob && api("POST", "/api/jobs/stop", { job_id: vbJob }).catch(err));
  await vsRefresh();

  // acquisition
  $("acq-vector-set").addEventListener("change", () => { acqSetHint(); applyState(S); });
  $("acq-resume").addEventListener("change", () => applyState(S));
  $("acq-start").addEventListener("click", acqStart);
  $("acq-stop").addEventListener("click", async () => { if (acqJob) { await api("POST", "/api/jobs/stop", { job_id: acqJob }).catch(err); toast("Stopping after the current trace…", "warn"); } });
  ["live-show-last", "live-show-mean", "live-show-diff"].forEach((id) => $(id).addEventListener("change", () => liveDraw()));
  $("cap-one").addEventListener("click", capOne);
  $("trig-check").addEventListener("click", trigCheck);
  $("cap-clear").addEventListener("click", () => { captures = []; capDraw(); });
  $("acq-open-library").addEventListener("click", () => { showPage("library"); if (acqLastFile) setTimeout(() => libShow(acqLastFile), 300); });
  $("acq-open-analysis").addEventListener("click", () => { showPage("analysis"); if (acqLastFile) anSelectDataset(acqLastFile); });

  // library
  libMode = seg("lib-mode", libFilter);
  libTable = makeTable("lib-table", [
    { key: "label", label: "campaign", value: (f) => (f.label || f.filename).toLowerCase(),
      render: (f) => `<div class="name">${esc(f.label || f.filename)}</div>${f.label ? `<div class="sub">${esc(f.filename)}</div>` : ""}${f.notes ? `<div class="sub" style="font-family:inherit">${esc(short(f.notes, 60))}</div>` : ""}` +
        (f.tags?.length ? `<div>${tagChips(f.tags)}</div>` : "") },
    { key: "mode", label: "mode", render: (f) => modeBadge(f.mode) },
    { key: "n_completed", label: "traces", num: true, render: (f) => `${f.n_completed}${f.n_traces_planned && f.n_traces_planned !== f.n_completed ? `<span class="dim">/${f.n_traces_planned}</span>` : ""}` },
    { key: "status", label: "status", render: (f) => statusBadge(f.status) },
    { key: "k_match_fraction", label: "K ✓", num: true, render: (f) => `<span class="${f.k_match_fraction === 1 ? "good" : f.k_match_fraction === null || f.k_match_fraction === undefined ? "dim" : "bad"}">${pct(f.k_match_fraction)}</span>` },
    { key: "size_bytes", label: "size", num: true, render: (f) => fmtBytes(f.size_bytes) },
    { key: "created_unix", label: "captured", render: (f) => fmtAgo(f.created_unix) },
  ], { key: (f) => f.path, onRow: (f) => libShow(f.path), sortKey: "created_unix", dir: -1, checks: libChecks, onCheck: libBulk,
       onTag: (t) => { $("lib-tag").value = t; libFilter(); },
       empty: () => libTrash ? '<div class="empty"><div class="t">Trash is empty</div>Campaigns moved to the trash wait here until you delete them permanently.</div>'
         : '<div class="empty"><div class="t">No campaigns here</div>Captured campaigns appear here (or clear the filters). Start one on the Acquisition page.</div>' });
  $("lib-search").addEventListener("input", libFilter);
  $("lib-tag").addEventListener("change", libFilter);
  $("lib-empty-trash").addEventListener("click", libEmptyTrash);
  $("lib-status").addEventListener("change", libFilter);
  $("lib-refresh").addEventListener("click", () => libRefresh());
  $("lib-trash-toggle").addEventListener("click", () => { libTrash = !libTrash; libChecks.clear(); libSel = null; libDetailEmpty(); libRefresh(); });
  $("tv-load").addEventListener("click", tvLoad);
  $("tv-prev").addEventListener("click", () => tvStep(-1));
  $("tv-next").addEventListener("click", () => tvStep(1));
  $("tv-play").addEventListener("click", tvPlayToggle);
  $("tv-pos").addEventListener("input", tvScrub);
  $("tv-cls").addEventListener("change", () => { if (tvPlay) tvPlayReset(); });
  document.addEventListener("keydown", (e) => {   // space: play / pause while the viewer is on screen
    if (e.code !== "Space" || currentPage !== "library" || $("lib-viewer").classList.contains("hidden")) return;
    if (/^(INPUT|TEXTAREA|SELECT|BUTTON)$/.test(document.activeElement?.tagName || "")) return;
    e.preventDefault(); tvPlayToggle();
  });
  $("tv-bands").addEventListener("change", tvDraw);
  $("tv-export-csv").addEventListener("click", tvCsv);

  // analysis
  anOracle = seg("an-oracle", anOracleChanged);
  anOracleChanged("");
  $("an-dataset").addEventListener("change", () => anDatasetChanged());
  $("an-report").addEventListener("change", () => $("an-report").value && anOpenReport($("an-report").value));
  $("an-new").addEventListener("click", () => { anConfig(true); $("an-config").scrollIntoView({ behavior: "smooth", block: "nearest" }); });
  $("an-config-close").addEventListener("click", () => anConfig(false));
  $("an-more-btn").addEventListener("click", (e) => { e.stopPropagation(); $("an-more").classList.toggle("open"); });
  $("an-more").querySelectorAll("a").forEach((a) => a.addEventListener("click", () => anReportAction(a.dataset.act)));
  $("an-model").addEventListener("change", () => anModel());
  $("an-model-reload").addEventListener("click", () => anLoadModels(false));
  $("an-run").addEventListener("click", anRun);
  $("model-selftest").addEventListener("click", modelSelftest);
  $("model-save-default").addEventListener("click", modelSaveDefault);
  await anLoadModels(true);

  // advanced
  lsReady = true;
  $("ls-save").addEventListener("click", lsSave);
  $("ls-reprobe").addEventListener("click", lsReprobe);
  document.querySelectorAll("[data-step]").forEach((b) => b.addEventListener("click", () => opStep(b.dataset.step, b)));
  $("op-mode").addEventListener("change", opModeChanged);
  opModeChanged();
  $("reg-read-all").addEventListener("click", regReadAll);
  $("reg-read").addEventListener("click", regRead); $("reg-write").addEventListener("click", regWrite);
  $("buf-read").addEventListener("click", bufRead); $("buf-write").addEventListener("click", bufWrite);
  $("op-fill").addEventListener("click", opFill); $("op-run").addEventListener("click", opRun);
  lsLoad();

  libRefresh(true);
  showPage(location.hash.slice(1) || "connect", false);
  applyState(S);
  pollState();
  pollLog();
}
init();
