const defaultHost = window.location.hostname || "127.0.0.1";
const defaultProto = window.location.protocol === "https:" ? "https:" : "http:";
const defaultApiBase = `${defaultProto}//${defaultHost}:8000/api/v1`;
let apiBase = localStorage.getItem("autofleet_api_base") || defaultApiBase;

const output = document.getElementById("output");
const robotTable = document.getElementById("robotTable");
const videoWall = document.getElementById("videoWall");
const formationStatus = document.getElementById("formationStatus");
const teleopStatus = document.getElementById("teleopStatus");
const apiBaseInput = document.getElementById("apiBase");
const autoRefreshToggle = document.getElementById("autoRefreshToggle");
const themeSelect = document.getElementById("themeSelect");

const networkChart = document.getElementById("networkChart");
const networkLegend = document.getElementById("networkLegend");
const networkSummary = document.getElementById("networkSummary");
const networkMetricSelect = document.getElementById("networkMetricSelect");
const MAX_NETWORK_POINTS = 120;

const robotIdInput = document.getElementById("robotId");
const teleopRobotIdInput = document.getElementById("teleopRobotId");
const leaderRobotIdInput = document.getElementById("leaderRobotId");
const followerRobotIdsInput = document.getElementById("followerRobotIds");

apiBaseInput.value = apiBase;

let robotsCache = [];
let refreshTimer = null;
let teleopTimer = null;
let activeKeys = new Set();
let lastTeleop = { robotId: "", linear_x: 0, angular_z: 0 };
const networkHistory = new Map();

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

function setApiBase(nextValue) {
  apiBase = nextValue.replace(/\/+$/, "");
  localStorage.setItem("autofleet_api_base", apiBase);
}

function applyTheme(themeName) {
  const theme = themeName === "cyberpunk" ? "cyberpunk" : "neo";
  document.body.classList.toggle("theme-cyberpunk", theme === "cyberpunk");
  document.body.classList.toggle("theme-neo", theme === "neo");
  localStorage.setItem("autofleet_theme", theme);
  if (themeSelect) themeSelect.value = theme;
  drawNetworkChart();
}

async function api(path, options = {}) {
  const res = await fetch(`${apiBase}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const body = await res.json();
  if (!res.ok) throw new Error(body.detail || "API error");
  return body;
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
      <td>${escapeHtml(robot.video_rtsp_url ?? "-")}</td>
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
      const rtsp = robot.video_rtsp_url || "";
      const streamHtml = buildStreamView(rtsp, robot.robot_id);
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
        </div>
      </article>`;
    })
    .join("");
}

function buildStreamView(streamUrl, robotId) {
  if (!streamUrl) {
    return "No stream URL in telemetry.";
  }
  const lowered = streamUrl.toLowerCase();
  const escaped = escapeHtml(streamUrl);
  if (lowered.startsWith("http://") || lowered.startsWith("https://")) {
    return `<video controls muted autoplay playsinline src="${escaped}"></video>`;
  }
  if (lowered.startsWith("rtsp://")) {
    return `RTSP stream for ${escapeHtml(robotId)}<br><a class="rtsp-link" href="${escaped}" target="_blank" rel="noopener">Open ${escaped}</a><br>Browser usually cannot play RTSP directly.`;
  }
  return `Unsupported stream URL: ${escaped}`;
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
  const isCyber = document.body.classList.contains("theme-cyberpunk");
  const bg = isCyber ? "#090a14" : "#111111";
  const grid = isCyber ? "#3e2a67" : "#2b2b2b";
  const axis = isCyber ? "#ff4fc4" : "#ffffff";
  const label = isCyber ? "#f5e9ff" : "#f7f7f7";

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

async function refreshRobots({ quiet = false } = {}) {
  const data = await api("/robots");
  robotsCache = data.items || [];
  updateNetworkHistory(robotsCache);
  renderRobotTable(robotsCache);
  renderVideoWall(robotsCache);
  renderNetworkSummary(robotsCache);
  renderNetworkLegend(robotsCache);
  drawNetworkChart();
  syncDefaultRobotIds(robotsCache);
  if (!quiet) print(data);
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
  if (autoRefreshToggle.checked) {
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

autoRefreshToggle.onchange = resetAutoRefresh;
networkMetricSelect.onchange = drawNetworkChart;
if (themeSelect) {
  themeSelect.onchange = (ev) => applyTheme(ev.target.value);
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

window.addEventListener("resize", () => drawNetworkChart());

applyTheme(localStorage.getItem("autofleet_theme") || "neo");
resetAutoRefresh();
refreshRobots().catch((err) => print({ error: String(err) }));
refreshFormation({ quiet: true }).catch((err) => print({ error: String(err) }));
