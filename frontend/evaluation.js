/* Algorithm workbench. User-supplied values only enter text nodes, never HTML. */
"use strict";
const $ = id => document.getElementById(id);
let selectedReport = null;
let apiBase = "";
const endpoint = $("endpoint");
endpoint.value = localStorage.getItem("autofleet_api_base") || `${location.protocol === "https:" ? "https:" : "http:"}//${location.hostname || "127.0.0.1"}:8200/api/v1`;

function node(tag, text, cls) {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = String(text);
  if (cls) el.className = cls;
  return el;
}
function message(text, error = false) { $("notice").textContent = text; $("notice").className = error ? "error" : "hint"; }
function format(value) { return value === null || value === undefined ? "Unavailable" : typeof value === "number" ? Number(value.toFixed(4)).toString() : String(value); }
async function request(path, options = {}) {
  const res = await fetch(`${apiBase}/evaluation${path}`, options);
  const result = await res.json();
  if (!res.ok) throw new Error(typeof result.detail === "string" ? result.detail : JSON.stringify(result.detail || result));
  return result;
}
function table(target, headers, rows) {
  const root = typeof target === "string" ? $(target) : target;
  const t = node("table"), head = node("thead"), tr = node("tr"), body = node("tbody");
  headers.forEach(h => tr.append(node("th", h))); head.append(tr);
  if (!rows.length) { const row = node("tr"), cell = node("td", "No records."); cell.colSpan = headers.length; row.append(cell); body.append(row); }
  rows.forEach(values => { const row = node("tr"); values.forEach(v => { const cell = node("td"); cell.append(v instanceof Node ? v : document.createTextNode(format(v))); row.append(cell); }); body.append(row); });
  t.append(head, body); root.replaceChildren(t);
}
async function busy(button, action) {
  button.disabled = true;
  try { await action(); } catch (error) { message(error.message, true); }
  finally { button.disabled = false; }
}
async function loadRuns() {
  const { items } = await request("/runs");
  $("run-count").textContent = `${items.length} recent runs`;
  $("runs").replaceChildren();
  for (const item of items) {
    const row = node("tr"), inspect = node("button", "Inspect", "btn subtle compact");
    inspect.type = "button";
    inspect.addEventListener("click", () => busy(inspect, () => inspectRun(item.run_id)));
    [item.candidate_version, `${item.source.kind} / ${item.source.dataset}`, item.gate.status, new Date(item.created_at).toLocaleString()].forEach(text => row.append(node("td", text)));
    const cell = node("td"); cell.append(inspect); row.append(cell); $("runs").append(row);
  }
  if (!items.length) { const row = node("tr"), cell = node("td", "No recorded runs. Import data to begin."); cell.colSpan = 5; row.append(cell); $("runs").append(row); }
  for (const id of ["baseline", "candidate"]) {
    const old = $(id).value; $(id).replaceChildren();
    items.forEach(item => { const opt = node("option", `${item.candidate_version} · ${item.run_id.slice(0, 8)}`); opt.value = item.run_id; $(id).append(opt); });
    if (items.some(i => i.run_id === old)) $(id).value = old;
  }
  if (items.length > 1 && $("baseline").value === $("candidate").value) $("baseline").selectedIndex = 1;
}
function renderFailures() {
  const term = $("failure-filter").value.toLowerCase();
  const failures = (selectedReport?.failures || []).filter(f => `${f.scenario_id} ${f.reason} ${f.label}`.toLowerCase().includes(term));
  table("failures", ["Scenario", "Frame / time", "Reason", "Class", "Box (xyxy)", "ODD"], failures.slice(0, 500).map(f => [f.scenario_id, `${f.frame_id} / ${format(f.t_s)}`, f.reason, f.label, f.box?.map(format).join(", ") || "—", f.tags.join(", ")]));
}
function renderTrajectories(rows) {
  const container = $("trajectory"); container.replaceChildren();
  if (!rows.length) { container.append(node("p", "No trajectory samples.", "hint")); return; }
  for (const row of rows) {
    const points = [...row.trace, ...row.reference_trace];
    const minX = Math.min(...points.map(p => p.x_m)), maxX = Math.max(...points.map(p => p.x_m));
    const minY = Math.min(...points.map(p => p.y_m)), maxY = Math.max(...points.map(p => p.y_m));
    const scale = Math.min(700 / Math.max(maxX - minX, 1), 230 / Math.max(maxY - minY, 1));
    const ns = "http://www.w3.org/2000/svg", svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 760 290"); svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", `${row.scenario_id} reference and observed trajectory in metres`);
    for (const [trace, color, dash] of [[row.reference_trace, "#bbbbbb", "6 4"], [row.trace, "#ff62bf", ""]]) {
      const line = document.createElementNS(ns, "polyline");
      line.setAttribute("points", trace.map(p => `${30 + (p.x_m - minX) * scale},${260 - (p.y_m - minY) * scale}`).join(" "));
      line.setAttribute("fill", "none"); line.setAttribute("stroke", color); line.setAttribute("stroke-width", "2.5");
      if (dash) line.setAttribute("stroke-dasharray", dash); svg.append(line);
    }
    container.append(node("p", `${row.scenario_id} · grey dashed: reference · pink: observed · equal metre scale · coverage ${format(row.coverage)}`, "trajectory-caption"), svg);
  }
}
async function inspectRun(id) {
  const report = await request(`/runs/${encodeURIComponent(id)}`); selectedReport = report;
  $("detail-panel").hidden = false; $("detail-heading").textContent = `03 / ${report.candidate_version}`;
  $("gate-status").textContent = report.gate.status.toUpperCase(); $("gate-status").className = `badge ${report.gate.passed ? "pass" : "blocked"}`;
  $("run-meta").textContent = `${report.source.kind} · ${report.source.dataset} · suite ${report.suite_version} · run ${report.run_id}`;
  $("metric-cards").replaceChildren();
  for (const [label, value] of [["Precision", report.perception.precision], ["Recall", report.perception.recall], ["mAP @ configured IoU", report.perception.map_at_iou], ["ADE / m", report.planning.ade_m], ["FDE / m", report.planning.fde_m], ["Parking success", report.planning.parking_success_rate]]) {
    const card = node("div", undefined, "metric-card"); card.append(node("span", label), node("strong", format(value))); $("metric-cards").append(card);
  }
  $("exports").replaceChildren();
  for (const [label, suffix] of [["HTML report", "/report.html"], ["Failure CSV", "/failures.csv"], ["Input JSON", "/input"], ["Report JSON", ""]]) {
    const link = node("a", label, "btn subtle compact"); link.href = `${apiBase}/evaluation/runs/${encodeURIComponent(id)}${suffix}`; link.target = "_blank"; link.rel = "noopener"; $("exports").append(link);
  }
  table("gates", ["Metric", "Requirement", "Actual", "Status"], report.gate.checks.map(c => [c.metric, `${c.op} ${c.value}`, c.actual, c.status]));
  table("slices", ["Slice", "Labelled frames", "Precision", "Recall", "FDE / m", "Coverage"], Object.entries(report.slices).map(([key, s]) => [key, s.perception.labelled_frames, s.perception.precision, s.perception.recall, s.planning.fde_m, s.planning.coverage]));
  renderTrajectories(report.planning.per_scenario); renderFailures();
  $("provenance").textContent = JSON.stringify({ source: report.source, input_digest: report.input_digest, truth_digest: report.truth_digest, policy_digest: report.policy_digest, engine: report.engine_version, limitations: report.limitations }, null, 2);
}
async function loadDefects() {
  const status = $("defect-filter").value;
  const { items } = await request(`/defects${status ? `?status=${encodeURIComponent(status)}` : ""}`);
  table("defects", ["Failure", "Status / occurrences", "History", "Review action"], items.map(d => {
    const history = node("details"), summary = node("summary", `${d.history.length} events`), list = node("ul");
    d.history.forEach(h => list.append(node("li", `${h.at} · ${h.status} · ${h.note}`))); history.append(summary, list);
    const form = node("form", undefined, "defect-editor"), select = node("select"), note = node("textarea"), button = node("button", "Save review", "btn subtle compact");
    select.setAttribute("aria-label", "Defect status"); ["open", "resolved", "ignored"].forEach(s => { const opt = node("option", s); opt.value = s; select.append(opt); }); select.value = d.status;
    note.setAttribute("aria-label", "Review note"); note.placeholder = "Reason / fix reference"; note.required = true; note.maxLength = 2000;
    button.type = "submit"; form.append(select, note, button);
    form.addEventListener("submit", e => { e.preventDefault(); busy(button, async () => { await request(`/defects/${d.defect_id}`, {method: "PATCH", headers: {"Content-Type": "application/json"}, body: JSON.stringify({status: select.value, note: note.value})}); await loadDefects(); message("Defect review saved. Historical reports are unchanged."); }); });
    return [`${d.failure.scenario_id} / ${d.failure.frame_id} / ${d.failure.reason} / ${d.failure.label}`, `${d.status} · ${d.occurrences}`, history, form];
  }));
}
$("connection-form").addEventListener("submit", e => { e.preventDefault(); busy(e.submitter, connect); });
async function connect() {
  const url = new URL(endpoint.value.trim());
  if (!["http:", "https:"].includes(url.protocol)) throw new Error("Use an HTTP or HTTPS backend endpoint.");
  apiBase = url.href.replace(/\/+$/, "");
  if (!apiBase.endsWith("/api/v1")) apiBase += "/api/v1";
  await request("/health"); localStorage.setItem("autofleet_api_base", apiBase);
  await loadRuns(); await loadDefects(); message("Connected. Import a dataset or inspect recorded runs.");
}
$("dataset-file").addEventListener("change", async e => {
  const file = e.target.files[0]; if (!file) return;
  if (file.size > 20 * 1024 * 1024) { message("Split datasets larger than 20 MiB before importing.", true); return; }
  $("dataset-json").value = await file.text(); message(`Loaded ${file.name}. Review the versions and gates before running.`);
});
$("example").addEventListener("click", () => busy($("example"), async () => { $("dataset-json").value = JSON.stringify(await request("/example"), null, 2); message("Loaded a synthetic functional fixture. No evaluation has run yet."); }));
$("run").addEventListener("click", () => busy($("run"), async () => {
  const input = JSON.parse($("dataset-json").value); const report = await request("/runs", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(input)});
  await loadRuns(); await inspectRun(report.run_id); await loadDefects(); message(`Evaluation recorded: ${report.gate.status}.`);
}));
$("failure-filter").addEventListener("input", renderFailures);
$("defect-filter").addEventListener("change", () => loadDefects().catch(e => message(e.message, true)));
$("compare-form").addEventListener("submit", e => { e.preventDefault(); busy(e.submitter, async () => {
  const result = await request(`/compare?baseline=${encodeURIComponent($("baseline").value)}&candidate=${encodeURIComponent($("candidate").value)}`);
  const summary = node("p", `${result.regressed_metrics.length} regressed metrics / slices · candidate gate ${result.candidate_gate.status}`, result.regression_free ? "pass" : "blocked");
  const container = node("div");
  const rows = Object.entries(result.metrics).filter(([, v]) => v.baseline !== null || v.candidate !== null).map(([k, v]) => [k, v.baseline, v.candidate, v.delta, v.regressed ? "Regressed" : "—"]);
  for (const [slice, metrics] of Object.entries(result.slices)) for (const [key, value] of Object.entries(metrics)) if (value.regressed) rows.push([`${slice} / ${key}`, value.baseline, value.candidate, value.delta, "Regressed"]);
  table(container, ["Metric / slice", "Baseline", "Candidate", "Delta", "Review"], rows); $("comparison").replaceChildren(summary, container);
}); });
connect().catch(e => message(`Not connected: ${e.message}`, true));
