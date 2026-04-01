const defaultHost = window.location.hostname || "127.0.0.1";
const defaultProto = window.location.protocol === "https:" ? "https:" : "http:";
const defaultApiBases = [
  `${defaultProto}//${defaultHost}:8000/api/v1`,
  `${defaultProto}//${defaultHost}:8200/api/v1`,
  `${defaultProto}//${defaultHost}:8001/api/v1`,
];
const savedApiBase = (localStorage.getItem("autofleet_api_base") || "").replace(/\/+$/, "");
const hasPinnedApiBase = Boolean(savedApiBase) && !defaultApiBases.includes(savedApiBase);
let apiBase = savedApiBase || defaultApiBases[0];

const output = document.getElementById("output");
const robotTable = document.getElementById("robotTable");
const videoWall = document.getElementById("videoWall");
const fleetOverview = document.getElementById("fleetOverview");
const riskMap = document.getElementById("riskMap");
const mapSummaryGrid = document.getElementById("mapSummaryGrid");
const alertList = document.getElementById("alertList");
const protocolSummary = document.getElementById("protocolSummary");
const protocolOutput = document.getElementById("protocolOutput");
const formationStatus = document.getElementById("formationStatus");
const teleopStatus = document.getElementById("teleopStatus");
const apiBaseInput = document.getElementById("apiBase");
const autoRefreshToggle = document.getElementById("autoRefreshToggle");

const networkChart = document.getElementById("networkChart");
const networkLegend = document.getElementById("networkLegend");
const networkSummary = document.getElementById("networkSummary");
const networkMetricSelect = document.getElementById("networkMetricSelect");
const MAX_NETWORK_POINTS = 120;
const MAX_DIAG_POINTS = 240;

const viewControlBtn = document.getElementById("viewControlBtn");
const viewDiagnosticsBtn = document.getElementById("viewDiagnosticsBtn");
const diagSnapshotBtn = document.getElementById("diagSnapshotBtn");
const diagStressBtn = document.getElementById("diagStressBtn");
const diagVideoRobotCount = document.getElementById("diagVideoRobotCount");
const diagStopBtn = document.getElementById("diagStopBtn");
const diagStatus = document.getElementById("diagStatus");
const diagChart = document.getElementById("diagChart");
const diagCards = document.getElementById("diagCards");
const diagProtocolCards = document.getElementById("diagProtocolCards");
const diagRobotTable = document.getElementById("diagRobotTable");
const diagOutput = document.getElementById("diagOutput");

const robotIdInput = document.getElementById("robotId");
const teleopRobotIdInput = document.getElementById("teleopRobotId");
const leaderRobotIdInput = document.getElementById("leaderRobotId");
const followerRobotIdsInput = document.getElementById("followerRobotIds");

apiBaseInput.value = apiBase;

let robotsCache = [];
let alertsCache = [];
let healthCache = null;
let protocolSpecCache = null;
let eventsCache = [];
let refreshTimer = null;
let teleopTimer = null;
let activeKeys = new Set();
let lastTeleop = { robotId: "", linear_x: 0, angular_z: 0 };
const networkHistory = new Map();
const diagHistory = [];
let lastDiagSnapshot = null;
let diagTimer = null;
let diagTickInFlight = false;
let stressTimer = null;
const stressState = {
  running: false,
  startedAt: 0,
  stopAt: 0,
  simulated_capacity_mbps: 0,
  vehicle_count: 3,
  samples: [],
  lastError: null,
};

function print(obj) {
  output.textContent = JSON.stringify(obj, null, 2);
}

function escapeHtml(raw) {
  return String(raw ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function fmtNum(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return Number(value).toFixed(digits);
}

function parseRobotIds(text) {
  return text
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);
}

function finiteOrNaN(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : Number.NaN;
}

function finiteValues(values) {
  return values.filter((v) => Number.isFinite(v));
}

function average(values) {
  const arr = finiteValues(values);
  if (!arr.length) return Number.NaN;
  return arr.reduce((a, b) => a + b, 0) / arr.length;
}

function percentile(values, p) {
  const arr = finiteValues(values).sort((a, b) => a - b);
  if (!arr.length) return Number.NaN;
  const idx = Math.max(0, Math.min(arr.length - 1, Math.floor((arr.length - 1) * p)));
  return arr[idx];
}

function computeJitter(values) {
  const arr = finiteValues(values);
  if (arr.length < 2) return Number.NaN;
  const diffs = [];
  for (let i = 1; i < arr.length; i += 1) {
    diffs.push(Math.abs(arr[i] - arr[i - 1]));
  }
  return average(diffs);
}

function bytesToKbps(bytes, durationMs) {
  if (!Number.isFinite(bytes) || !Number.isFinite(durationMs) || durationMs <= 0) return Number.NaN;
  return (bytes * 8) / (durationMs / 1000) / 1000;
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function formatTs(tsMs) {
  const d = new Date(tsMs);
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function riskColor(level) {
  if (level === "CRITICAL") return "#ff6ea8";
  if (level === "HIGH") return "#ff9a4d";
  if (level === "MEDIUM") return "#ffd95b";
  if (level === "LOW") return "#90d8ff";
  return "#7eff96";
}

function severityClass(level) {
  return level === "critical" || level === "CRITICAL" ? "critical" : level === "warning" || level === "HIGH" ? "warning" : "ok";
}

function highestRobotRisk(robot) {
  const alertSeverity = (robot.recent_alerts || []).some((x) => x.severity === "critical") ? "CRITICAL" : null;
  return alertSeverity || robot.latest_perception?.risk_level || robot.map_summary?.risk_level || robot.coordination?.collision_risk || "NONE";
}

function setApiBase(nextValue) {
  apiBase = nextValue.replace(/\/+$/, "");
  localStorage.setItem("autofleet_api_base", apiBase);
}

function uniqueApiBases(values) {
  return Array.from(new Set(values.map((value) => String(value || "").replace(/\/+$/, "")).filter(Boolean)));
}

async function fetchWithApiFallback(path, options = {}) {
  const candidates = hasPinnedApiBase ? [apiBase] : uniqueApiBases([apiBase, ...defaultApiBases]);
  let lastError = null;
  for (const candidate of candidates) {
    try {
      const res = await fetch(`${candidate}${path}`, options);
      if (candidate !== apiBase) {
        setApiBase(candidate);
        apiBaseInput.value = candidate;
      }
      return res;
    } catch (err) {
      lastError = err;
    }
  }
  throw lastError || new Error(`Unable to reach API for ${path}`);
}

function applyNeoTheme() {
  document.body.classList.add("theme-neo");
  drawNetworkChart();
  drawDiagChart();
}

async function api(path, options = {}) {
  const res = await fetchWithApiFallback(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const body = await res.json();
  if (!res.ok) throw new Error(body.detail || "API error");
  return body;
}

async function timedGet(path) {
  const started = performance.now();
  const res = await fetchWithApiFallback(path, {
    method: "GET",
    headers: { "Content-Type": "application/json" },
  });
  const text = await res.text();
  const ended = performance.now();
  let body = {};
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = { raw: text };
    }
  }
  const bytes = new TextEncoder().encode(text).length;
  if (!res.ok) {
    const detail = body && typeof body === "object" ? body.detail : null;
    throw new Error(detail || `HTTP ${res.status} @ ${path}`);
  }
  return {
    path,
    latency_ms: ended - started,
    bytes,
    throughput_kbps: bytesToKbps(bytes, ended - started),
    body,
  };
}

function colorForRobot(robotId) {
  const palette = ["#ff2ea6", "#00d6ff", "#f8ff57", "#7eff96", "#ff8c42", "#b695ff", "#ff5f7f", "#39f0d0"];
  let hash = 0;
  for (const ch of robotId) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  return palette[hash % palette.length];
}

function getLatestSample(robotId) {
  const series = networkHistory.get(robotId) || [];
  return series.length ? series[series.length - 1] : null;
}

function getMaxThroughput(robotId) {
  const series = networkHistory.get(robotId) || [];
  const values = finiteValues(series.map((x) => x.throughput_kbps));
  if (!values.length) return Number.NaN;
  return Math.max(...values);
}

function renderRobotTable(items) {
  robotTable.innerHTML = "";
  for (const robot of items) {
    const sample = getLatestSample(robot.robot_id);
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${escapeHtml(robot.robot_id)}</td>
      <td class="${robot.online ? "online" : "offline"}">${escapeHtml(robot.online)}</td>
      <td>${escapeHtml(robot.state ?? "-")}</td>
      <td>${escapeHtml(fmtNum(robot.battery, 3))}</td>
      <td>${escapeHtml(robot.last_seen_age_s ?? "-")}</td>
      <td>${escapeHtml(fmtNum(sample?.latency_ms, 1))}ms</td>
      <td>${escapeHtml(fmtNum(sample?.jitter_ms, 1))}ms</td>
      <td>${escapeHtml(fmtNum(sample?.throughput_kbps, 0))}kbps</td>
      <td>${escapeHtml(fmtNum(sample?.control_rtt_ms, 1))}ms</td>
      <td>${escapeHtml(robot.video_status?.proxy_url || robot.video_rtsp_url || "-")}</td>
    `;
    robotTable.appendChild(tr);
  }
}

function renderVideoWall(items) {
  if (!items.length) {
    videoWall.innerHTML = `<div class="video-empty">No connected robots yet. Publish telemetry to see streams.</div>`;
    return;
  }

  videoWall.innerHTML = items
    .map((robot) => {
      const pose = robot.pose || {};
      const controls = robot.controls || {};
      const motors = robot.motors || {};
      const ack = robot.latest_ack || {};
      const sample = getLatestSample(robot.robot_id) || {};
      const streamHtml = buildStreamView(robot);
      const detections = robot.latest_perception?.detections || [];
      const risk = highestRobotRisk(robot);
      const streamState = robot.video_status?.status || "offline";
      const flags = [
        `<span class="flag-pill ${severityClass(streamState === "online" ? "ok" : "warning")}">stream ${escapeHtml(streamState)}</span>`,
        `<span class="flag-pill ${severityClass(risk)}">risk ${escapeHtml(risk)}</span>`,
      ]
        .concat(
          detections.slice(0, 3).map(
            (d) => `<span class="flag-pill ${severityClass(d.severity)}">${escapeHtml(d.label)}</span>`
          )
        )
        .join("");
      return `
      <article class="video-card">
        <div class="video-card-head">
          <strong>${escapeHtml(robot.robot_id)}</strong>
          <span class="${robot.online ? "online" : "offline"}">${escapeHtml(robot.state ?? "UNKNOWN")}</span>
        </div>
        <div class="video-view">${streamHtml}</div>
        <div class="video-meta">
          <div class="meta-item"><span class="meta-key">Battery</span>${escapeHtml(fmtNum(robot.battery, 3))}</div>
          <div class="meta-item"><span class="meta-key">Last Seen(s)</span>${escapeHtml(robot.last_seen_age_s ?? "-")}</div>
          <div class="meta-item"><span class="meta-key">Pose(X,Y,Yaw)</span>${escapeHtml(`${fmtNum(pose.x, 2)}, ${fmtNum(pose.y, 2)}, ${fmtNum(pose.yaw, 2)}`)}</div>
          <div class="meta-item"><span class="meta-key">Input(Lin,Ang)</span>${escapeHtml(`${fmtNum(controls.linear_x, 2)}, ${fmtNum(controls.angular_z, 2)}`)}</div>
          <div class="meta-item"><span class="meta-key">Output(L,R RPM)</span>${escapeHtml(`${fmtNum(motors.left_rpm, 1)}, ${fmtNum(motors.right_rpm, 1)}`)}</div>
          <div class="meta-item"><span class="meta-key">Latest ACK</span>${escapeHtml(ack.status ?? "-")}</div>
          <div class="meta-item"><span class="meta-key">Latency / Jitter</span>${escapeHtml(`${fmtNum(sample.latency_ms, 1)}ms / ${fmtNum(sample.jitter_ms, 1)}ms`)}</div>
          <div class="meta-item"><span class="meta-key">Throughput / RTT</span>${escapeHtml(`${fmtNum(sample.throughput_kbps, 0)}kbps / ${fmtNum(sample.control_rtt_ms, 1)}ms`)}</div>
          <div class="meta-item"><span class="meta-key">Perception</span>${escapeHtml(detections.length ? detections.map((d) => d.label).join(", ") : "no detection")}</div>
          <div class="meta-item"><span class="meta-key">Obstacles / Stream</span>${escapeHtml(`${robot.latest_perception?.obstacle_count ?? 0} / ${streamState}`)}</div>
        </div>
        <div class="video-flags">${flags}</div>
      </article>`;
    })
    .join("");
}

function buildStreamView(robot) {
  const proxyUrl = robot.video_status?.proxy_url || "";
  const snapshotUrl = robot.video_status?.snapshot_url || "";
  const rtspUrl = robot.video_rtsp_url || "";
  const note = robot.video_status?.note || "";
  if (proxyUrl) {
    return `<img class="mjpeg-stream" src="${escapeHtml(proxyUrl)}" alt="Live stream for ${escapeHtml(robot.robot_id)}">`;
  }
  if (snapshotUrl) {
    return `<img class="snapshot-thumb" src="${escapeHtml(snapshotUrl)}" alt="Snapshot for ${escapeHtml(robot.robot_id)}">`;
  }
  if (rtspUrl) {
    return `
      <div class="stream-note">
        <div>Source registered for ${escapeHtml(robot.robot_id)}, waiting for proxy stream.</div>
        <a class="rtsp-link" href="${escapeHtml(rtspUrl)}" target="_blank" rel="noopener">Open ${escapeHtml(rtspUrl)}</a>
        <div>${escapeHtml(note || "Video worker has not published a proxy URL yet.")}</div>
      </div>
    `;
  }
  return `<div class="stream-note">No stream URL in telemetry yet.</div>`;
}

function syncDefaultRobotIds(items) {
  if (!items.length) return;
  const ids = items.map((x) => x.robot_id);
  const first = ids[0];
  if (!robotIdInput.value.trim()) robotIdInput.value = first;
  if (!teleopRobotIdInput.value.trim()) teleopRobotIdInput.value = first;
  if (!leaderRobotIdInput.value.trim()) leaderRobotIdInput.value = first;
  if (!followerRobotIdsInput.value.trim()) followerRobotIdsInput.value = ids.slice(1).join(",");
}

function updateNetworkHistory(items) {
  const now = Date.now();
  const activeIds = new Set();

  for (const robot of items) {
    activeIds.add(robot.robot_id);
    const series = networkHistory.get(robot.robot_id) || [];
    const prev = series.length ? series[series.length - 1] : null;

    const latency = finiteOrNaN(robot.network?.latency_ms);
    const throughput = finiteOrNaN(robot.network?.throughput_kbps);
    const packetLoss = finiteOrNaN(robot.network?.packet_loss_pct);
    const rssi = finiteOrNaN(robot.network?.rssi_dbm);
    const controlRtt = finiteOrNaN(robot.control_rtt_ms);
    const jitter =
      Number.isFinite(latency) && prev && Number.isFinite(prev.latency_ms)
        ? Math.abs(latency - prev.latency_ms)
        : Number.NaN;

    const sample = {
      t: now,
      latency_ms: latency,
      jitter_ms: jitter,
      throughput_kbps: throughput,
      packet_loss_pct: packetLoss,
      rssi_dbm: rssi,
      control_rtt_ms: controlRtt,
    };

    series.push(sample);
    if (series.length > MAX_NETWORK_POINTS) {
      series.splice(0, series.length - MAX_NETWORK_POINTS);
    }
    networkHistory.set(robot.robot_id, series);
  }

  for (const robotId of Array.from(networkHistory.keys())) {
    if (!activeIds.has(robotId)) {
      const series = networkHistory.get(robotId) || [];
      const last = series[series.length - 1];
      if (!last || now - last.t > 60_000) {
        networkHistory.delete(robotId);
      }
    }
  }
}

function metricConfig(metric) {
  if (metric === "throughput_kbps") return { label: "Throughput", unit: "kbps", minMax: 300 };
  if (metric === "packet_loss_pct") return { label: "Packet Loss", unit: "%", minMax: 5 };
  if (metric === "jitter_ms") return { label: "Jitter", unit: "ms", minMax: 20 };
  if (metric === "control_rtt_ms") return { label: "Control RTT", unit: "ms", minMax: 40 };
  return { label: "Latency", unit: "ms", minMax: 80 };
}

function drawNetworkChart() {
  const ctx = networkChart.getContext("2d");
  if (!ctx) return;

  const metric = networkMetricSelect.value || "latency_ms";
  const cfg = metricConfig(metric);
  const bg = "#111111";
  const grid = "#2b2b2b";
  const axis = "#ffffff";
  const label = "#f7f7f7";

  const rect = networkChart.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const width = Math.max(320, Math.floor(rect.width));
  const height = Math.max(180, Math.floor(rect.height || 180));
  networkChart.width = Math.floor(width * dpr);
  networkChart.height = Math.floor(height * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  const padding = { top: 14, right: 14, bottom: 24, left: 44 };
  const plotW = width - padding.left - padding.right;
  const plotH = height - padding.top - padding.bottom;

  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, width, height);
  ctx.strokeStyle = "#000000";
  ctx.lineWidth = 2;
  ctx.strokeRect(0, 0, width, height);

  const allValues = finiteValues(
    Array.from(networkHistory.values()).flatMap((series) => series.map((s) => s[metric]))
  );
  const maxY = allValues.length ? Math.max(cfg.minMax, ...allValues) : cfg.minMax;
  const minY = 0;

  ctx.strokeStyle = grid;
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i += 1) {
    const y = padding.top + (plotH * i) / 4;
    ctx.beginPath();
    ctx.moveTo(padding.left, y);
    ctx.lineTo(padding.left + plotW, y);
    ctx.stroke();
  }
  for (let i = 0; i <= 6; i += 1) {
    const x = padding.left + (plotW * i) / 6;
    ctx.beginPath();
    ctx.moveTo(x, padding.top);
    ctx.lineTo(x, padding.top + plotH);
    ctx.stroke();
  }

  ctx.strokeStyle = axis;
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(padding.left, padding.top);
  ctx.lineTo(padding.left, padding.top + plotH);
  ctx.lineTo(padding.left + plotW, padding.top + plotH);
  ctx.stroke();

  const yToPx = (v) => padding.top + ((maxY - v) / (maxY - minY || 1)) * plotH;
  for (const [robotId, series] of Array.from(networkHistory.entries()).sort(([a], [b]) => a.localeCompare(b))) {
    const points = series.filter((s) => Number.isFinite(s[metric]));
    if (points.length < 2) continue;

    ctx.strokeStyle = colorForRobot(robotId);
    ctx.lineWidth = 2.2;
    ctx.beginPath();
    for (let i = 0; i < points.length; i += 1) {
      const x = padding.left + (plotW * i) / (MAX_NETWORK_POINTS - 1);
      const y = yToPx(points[i][metric]);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();
  }

  ctx.fillStyle = label;
  ctx.font = "12px IBM Plex Mono, monospace";
  ctx.fillText(`${cfg.label} (${cfg.unit})`, padding.left + 4, 12);
  ctx.fillText(`${Math.round(maxY)}${cfg.unit}`, 2, padding.top + 10);
  ctx.fillText(`0${cfg.unit}`, 8, padding.top + plotH);
  ctx.fillText(`-${Math.round(MAX_NETWORK_POINTS)}s`, padding.left + 2, height - 6);
  ctx.fillText("now", width - 28, height - 6);
}

function renderNetworkSummary(items) {
  const latestSamples = items.map((r) => getLatestSample(r.robot_id)).filter(Boolean);
  const avgLatency = average(latestSamples.map((s) => s.latency_ms));
  const avgJitter = average(latestSamples.map((s) => s.jitter_ms));
  const avgLoss = average(latestSamples.map((s) => s.packet_loss_pct));
  const avgRtt = average(latestSamples.map((s) => s.control_rtt_ms));
  const totalThroughput = finiteValues(latestSamples.map((s) => s.throughput_kbps)).reduce((a, b) => a + b, 0);
  const peakThroughput = Math.max(
    0,
    ...Array.from(networkHistory.keys()).map((robotId) => getMaxThroughput(robotId)).filter((v) => Number.isFinite(v))
  );
  const onlineCount = items.filter((x) => x.online).length;

  networkSummary.innerHTML = `
    <div class="summary-card"><span>Connected</span><strong>${escapeHtml(onlineCount)}</strong></div>
    <div class="summary-card"><span>Avg Latency</span><strong>${escapeHtml(fmtNum(avgLatency, 1))} ms</strong></div>
    <div class="summary-card"><span>Avg Jitter</span><strong>${escapeHtml(fmtNum(avgJitter, 1))} ms</strong></div>
    <div class="summary-card"><span>Avg Packet Loss</span><strong>${escapeHtml(fmtNum(avgLoss, 2))} %</strong></div>
    <div class="summary-card"><span>Total Throughput</span><strong>${escapeHtml(fmtNum(totalThroughput, 0))} kbps</strong></div>
    <div class="summary-card"><span>Peak Throughput</span><strong>${escapeHtml(fmtNum(peakThroughput, 0))} kbps</strong></div>
    <div class="summary-card"><span>Avg Control RTT</span><strong>${escapeHtml(fmtNum(avgRtt, 1))} ms</strong></div>
  `;
}

function renderNetworkLegend(items) {
  if (!items.length) {
    networkLegend.innerHTML = `<div class="legend-item legend-empty">No telemetry yet. Start robot_sim or publish MQTT telemetry.</div>`;
    return;
  }
  networkLegend.innerHTML = items
    .map((robot) => {
      const sample = getLatestSample(robot.robot_id) || {};
      const maxThr = getMaxThroughput(robot.robot_id);
      const color = colorForRobot(robot.robot_id);
      return `
        <div class="legend-item">
          <span class="legend-color" style="background:${escapeHtml(color)}"></span>
          <strong>${escapeHtml(robot.robot_id)}</strong>
          <span>lat ${escapeHtml(fmtNum(sample.latency_ms, 1))}ms</span>
          <span>jit ${escapeHtml(fmtNum(sample.jitter_ms, 1))}ms</span>
          <span>thr ${escapeHtml(fmtNum(sample.throughput_kbps, 0))}kbps</span>
          <span>max ${escapeHtml(fmtNum(maxThr, 0))}kbps</span>
          <span>loss ${escapeHtml(fmtNum(sample.packet_loss_pct, 1))}%</span>
          <span>rtt ${escapeHtml(fmtNum(sample.control_rtt_ms, 1))}ms</span>
        </div>`;
    })
    .join("");
}

function renderFleetOverview(items) {
  if (!fleetOverview) return;
  const onlineCount = items.filter((x) => x.online).length;
  const avgBattery = average(items.map((x) => Number(x.battery) * 100));
  const activeAlerts = alertsCache.length;
  const criticalAlerts = alertsCache.filter((x) => x.severity === "critical").length;
  const proxiedStreams = items.filter((x) => x.video_status?.proxy_url).length;
  const highRisk = items.filter((x) => ["HIGH", "CRITICAL"].includes(highestRobotRisk(x))).length;
  const services = healthCache?.protocol?.service_heartbeats_age_s || {};
  const healthyServices = Object.values(services).filter((age) => Number(age) <= 10).length;
  const collisionRisk = items.filter((x) => ["HIGH", "CRITICAL"].includes(x.coordination?.collision_risk)).length;

  fleetOverview.innerHTML = `
    <div class="summary-card"><span>Connected Robots</span><strong>${escapeHtml(onlineCount)}/${escapeHtml(items.length)}</strong></div>
    <div class="summary-card"><span>Avg Battery</span><strong>${escapeHtml(fmtNum(avgBattery, 1))}%</strong></div>
    <div class="summary-card"><span>Live Video Proxies</span><strong>${escapeHtml(proxiedStreams)}</strong></div>
    <div class="summary-card"><span>Active Alerts</span><strong>${escapeHtml(activeAlerts)}</strong></div>
    <div class="summary-card"><span>Critical Alerts</span><strong>${escapeHtml(criticalAlerts)}</strong></div>
    <div class="summary-card"><span>High-risk Robots</span><strong>${escapeHtml(highRisk)}</strong></div>
    <div class="summary-card"><span>Collision Risk Robots</span><strong>${escapeHtml(collisionRisk)}</strong></div>
    <div class="summary-card"><span>Healthy Services</span><strong>${escapeHtml(healthyServices)}/${escapeHtml(Object.keys(services).length || 0)}</strong></div>
  `;
}

function renderMapSummaryGrid(items) {
  if (!mapSummaryGrid) return;
  if (!items.length) {
    mapSummaryGrid.innerHTML = "";
    return;
  }
  mapSummaryGrid.innerHTML = items
    .map((robot) => {
      const map = robot.map_summary || {};
      const coord = robot.coordination || {};
      return `
        <div class="summary-card">
          <span>${escapeHtml(robot.robot_id)}</span>
          <strong>${escapeHtml(map.obstacle_count ?? 0)} obstacle(s)</strong>
          <div>${escapeHtml(`map risk ${map.risk_level || "NONE"}`)}</div>
          <div>${escapeHtml(`role ${coord.role || "independent"}`)}</div>
          <div>${escapeHtml(`min peer ${fmtNum(coord.min_peer_distance_m, 2)} m`)}</div>
        </div>
      `;
    })
    .join("");
}

function renderRiskMap(items) {
  if (!riskMap) return;
  const robots = items.filter((robot) => Number.isFinite(Number(robot.pose?.x)) && Number.isFinite(Number(robot.pose?.y)));
  if (!robots.length) {
    riskMap.innerHTML = `<div class="risk-map-empty">No robot positions yet. Publish telemetry to populate the spatial view.</div>`;
    return;
  }

  const xs = robots.map((robot) => Number(robot.pose.x));
  const ys = robots.map((robot) => Number(robot.pose.y));
  const minX = Math.min(...xs) - 1;
  const maxX = Math.max(...xs) + 1;
  const minY = Math.min(...ys) - 1;
  const maxY = Math.max(...ys) + 1;
  const width = 620;
  const height = 286;
  const scaleX = (x) => 30 + ((x - minX) / Math.max(1, maxX - minX)) * (width - 60);
  const scaleY = (y) => height - 30 - ((y - minY) / Math.max(1, maxY - minY)) * (height - 60);

  const obstacleMarks = robots
    .flatMap((robot) =>
      (robot.map_summary?.obstacles || []).map((obs) => {
        const px = scaleX(Number(obs.x || robot.pose.x));
        const py = scaleY(Number(obs.y || robot.pose.y));
        return `<rect x="${px - 8}" y="${py - 8}" width="16" height="16" fill="${riskColor(robot.map_summary?.risk_level || "MEDIUM")}" stroke="#fff" stroke-width="2" />`;
      })
    )
    .join("");

  const robotMarks = robots
    .map((robot) => {
      const px = scaleX(Number(robot.pose.x));
      const py = scaleY(Number(robot.pose.y));
      const risk = highestRobotRisk(robot);
      const ring = ["HIGH", "CRITICAL"].includes(risk)
        ? `<circle cx="${px}" cy="${py}" r="24" fill="none" stroke="${riskColor(risk)}" stroke-width="4" stroke-dasharray="5 4" />`
        : "";
      return `
        ${ring}
        <circle cx="${px}" cy="${py}" r="13" fill="${riskColor(risk)}" stroke="#ffffff" stroke-width="3" />
        <text x="${px + 16}" y="${py - 12}" fill="#ffffff" font-size="12" font-family="IBM Plex Mono, monospace">${escapeHtml(robot.robot_id)}</text>
        <text x="${px + 16}" y="${py + 6}" fill="#bbbbbb" font-size="10" font-family="IBM Plex Mono, monospace">${escapeHtml(robot.coordination?.role || "independent")}</text>
      `;
    })
    .join("");

  const links = robots
    .flatMap((robot) =>
      (robot.coordination?.neighbors || [])
        .slice(0, 1)
        .map((neighbor) => {
          const peer = robots.find((item) => item.robot_id === neighbor.robot_id);
          if (!peer) return "";
          return `<line x1="${scaleX(Number(robot.pose.x))}" y1="${scaleY(Number(robot.pose.y))}" x2="${scaleX(Number(peer.pose.x))}" y2="${scaleY(Number(peer.pose.y))}" stroke="${riskColor(neighbor.risk_level)}" stroke-width="2" stroke-dasharray="6 5" />`;
        })
    )
    .join("");

  riskMap.innerHTML = `
    <svg class="risk-stage" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none">
      <rect x="0" y="0" width="${width}" height="${height}" fill="#0d0d0d" />
      <g stroke="#303030" stroke-width="1">
        <line x1="30" y1="30" x2="30" y2="${height - 30}" />
        <line x1="30" y1="${height - 30}" x2="${width - 30}" y2="${height - 30}" />
        <line x1="30" y1="${height / 2}" x2="${width - 30}" y2="${height / 2}" />
        <line x1="${width / 2}" y1="30" x2="${width / 2}" y2="${height - 30}" />
      </g>
      ${links}
      ${obstacleMarks}
      ${robotMarks}
    </svg>
    <div class="risk-legend">
      <div class="risk-legend-item"><span class="risk-dot" style="background:${riskColor("LOW")}"></span>Low</div>
      <div class="risk-legend-item"><span class="risk-dot" style="background:${riskColor("MEDIUM")}"></span>Medium</div>
      <div class="risk-legend-item"><span class="risk-dot" style="background:${riskColor("HIGH")}"></span>High</div>
      <div class="risk-legend-item"><span class="risk-dot" style="background:${riskColor("CRITICAL")}"></span>Critical</div>
    </div>
  `;
}

function renderAlertList(alerts) {
  if (!alertList) return;
  if (!alerts.length) {
    alertList.innerHTML = `<div class="video-empty">No active alerts. Perception worker has not raised any hazard yet.</div>`;
    return;
  }
  alertList.innerHTML = alerts
    .map(
      (alert) => `
        <article class="alert-card ${severityClass(alert.severity)}">
          <div class="alert-head">
            <strong>${escapeHtml(alert.alert_type)}</strong>
            <button class="btn subtle ack-btn" data-alert-id="${escapeHtml(alert.alert_id)}">Acknowledge</button>
          </div>
          <div>${escapeHtml(alert.message)}</div>
          <div class="alert-meta">${escapeHtml(`${alert.robot_id} | ${alert.severity} | ${formatTs((alert.ts || 0) * 1000)}`)}</div>
          <div class="alert-meta">${escapeHtml((alert.metadata?.detection_labels || []).join(", ") || "no labels")}</div>
        </article>
      `
    )
    .join("");
}

function renderProtocolSummary(items) {
  if (!protocolSummary || !protocolOutput) return;
  const protocol = healthCache?.protocol || {};
  const topics = Object.keys(protocolSpecCache?.topics || {}).length;
  const serviceAges = protocol.service_heartbeats_age_s || {};
  const robotAges = protocol.robot_heartbeats_age_s || {};
  const slowestService = Object.entries(serviceAges).sort((a, b) => Number(b[1]) - Number(a[1]))[0];

  protocolSummary.innerHTML = `
    <div class="summary-card"><span>Schema Version</span><strong>${escapeHtml(protocol.schema_version || protocolSpecCache?.schema_version || "-")}</strong></div>
    <div class="summary-card"><span>Topic Families</span><strong>${escapeHtml(topics)}</strong></div>
    <div class="summary-card"><span>Pending Commands</span><strong>${escapeHtml(protocol.pending_commands ?? 0)}</strong></div>
    <div class="summary-card"><span>Robot Heartbeats</span><strong>${escapeHtml(Object.keys(robotAges).length)}</strong></div>
    <div class="summary-card"><span>Service Heartbeats</span><strong>${escapeHtml(Object.keys(serviceAges).length)}</strong></div>
    <div class="summary-card"><span>Active Alerts</span><strong>${escapeHtml(protocol.active_alerts ?? 0)}</strong></div>
    <div class="summary-card"><span>Collision Risk</span><strong>${escapeHtml(protocol.high_collision_risk_robots ?? 0)}</strong></div>
    <div class="summary-card"><span>Slowest Service</span><strong>${escapeHtml(slowestService ? `${slowestService[0]} ${slowestService[1]}s` : "-")}</strong></div>
  `;
  protocolOutput.textContent = JSON.stringify(
    {
      health: healthCache,
      protocol_spec: protocolSpecCache,
      recent_events: eventsCache,
      robots: items.map((robot) => ({
        robot_id: robot.robot_id,
        coordination: robot.coordination,
        alerts: robot.recent_alerts,
        video_status: robot.video_status,
      })),
    },
    null,
    2
  );
}

function setDiagStatus(text) {
  if (diagStatus) diagStatus.textContent = text;
}

function addDiagSample(latencyMs) {
  if (!Number.isFinite(latencyMs)) return;
  diagHistory.push({ t: Date.now(), latency_ms: latencyMs });
  if (diagHistory.length > MAX_DIAG_POINTS) {
    diagHistory.splice(0, diagHistory.length - MAX_DIAG_POINTS);
  }
}

function drawDiagChart() {
  if (!diagChart) return;
  const ctx = diagChart.getContext("2d");
  if (!ctx) return;

  const bg = "#111111";
  const grid = "#2b2b2b";
  const axis = "#ffffff";
  const label = "#f7f7f7";
  const line = "#ff2ea6";

  const rect = diagChart.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const width = Math.max(360, Math.floor(rect.width || 360));
  const height = Math.max(190, Math.floor(rect.height || 190));
  diagChart.width = Math.floor(width * dpr);
  diagChart.height = Math.floor(height * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  const padding = { top: 14, right: 14, bottom: 24, left: 48 };
  const plotW = width - padding.left - padding.right;
  const plotH = height - padding.top - padding.bottom;

  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, width, height);
  ctx.strokeStyle = "#000000";
  ctx.lineWidth = 2;
  ctx.strokeRect(0, 0, width, height);

  const videoSamples = stressState.samples || [];
  const hasVideoData = videoSamples.length >= 2;
  const seriesA = hasVideoData ? videoSamples.map((x) => x.offered_mbps) : diagHistory.map((x) => x.latency_ms);
  const seriesB = hasVideoData ? videoSamples.map((x) => x.delivered_mbps) : [];
  const values = finiteValues([...seriesA, ...seriesB]);
  const maxY = values.length
    ? Math.max(hasVideoData ? 10 : 80, ...values, hasVideoData ? stressState.simulated_capacity_mbps : 0)
    : hasVideoData
      ? 10
      : 80;
  const minY = 0;

  ctx.strokeStyle = grid;
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i += 1) {
    const y = padding.top + (plotH * i) / 4;
    ctx.beginPath();
    ctx.moveTo(padding.left, y);
    ctx.lineTo(padding.left + plotW, y);
    ctx.stroke();
  }
  for (let i = 0; i <= 6; i += 1) {
    const x = padding.left + (plotW * i) / 6;
    ctx.beginPath();
    ctx.moveTo(x, padding.top);
    ctx.lineTo(x, padding.top + plotH);
    ctx.stroke();
  }

  ctx.strokeStyle = axis;
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(padding.left, padding.top);
  ctx.lineTo(padding.left, padding.top + plotH);
  ctx.lineTo(padding.left + plotW, padding.top + plotH);
  ctx.stroke();

  const yToPx = (v) => padding.top + ((maxY - v) / (maxY - minY || 1)) * plotH;
  const pointsA = hasVideoData
    ? videoSamples.filter((x) => Number.isFinite(x.offered_mbps))
    : diagHistory.filter((x) => Number.isFinite(x.latency_ms));
  if (pointsA.length >= 2) {
    ctx.strokeStyle = line;
    ctx.lineWidth = 2.2;
    ctx.beginPath();
    for (let i = 0; i < pointsA.length; i += 1) {
      const x = padding.left + (plotW * i) / (MAX_DIAG_POINTS - 1);
      const y = yToPx(hasVideoData ? pointsA[i].offered_mbps : pointsA[i].latency_ms);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();
  }

  if (hasVideoData) {
    const pointsB = videoSamples.filter((x) => Number.isFinite(x.delivered_mbps));
    if (pointsB.length >= 2) {
      ctx.strokeStyle = "#00d6ff";
      ctx.lineWidth = 2.3;
      ctx.beginPath();
      for (let i = 0; i < pointsB.length; i += 1) {
        const x = padding.left + (plotW * i) / (MAX_DIAG_POINTS - 1);
        const y = yToPx(pointsB[i].delivered_mbps);
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.stroke();
    }

    if (Number.isFinite(stressState.simulated_capacity_mbps) && stressState.simulated_capacity_mbps > 0) {
      ctx.strokeStyle = "#f8ff57";
      ctx.setLineDash([6, 4]);
      ctx.lineWidth = 1.4;
      const y = yToPx(stressState.simulated_capacity_mbps);
      ctx.beginPath();
      ctx.moveTo(padding.left, y);
      ctx.lineTo(padding.left + plotW, y);
      ctx.stroke();
      ctx.setLineDash([]);
    }
  }

  ctx.fillStyle = label;
  ctx.font = "12px IBM Plex Mono, monospace";
  if (hasVideoData) {
    ctx.fillText("Video stress throughput (Mbps)", padding.left + 4, 12);
    ctx.fillText(`${Math.round(maxY)}Mbps`, 2, padding.top + 10);
    ctx.fillText("0Mbps", 8, padding.top + plotH);
    ctx.fillText(`-${Math.round(MAX_DIAG_POINTS / 4)}s`, padding.left + 2, height - 6);
    ctx.fillText("now", width - 28, height - 6);
    ctx.fillText("Pink=Offered  Cyan=Delivered  Yellow=Link cap", padding.left + 4, height - 6);
  } else {
    ctx.fillText("API latency (ms)", padding.left + 4, 12);
    ctx.fillText(`${Math.round(maxY)}ms`, 4, padding.top + 10);
    ctx.fillText("0ms", 10, padding.top + plotH);
    ctx.fillText(`-${Math.round(MAX_DIAG_POINTS)}s`, padding.left + 2, height - 6);
    ctx.fillText("now", width - 28, height - 6);
  }
}

function getVideoVehicleCount() {
  const selected = Number(diagVideoRobotCount?.value || 3);
  return clamp(Math.round(selected), 1, 5);
}

function stressSummary(nowMs = Date.now()) {
  const end = stressState.running ? nowMs : stressState.stopAt;
  const durationMs = Math.max(1, end - stressState.startedAt);
  const samples = stressState.samples || [];
  const delivered = finiteValues(samples.map((x) => x.delivered_mbps));
  const loss = finiteValues(samples.map((x) => x.loss_pct));
  const jitter = finiteValues(samples.map((x) => x.jitter_ms));
  const stableSamples = samples.filter((x) => x.loss_pct <= 2 && x.jitter_ms <= 20);
  const stableDelivered = finiteValues(stableSamples.map((x) => x.delivered_mbps));
  const totalLimitMbps = stableDelivered.length ? percentile(stableDelivered, 0.95) : percentile(delivered, 0.5);
  const vehicleCount = Math.max(1, stressState.vehicle_count || 1);
  const singleLimitMbps = Number.isFinite(totalLimitMbps) ? totalLimitMbps / vehicleCount : Number.NaN;
  const avgLoss = average(loss);
  const p95Jitter = percentile(jitter, 0.95);
  const maxDeliveredMbps = delivered.length ? Math.max(...delivered) : Number.NaN;
  const currentDeliveredMbps = delivered.length ? delivered[delivered.length - 1] : Number.NaN;
  const stablePct = samples.length ? (stableSamples.length * 100) / samples.length : Number.NaN;
  const isStable = Number.isFinite(avgLoss) && Number.isFinite(p95Jitter) && avgLoss <= 2 && p95Jitter <= 20 && stablePct >= 80;
  return {
    running: stressState.running,
    duration_s: durationMs / 1000,
    vehicle_count: vehicleCount,
    sample_count: samples.length,
    simulated_capacity_mbps: stressState.simulated_capacity_mbps,
    total_bw_limit_mbps: totalLimitMbps,
    per_stream_limit_mbps: singleLimitMbps,
    avg_packet_loss_pct: avgLoss,
    p95_jitter_ms: p95Jitter,
    max_delivered_mbps: maxDeliveredMbps,
    current_delivered_mbps: currentDeliveredMbps,
    stable_sample_pct: stablePct,
    stable: isStable,
    last_error: stressState.lastError,
  };
}

function renderDiagnostics(snapshot) {
  if (!diagCards || !diagProtocolCards || !diagRobotTable || !diagOutput) return;

  const items = snapshot.items || [];
  const latestSamples = items.map((r) => getLatestSample(r.robot_id)).filter(Boolean);
  const online = items.filter((x) => x.online).length;
  const avgTelemetryLatency = average(latestSamples.map((s) => s.latency_ms));
  const avgTelemetryJitter = average(latestSamples.map((s) => s.jitter_ms));
  const avgControlRtt = average(latestSamples.map((s) => s.control_rtt_ms));
  const totalTelemetryKbps = finiteValues(latestSamples.map((s) => s.throughput_kbps)).reduce((a, b) => a + b, 0);
  const peakTelemetryKbps = Math.max(
    0,
    ...items.map((r) => getMaxThroughput(r.robot_id)).filter((x) => Number.isFinite(x))
  );
  const streamCount = items.filter((r) => r.video_rtsp_url).length;
  const stress = stressSummary();

  diagCards.innerHTML = `
    <div class="summary-card"><span>Backend /health</span><strong>${escapeHtml(fmtNum(snapshot.health_latency_ms, 1))} ms</strong></div>
    <div class="summary-card"><span>Backend /robots</span><strong>${escapeHtml(fmtNum(snapshot.robots_latency_ms, 1))} ms</strong></div>
    <div class="summary-card"><span>Connected Robots</span><strong>${escapeHtml(online)}/${escapeHtml(items.length)}</strong></div>
    <div class="summary-card"><span>Avg Device Latency</span><strong>${escapeHtml(fmtNum(avgTelemetryLatency, 1))} ms</strong></div>
    <div class="summary-card"><span>Avg Device Jitter</span><strong>${escapeHtml(fmtNum(avgTelemetryJitter, 1))} ms</strong></div>
    <div class="summary-card"><span>Avg Control RTT</span><strong>${escapeHtml(fmtNum(avgControlRtt, 1))} ms</strong></div>
    <div class="summary-card"><span>Video Total Limit</span><strong>${escapeHtml(fmtNum(stress.total_bw_limit_mbps, 2))} Mbps</strong></div>
    <div class="summary-card"><span>Video Per-stream Limit</span><strong>${escapeHtml(fmtNum(stress.per_stream_limit_mbps, 2))} Mbps</strong></div>
    <div class="summary-card"><span>Sim Avg Packet Loss</span><strong>${escapeHtml(fmtNum(stress.avg_packet_loss_pct, 2))} %</strong></div>
    <div class="summary-card"><span>Sim P95 Jitter</span><strong>${escapeHtml(fmtNum(stress.p95_jitter_ms, 1))} ms</strong></div>
  `;

  diagProtocolCards.innerHTML = `
    <div class="summary-card"><span>HTTP Pull Throughput</span><strong>${escapeHtml(fmtNum(snapshot.http_kbps, 1))} kbps</strong></div>
    <div class="summary-card"><span>MQTT Telemetry Total</span><strong>${escapeHtml(fmtNum(totalTelemetryKbps, 0))} kbps</strong></div>
    <div class="summary-card"><span>MQTT Telemetry Peak</span><strong>${escapeHtml(fmtNum(peakTelemetryKbps, 0))} kbps</strong></div>
    <div class="summary-card"><span>RTSP Streams Online</span><strong>${escapeHtml(streamCount)}</strong></div>
    <div class="summary-card"><span>Sim Vehicles</span><strong>${escapeHtml(stress.vehicle_count)}</strong></div>
    <div class="summary-card"><span>Sim Link Capacity</span><strong>${escapeHtml(fmtNum(stress.simulated_capacity_mbps, 2))} Mbps</strong></div>
    <div class="summary-card"><span>Sim Max Delivered</span><strong>${escapeHtml(fmtNum(stress.max_delivered_mbps, 2))} Mbps</strong></div>
    <div class="summary-card"><span>Stability</span><strong>${stress.stable ? "Stable" : "Unstable"}</strong></div>
  `;

  diagRobotTable.innerHTML = items
    .map((robot) => {
      const sample = getLatestSample(robot.robot_id) || {};
      return `
      <tr>
        <td>${escapeHtml(robot.robot_id)}</td>
        <td class="${robot.online ? "online" : "offline"}">${escapeHtml(robot.online)}</td>
        <td>${escapeHtml(fmtNum(sample.latency_ms, 1))}ms</td>
        <td>${escapeHtml(fmtNum(sample.jitter_ms, 1))}ms</td>
        <td>${escapeHtml(fmtNum(sample.throughput_kbps, 0))}kbps</td>
        <td>${escapeHtml(fmtNum(sample.control_rtt_ms, 1))}ms</td>
        <td>${escapeHtml(fmtNum(sample.packet_loss_pct, 2))}%</td>
      </tr>`;
    })
    .join("");

  diagOutput.textContent = JSON.stringify(
    {
      ts: formatTs(Date.now()),
      snapshot,
      video_simulation_report: stress,
    },
    null,
    2
  );
}

async function refreshDiagnosticsSnapshot({ quiet = false } = {}) {
  const [health, robots] = await Promise.all([timedGet("/health"), timedGet("/robots")]);
  const items = robots.body.items || [];
  updateNetworkHistory(items);
  const pullLatency = [health.latency_ms, robots.latency_ms];
  const snapshot = {
    health_latency_ms: health.latency_ms,
    robots_latency_ms: robots.latency_ms,
    api_pull_jitter_ms: computeJitter(pullLatency),
    http_kbps: bytesToKbps(health.bytes + robots.bytes, health.latency_ms + robots.latency_ms),
    items,
  };
  lastDiagSnapshot = snapshot;
  addDiagSample(robots.latency_ms);
  drawDiagChart();
  renderDiagnostics(snapshot);
  if (!quiet) {
    setDiagStatus(`Snapshot refreshed at ${formatTs(Date.now())}`);
  }
}

function clearStressState() {
  stressState.running = false;
  stressState.startedAt = 0;
  stressState.stopAt = 0;
  stressState.simulated_capacity_mbps = 0;
  stressState.vehicle_count = getVideoVehicleCount();
  stressState.samples = [];
  stressState.lastError = null;
}

function stopStressTest({ byTimeout = false } = {}) {
  if (stressTimer) {
    clearInterval(stressTimer);
    stressTimer = null;
  }
  if (stressState.running) {
    stressState.running = false;
    stressState.stopAt = Date.now();
  }
  const s = stressSummary();
  if (byTimeout) {
    setDiagStatus(
      `Video stress done: total limit ${fmtNum(s.total_bw_limit_mbps, 2)} Mbps, per stream ${fmtNum(s.per_stream_limit_mbps, 2)} Mbps, ${s.stable ? "stable" : "unstable"}`
    );
  } else {
    setDiagStatus(
      `Video stress stopped: total limit ${fmtNum(s.total_bw_limit_mbps, 2)} Mbps, per stream ${fmtNum(s.per_stream_limit_mbps, 2)} Mbps`
    );
  }
  if (lastDiagSnapshot) {
    renderDiagnostics(lastDiagSnapshot);
    drawDiagChart();
  }
}

function simulateVideoSample(elapsedMs) {
  const vehicleCount = Math.max(1, stressState.vehicle_count || 1);
  const progress = clamp(elapsedMs / 60_000, 0, 1);
  const perStreamOffered = 1.2 + progress * 10.0;
  const offeredMbps = perStreamOffered * vehicleCount;
  const capacity = Math.max(8, stressState.simulated_capacity_mbps);
  const load = offeredMbps / capacity;

  const baseLoss = 0.2 + Math.random() * 0.35;
  const overloadLoss = load > 1 ? (load - 1) * (18 + vehicleCount * 1.3) + Math.random() * 1.5 : 0;
  const lossPct = clamp(baseLoss + overloadLoss, 0, 45);

  const baseJitter = 2.0 + Math.random() * 3.5;
  const overloadJitter = load > 1 ? (load - 1) * 30 + Math.random() * 5 : 0;
  const jitterMs = clamp(baseJitter + overloadJitter, 0.5, 120);

  let deliveredMbps = offeredMbps * (1 - lossPct / 100);
  deliveredMbps = Math.min(deliveredMbps, capacity * (0.95 + Math.random() * 0.05));
  deliveredMbps = Math.max(0, deliveredMbps);
  const perStreamDeliveredMbps = deliveredMbps / vehicleCount;

  return {
    t: Date.now(),
    elapsed_ms: elapsedMs,
    offered_mbps: offeredMbps,
    delivered_mbps: deliveredMbps,
    per_stream_delivered_mbps: perStreamDeliveredMbps,
    loss_pct: lossPct,
    jitter_ms: jitterMs,
  };
}

function runStressTick() {
  if (!stressState.running) return;
  const elapsed = Date.now() - stressState.startedAt;
  const sample = simulateVideoSample(elapsed);
  stressState.samples.push(sample);
  if (stressState.samples.length > MAX_DIAG_POINTS) {
    stressState.samples.splice(0, stressState.samples.length - MAX_DIAG_POINTS);
  }
  const remaining = Math.max(0, Math.ceil((60_000 - elapsed) / 1000));
  const s = stressSummary();
  setDiagStatus(
    `Video stress (${remaining}s left): delivered ${fmtNum(s.current_delivered_mbps, 2)} Mbps, loss ${fmtNum(s.avg_packet_loss_pct, 2)}%, jitter ${fmtNum(s.p95_jitter_ms, 1)}ms`
  );
  if (lastDiagSnapshot) {
    renderDiagnostics(lastDiagSnapshot);
  }
  drawDiagChart();
}

function startStressTest() {
  if (stressState.running) return;
  clearStressState();
  stressState.running = true;
  stressState.vehicle_count = getVideoVehicleCount();
  stressState.simulated_capacity_mbps = clamp(10 + Math.random() * 18 - stressState.vehicle_count * 0.6, 8, 35);
  stressState.startedAt = Date.now();
  stressState.stopAt = stressState.startedAt + 60_000;
  setDiagStatus(
    `Video simulation started: ${stressState.vehicle_count} robots, link cap ${fmtNum(stressState.simulated_capacity_mbps, 2)} Mbps`
  );
  stressTimer = setInterval(() => {
    runStressTick();
    if (Date.now() >= stressState.stopAt) {
      stopStressTest({ byTimeout: true });
    }
  }, 250);
  runStressTick();
}

function resetDiagnosticsAutoRefresh() {
  if (diagTimer) {
    clearInterval(diagTimer);
    diagTimer = null;
  }
  const inDiagnostics = document.body.classList.contains("show-diagnostics");
  if (inDiagnostics && autoRefreshToggle.checked) {
    diagTimer = setInterval(async () => {
      if (diagTickInFlight) return;
      diagTickInFlight = true;
      try {
        await refreshDiagnosticsSnapshot({ quiet: true });
      } catch {
        // Keep UI responsive when one diagnostics tick fails.
      } finally {
        diagTickInFlight = false;
      }
    }, 1000);
  }
}

function setView(mode) {
  const next = mode === "diagnostics" ? "diagnostics" : "control";
  const showDiagnostics = next === "diagnostics";
  if (!showDiagnostics && stressState.running) {
    stopStressTest({ byTimeout: false });
  }
  document.body.classList.toggle("show-diagnostics", showDiagnostics);
  if (viewControlBtn) viewControlBtn.classList.toggle("active", !showDiagnostics);
  if (viewDiagnosticsBtn) viewDiagnosticsBtn.classList.toggle("active", showDiagnostics);
  localStorage.setItem("autofleet_view", next);
  resetAutoRefresh();
  resetDiagnosticsAutoRefresh();
  if (showDiagnostics) {
    refreshDiagnosticsSnapshot({ quiet: true }).catch((err) => setDiagStatus(`Diagnostics refresh failed: ${err}`));
    drawDiagChart();
  }
}

async function refreshRobots({ quiet = false } = {}) {
  const [robotsData, alertsData, healthData, protocolData, eventsData] = await Promise.all([
    api("/robots"),
    api("/alerts?active_only=true"),
    api("/health"),
    api("/protocol"),
    api("/events?limit=20"),
  ]);
  robotsCache = robotsData.items || [];
  alertsCache = alertsData.items || [];
  healthCache = healthData;
  protocolSpecCache = protocolData;
  eventsCache = eventsData.items || [];
  updateNetworkHistory(robotsCache);
  renderRobotTable(robotsCache);
  renderVideoWall(robotsCache);
  renderNetworkSummary(robotsCache);
  renderNetworkLegend(robotsCache);
  renderFleetOverview(robotsCache);
  renderMapSummaryGrid(robotsCache);
  renderRiskMap(robotsCache);
  renderAlertList(alertsCache);
  renderProtocolSummary(robotsCache);
  drawNetworkChart();
  syncDefaultRobotIds(robotsCache);
  if (!quiet) {
    print({
      robots: robotsData,
      alerts: alertsData,
      health: healthData,
      protocol: protocolData,
      events: eventsData,
    });
  }
}

async function refreshFormation({ quiet = true } = {}) {
  const data = await api("/formation");
  formationStatus.textContent = JSON.stringify(data, null, 2);
  if (!quiet) print(data);
}

function resetAutoRefresh() {
  if (refreshTimer) {
    clearInterval(refreshTimer);
    refreshTimer = null;
  }
  if (autoRefreshToggle.checked && !document.body.classList.contains("show-diagnostics")) {
    refreshTimer = setInterval(async () => {
      try {
        await refreshRobots({ quiet: true });
        await refreshFormation({ quiet: true });
      } catch {
        // Keep UI responsive even when one tick fails.
      }
    }, 1000);
  }
}

function normalizeKey(key) {
  return String(key).toLowerCase();
}

function shouldHandleTeleop(ev) {
  const target = ev.target;
  if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.tagName === "SELECT")) {
    return false;
  }
  const k = normalizeKey(ev.key);
  return ["w", "a", "s", "d", "arrowup", "arrowdown", "arrowleft", "arrowright"].includes(k);
}

function computeTeleopVector() {
  const up = activeKeys.has("w") || activeKeys.has("arrowup");
  const down = activeKeys.has("s") || activeKeys.has("arrowdown");
  const left = activeKeys.has("a") || activeKeys.has("arrowleft");
  const right = activeKeys.has("d") || activeKeys.has("arrowright");

  const linear_x = (up ? 1 : 0) + (down ? -1 : 0);
  const angular_z = (left ? 1 : 0) + (right ? -1 : 0);
  return { linear_x, angular_z };
}

function updateTeleopStatus(linear_x, angular_z, err = null) {
  const robotId = teleopRobotIdInput.value.trim();
  teleopStatus.textContent = JSON.stringify(
    {
      robot_id: robotId,
      active_keys: Array.from(activeKeys.values()),
      linear_x,
      angular_z,
      error: err ? String(err) : null,
    },
    null,
    2
  );
}

async function sendTeleop(linear_x, angular_z, { quiet = true } = {}) {
  const robotId = teleopRobotIdInput.value.trim();
  if (!robotId) {
    updateTeleopStatus(linear_x, angular_z, "empty robot id");
    return;
  }
  if (lastTeleop.robotId === robotId && lastTeleop.linear_x === linear_x && lastTeleop.angular_z === angular_z) {
    updateTeleopStatus(linear_x, angular_z);
    return;
  }

  const data = await api(`/teleop/${encodeURIComponent(robotId)}`, {
    method: "POST",
    body: JSON.stringify({ linear_x, angular_z, ttl_ms: 300 }),
  });
  lastTeleop = { robotId, linear_x, angular_z };
  updateTeleopStatus(linear_x, angular_z);
  if (!quiet) print(data);
}

function startTeleopLoop() {
  if (teleopTimer) return;
  teleopTimer = setInterval(() => {
    if (!activeKeys.size) return;
    const v = computeTeleopVector();
    sendTeleop(v.linear_x, v.angular_z, { quiet: true }).catch((err) => updateTeleopStatus(v.linear_x, v.angular_z, err));
  }, 130);
}

function stopTeleopLoop() {
  if (teleopTimer) {
    clearInterval(teleopTimer);
    teleopTimer = null;
  }
}

document.addEventListener("keydown", (ev) => {
  if (!shouldHandleTeleop(ev)) return;
  ev.preventDefault();
  const key = normalizeKey(ev.key);
  activeKeys.add(key);
  startTeleopLoop();
  const v = computeTeleopVector();
  sendTeleop(v.linear_x, v.angular_z, { quiet: true }).catch((err) => updateTeleopStatus(v.linear_x, v.angular_z, err));
});

document.addEventListener("keyup", (ev) => {
  if (!shouldHandleTeleop(ev)) return;
  ev.preventDefault();
  const key = normalizeKey(ev.key);
  activeKeys.delete(key);
  const v = computeTeleopVector();
  if (!activeKeys.size) {
    stopTeleopLoop();
  }
  sendTeleop(v.linear_x, v.angular_z, { quiet: true }).catch((err) => updateTeleopStatus(v.linear_x, v.angular_z, err));
});

document.getElementById("refreshBtn").onclick = async () => {
  try {
    await refreshRobots();
    await refreshFormation({ quiet: true });
  } catch (err) {
    print({ error: String(err) });
  }
};

networkMetricSelect.onchange = drawNetworkChart;

if (viewControlBtn) {
  viewControlBtn.onclick = () => setView("control");
}

if (viewDiagnosticsBtn) {
  viewDiagnosticsBtn.onclick = () => setView("diagnostics");
}

if (diagSnapshotBtn) {
  diagSnapshotBtn.onclick = async () => {
    try {
      await refreshDiagnosticsSnapshot();
    } catch (err) {
      setDiagStatus(`Diagnostics refresh failed: ${String(err)}`);
      print({ error: String(err) });
    }
  };
}

if (diagStressBtn) {
  diagStressBtn.onclick = () => {
    setView("diagnostics");
    startStressTest();
  };
}

if (diagStopBtn) {
  diagStopBtn.onclick = () => {
    stopStressTest({ byTimeout: false });
  };
}

document.getElementById("applyApiBaseBtn").onclick = async () => {
  try {
    const next = apiBaseInput.value.trim();
    if (!next) {
      throw new Error("API endpoint is empty");
    }
    setApiBase(next);
    await refreshRobots();
    await refreshFormation({ quiet: true });
    await refreshDiagnosticsSnapshot({ quiet: true });
  } catch (err) {
    print({ error: String(err) });
  }
};

document.getElementById("teleopStopBtn").onclick = async () => {
  try {
    activeKeys = new Set();
    stopTeleopLoop();
    await sendTeleop(0, 0, { quiet: false });
  } catch (err) {
    print({ error: String(err) });
  }
};

document.getElementById("startFollowBtn").onclick = async () => {
  try {
    const leader_id = leaderRobotIdInput.value.trim();
    const follower_ids = parseRobotIds(followerRobotIdsInput.value);
    const data = await api("/formation/follow/start", {
      method: "POST",
      body: JSON.stringify({ leader_id, follower_ids }),
    });
    teleopRobotIdInput.value = leader_id;
    formationStatus.textContent = JSON.stringify(data.formation, null, 2);
    print(data);
  } catch (err) {
    print({ error: String(err) });
  }
};

document.getElementById("stopFollowBtn").onclick = async () => {
  try {
    const data = await api("/formation/follow/stop", { method: "POST" });
    formationStatus.textContent = JSON.stringify(data.formation, null, 2);
    print(data);
  } catch (err) {
    print({ error: String(err) });
  }
};

if (alertList) {
  alertList.addEventListener("click", async (ev) => {
    const btn = ev.target.closest("[data-alert-id]");
    if (!btn) return;
    const alertId = btn.getAttribute("data-alert-id");
    if (!alertId) return;
    try {
      await api(`/alerts/${encodeURIComponent(alertId)}/ack`, {
        method: "POST",
        body: JSON.stringify({ status: "acknowledged" }),
      });
      await refreshRobots({ quiet: true });
    } catch (err) {
      print({ error: String(err) });
    }
  });
}

document.getElementById("sendCmdBtn").onclick = async () => {
  try {
    const robotId = robotIdInput.value.trim();
    const type = document.getElementById("commandType").value.trim();
    const args = JSON.parse(document.getElementById("commandArgs").value || "{}");
    const data = await api(`/robots/${encodeURIComponent(robotId)}/command`, {
      method: "POST",
      body: JSON.stringify({ type, args, ttl_ms: 2000 }),
    });
    print(data);
  } catch (err) {
    print({ error: String(err) });
  }
};

document.getElementById("startMissionBtn").onclick = async () => {
  try {
    const missionId = document.getElementById("missionId").value.trim();
    const robotIds = parseRobotIds(document.getElementById("missionRobots").value);
    const payload = {
      mission_id: missionId,
      robot_ids: robotIds,
      zone: { crs: "local", polygon: [[0, 0], [12, 0], [12, 6], [0, 6]] },
      return_point: { x: 0.5, y: 0.5 },
      strategy: { pattern: "lawnmower", lane_width_m: 0.8, speed_mps: 0.4 },
    };
    const data = await api("/missions/start", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    print(data);
  } catch (err) {
    print({ error: String(err) });
  }
};

document.getElementById("returnMissionBtn").onclick = async () => {
  try {
    const missionId = document.getElementById("missionId").value.trim();
    const data = await api(`/missions/${encodeURIComponent(missionId)}/return`, { method: "POST" });
    print(data);
  } catch (err) {
    print({ error: String(err) });
  }
};

document.getElementById("stopMissionBtn").onclick = async () => {
  try {
    const missionId = document.getElementById("missionId").value.trim();
    const data = await api(`/missions/${encodeURIComponent(missionId)}/stop`, { method: "POST" });
    print(data);
  } catch (err) {
    print({ error: String(err) });
  }
};

autoRefreshToggle.onchange = () => {
  resetAutoRefresh();
  resetDiagnosticsAutoRefresh();
};

window.addEventListener("resize", () => {
  drawNetworkChart();
  drawDiagChart();
});

applyNeoTheme();
setDiagStatus("Idle.");
setView(localStorage.getItem("autofleet_view") || "control");
refreshRobots()
  .then(() => refreshDiagnosticsSnapshot({ quiet: true }))
  .catch((err) => print({ error: String(err) }));
refreshFormation({ quiet: true }).catch((err) => print({ error: String(err) }));
