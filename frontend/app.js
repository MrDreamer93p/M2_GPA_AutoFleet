const defaultHost = window.location.hostname || "127.0.0.1";
const defaultProto = window.location.protocol === "https:" ? "https:" : "http:";
const defaultApiHosts = Array.from(new Set([defaultHost, "127.0.0.1", "localhost"].filter(Boolean)));
const defaultApiPorts = [8200, 8201, 8000, 8001];
const defaultApiBases = defaultApiHosts.flatMap((host) => defaultApiPorts.map((port) => `${defaultProto}//${host}:${port}/api/v1`));
let apiBase = `${defaultProto}//${defaultHost}:8200/api/v1`;
let apiBasePinned = false;
let availableApiBases = [];

const output = document.getElementById("output");
const robotTable = document.getElementById("robotTable");
const videoWall = document.getElementById("videoWall");
const kinectStage = document.getElementById("kinectStage");
const kinectStatus = document.getElementById("kinectStatus");
const fleetOverview = document.getElementById("fleetOverview");
const riskMap = document.getElementById("riskMap");
const mapSummaryGrid = document.getElementById("mapSummaryGrid");
const alertList = document.getElementById("alertList");
const protocolSummary = document.getElementById("protocolSummary");
const protocolOutput = document.getElementById("protocolOutput");
const formationStatus = document.getElementById("formationStatus");
const teleopStatus = document.getElementById("teleopStatus");
const apiBaseInput = document.getElementById("apiBase");
const apiBaseCustomInput = document.getElementById("apiBaseCustom");
const applyApiBaseBtn = document.getElementById("applyApiBaseBtn");
const detectApiBtn = document.getElementById("detectApiBtn");
const apiEndpointStatus = document.getElementById("apiEndpointStatus");
const autoRefreshToggle = document.getElementById("autoRefreshToggle");

const networkChart = document.getElementById("networkChart");
const networkLegend = document.getElementById("networkLegend");
const networkSummary = document.getElementById("networkSummary");
const networkMetricSelect = document.getElementById("networkMetricSelect");
const videoQualitySelect = document.getElementById("videoQualitySelect");
const applyVideoQualityBtn = document.getElementById("applyVideoQualityBtn");
const videoQualityStatus = document.getElementById("videoQualityStatus");
const MAX_NETWORK_POINTS = 120;
const MAX_DIAG_POINTS = 240;
const PREFERRED_KINECT_STREAM_KEYS = ["color", "rgb", "rgb_color", "depth", "distance", "infrared", "ir", "body_index", "skeleton", "body", "pose", "ir2", "default"];
const PREFERRED_VEHICLE_STREAM_KEYS = ["color", "rgb", "rgb_color", "default", "front", "camera", "depth", "pose"];

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

let robotsCache = [];
let alertsCache = [];
let healthCache = null;
let kinectBridgeHealthCache = null;
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
let videoWallRenderKey = "";
let kinectStageRenderKey = "";
let videoSettingsCache = null;
let selectedStreamsByRobot = {};
let selectedKinectRobotId = localStorage.getItem("autofleet_kinect_robot") || "";
let selectedKinectStream = localStorage.getItem("autofleet_kinect_stream") || "";
let kinectFullscreenOverlay = null;
const stressState = {
  running: false,
  startedAt: 0,
  stopAt: 0,
  simulated_capacity_mb_s: 0,
  vehicle_count: 3,
  samples: [],
  lastError: null,
};

function isDefaultApiBase(value) {
  const normalized = (value || "").replace(/\/+$/, "");
  return defaultApiBases.includes(normalized);
}

function getStoredApiBase() {
  return (localStorage.getItem("autofleet_api_base") || "").replace(/\/+$/, "");
}

function normalizeApiBaseInput(value) {
  let raw = String(value || "").trim();
  if (!raw) return "";
  if (!/^https?:\/\//i.test(raw)) {
    raw = `${defaultProto}//${raw}`;
  }
  try {
    const url = new URL(raw);
    url.pathname = url.pathname.replace(/\/+$/, "");
    if (!url.pathname || url.pathname === "/") {
      url.pathname = "/api/v1";
    } else if (!url.pathname.endsWith("/api/v1")) {
      url.pathname = `${url.pathname}/api/v1`.replace(/\/+/g, "/");
    }
    url.search = "";
    url.hash = "";
    return url.toString().replace(/\/+$/, "");
  } catch {
    return raw.replace(/\/+$/, "");
  }
}

function syncApiBaseInputs(nextValue) {
  if (apiBaseInput) apiBaseInput.value = nextValue;
  if (apiBaseCustomInput) apiBaseCustomInput.value = nextValue;
}

function describeApiBase(base) {
  try {
    const url = new URL(base);
    const port = url.port || (url.protocol === "https:" ? "443" : "80");
    const kind = port === "8201" || port === "8200" ? "Local Backend" : "Legacy Backend";
    return `${kind} (${url.hostname}:${port})`;
  } catch {
    return base;
  }
}

function apiRouteKey(base) {
  try {
    const url = new URL(base);
    const port = url.port || (url.protocol === "https:" ? "443" : "80");
    return `${url.protocol}//:${port}${url.pathname.replace(/\/+$/, "")}`;
  } catch {
    return String(base || "").replace(/\/+$/, "");
  }
}

function apiHostScore(base) {
  try {
    const host = new URL(base).hostname.toLowerCase();
    if (host === defaultHost.toLowerCase()) return 0;
    if (host === "localhost") return 1;
    if (host === "127.0.0.1") return 2;
    return 3;
  } catch {
    return 9;
  }
}

function setEndpointStatus(text, state = "idle") {
  if (!apiEndpointStatus) return;
  apiEndpointStatus.textContent = text;
  apiEndpointStatus.dataset.state = state;
}

function populateApiBasePresets() {
  if (!apiBaseInput) return;
  while (apiBaseInput.firstChild) {
    apiBaseInput.removeChild(apiBaseInput.firstChild);
  }

  const bases = uniqueApiBasesByRoute([apiBase, normalizeApiBaseInput(getStoredApiBase()), ...availableApiBases, ...defaultApiBases]);
  if (!bases.length) {
    bases.push(defaultApiBases[0]);
  }
  for (const base of bases) {
    const opt = document.createElement("option");
    opt.value = base;
    opt.textContent = availableApiBases.includes(base) ? `Online | ${describeApiBase(base)}` : `Candidate | ${describeApiBase(base)}`;
    apiBaseInput.appendChild(opt);
  }

  if (!bases.includes(apiBase) && bases.length) {
    apiBase = bases[0];
  }
  syncApiBaseInputs(apiBase);
}

function initApiBase() {
  const saved = normalizeApiBaseInput(getStoredApiBase());
  if (isDefaultApiBase(saved)) {
    apiBase = defaultApiBases[0];
    localStorage.setItem("autofleet_api_base", apiBase);
    apiBasePinned = false;
  } else if (saved) {
    apiBase = saved;
    apiBasePinned = true;
  } else {
    apiBase = defaultApiBases[0];
    apiBasePinned = false;
  }
  populateApiBasePresets();
  syncApiBaseInputs(apiBase);
}

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

function normalizeStreamKey(value) {
  return String(value || "")
    .trim()
    .toLowerCase()
    .replace(/[\s\-]+/g, "_");
}

function streamDisplayName(streamKey) {
  const key = normalizeStreamKey(streamKey);
  if (key === "rgb") return "Color (RGB)";
  if (key === "rgb_color") return "Color";
  if (key === "body") return "Body";
  if (key === "body_index") return "Body Index";
  if (key === "ir2") return "IR2";
  if (key === "infrared") return "Infrared";
  if (key === "kinect") return "Kinect Depth";
  if (key === "distance") return "Distance";
  if (key === "pose") return "Pose / Skeleton";
  if (key === "skeleton") return "Skeleton";
  if (key === "depth") return "Depth";
  if (key === "default") return "Default";
  return key || "Stream";
}

function getRobotStreamMap(robot) {
  const status = robot.video_status || {};
  const statusMap = status.source_map;
  const telemetryMap = robot.video_streams;
  const rawMap =
    statusMap && typeof statusMap === "object"
      ? { ...statusMap }
      : telemetryMap && typeof telemetryMap === "object"
      ? { ...telemetryMap }
      : {};
  const normalized = {};
  for (const [rawKey, url] of Object.entries(rawMap)) {
    const key = normalizeStreamKey(rawKey);
    if (!key || !url) continue;
    if (!(key in normalized)) normalized[key] = String(url);
  }
  if (!Object.keys(normalized).length) {
    const fallback = status.source_url || robot.video_rtsp_url;
    if (fallback) normalized.default = String(fallback);
  }
  return normalized;
}

function getRobotAvailableStreams(robot) {
  const status = robot.video_status || {};
  const statusList = Array.isArray(status.available_streams) ? status.available_streams : [];
  const streamMap = getRobotStreamMap(robot);
  const seen = new Set();
  const keys = [];
  for (const stream of statusList) {
    const key = normalizeStreamKey(stream);
    if (!key || seen.has(key)) continue;
    seen.add(key);
    keys.push(key);
  }
  for (const rawKey of Object.keys(streamMap)) {
    const key = normalizeStreamKey(rawKey);
    if (!key || seen.has(key)) continue;
    seen.add(key);
    keys.push(key);
  }
  if (!keys.length && (status.source_url || robot.video_rtsp_url)) {
    keys.push("default");
  }
  if (!keys.length) {
    keys.push("default");
  }
  return keys;
}

function preferredStreamForRobot(robot, preferredKeys = PREFERRED_VEHICLE_STREAM_KEYS) {
  const available = getRobotAvailableStreams(robot);
  for (const preferred of preferredKeys) {
    const normalized = normalizeStreamKey(preferred);
    if (available.includes(normalized)) return normalized;
  }
  return available[0] || "default";
}

function getRobotSelectedStream(robot, preferredKeys = PREFERRED_VEHICLE_STREAM_KEYS) {
  const robotId = String(robot.robot_id || "").trim();
  const available = getRobotAvailableStreams(robot);
  const configured = normalizeStreamKey(selectedStreamsByRobot[robotId]);
  const fallback = preferredStreamForRobot(robot, preferredKeys);
  if (configured && available.includes(configured)) return configured;
  selectedStreamsByRobot[robotId] = fallback;
  return fallback;
}

function streamSelectorHtml(robot, options, className = "video-stream-select") {
  if (!options.length || options.length <= 1) return "";
  const selected = className === "kinect-stream-select"
    ? getKinectSelectedStream(robot)
    : getRobotSelectedStream(robot, PREFERRED_VEHICLE_STREAM_KEYS);
  const robotId = escapeHtml(robot.robot_id || "");
  const optionHtml = options
    .map((rawKey) => {
      const key = normalizeStreamKey(rawKey);
      const isSelected = key === selected ? " selected" : "";
      return `<option value="${escapeHtml(key)}"${isSelected}>${escapeHtml(streamDisplayName(key))}</option>`;
    })
    .join("");
  return `
    <label class="stream-switch" data-robot-id="${robotId}">
      <span>Stream</span>
      <select class="${escapeHtml(className)}" data-robot-id="${robotId}">
        ${optionHtml}
      </select>
    </label>
  `;
}

function robotHasKinectFocusStream(robot) {
  const robotHint = String(robot.robot_id || "").toLowerCase();
  const streamList = getRobotAvailableStreams(robot);
  return /kinect/.test(robotHint) || streamList.some((stream) => /pose|skeleton|body|body_index|depth|ir|infrared/.test(stream));
}

function isKinectRobot(robot) {
  const robotId = String(robot.robot_id || "").toLowerCase();
  const status = robot.video_status || {};
  const sourceText = [
    status.source_url,
    status.proxy_url,
    status.snapshot_url,
    robot.video_rtsp_url,
    ...Object.values(robot.video_streams || {}),
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  const sensors = Array.isArray(robot.sensor_summary?.sensors) ? robot.sensor_summary.sensors : [];
  const hasOnlineKinectSensor = sensors.some(
    (sensor) => String(sensor.sensor_type || "").toLowerCase() === "kinect" && String(sensor.status || "").toLowerCase() === "online"
  );
  return robotId.includes("kinect") || sourceText.includes("kinect") || hasOnlineKinectSensor;
}

function getKinectSelectedStream(robot) {
  const robotId = String(robot.robot_id || "").trim();
  const available = getRobotAvailableStreams(robot);
  const configured = robotId === selectedKinectRobotId ? normalizeStreamKey(selectedKinectStream) : "";
  const fallback = preferredStreamForRobot(robot, PREFERRED_KINECT_STREAM_KEYS);
  const next = configured && available.includes(configured) ? configured : fallback;
  selectedKinectRobotId = robotId;
  selectedKinectStream = next;
  localStorage.setItem("autofleet_kinect_robot", selectedKinectRobotId);
  localStorage.setItem("autofleet_kinect_stream", selectedKinectStream);
  return next;
}

function kinectRobotSelectorHtml(kinectRobots) {
  if (kinectRobots.length <= 1) return "";
  const selectedId = selectedKinectRobotId || kinectRobots[0]?.robot_id || "";
  const options = kinectRobots
    .map((robot) => {
      const id = String(robot.robot_id || "");
      const selected = id === selectedId ? " selected" : "";
      return `<option value="${escapeHtml(id)}"${selected}>${escapeHtml(id)}</option>`;
    })
    .join("");
  return `
    <label class="stream-switch">
      <span>Kinect</span>
      <select class="kinect-robot-select">
        ${options}
      </select>
    </label>
  `;
}

function withStreamQuery(url, streamKey) {
  if (!url) return "";
  const normalized = normalizeStreamKey(streamKey);
  if (!normalized) return url;
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}stream=${encodeURIComponent(normalized)}`;
}


function resolveStreamUrl(rawUrl) {
  const trimmed = String(rawUrl || "").trim();
  if (!trimmed) return "";
  if (/^https?:\/\//i.test(trimmed)) return trimmed;
  if (!trimmed.startsWith("/")) return trimmed;
  try {
    const origin = new URL(apiBase).origin;
    return `${origin}${trimmed}`;
  } catch {
    return trimmed.startsWith("/") ? trimmed : `/${trimmed}`;
  }
}

function apiRelativeUrl(path) {
  try {
    return `${new URL(apiBase).origin}${path}`;
  } catch {
    return path;
  }
}

function backendVideoStreamUrl(robot, selectedStream) {
  const robotId = encodeURIComponent(String(robot.robot_id || ""));
  const streamKey = normalizeStreamKey(selectedStream);
  const query = streamKey ? `?stream=${encodeURIComponent(streamKey)}` : "";
  return apiRelativeUrl(`/api/v1/video/proxy/${robotId}.mjpeg${query}`);
}

function kinectLiveStreamUrl(robot, selectedStream) {
  const streamKey = normalizeStreamKey(selectedStream);
  const direct = getRobotStreamMap(robot)[streamKey] || robot.video_rtsp_url || "";
  if (/^https?:\/\//i.test(direct)) return direct;
  return `http://127.0.0.1:8450/streams/${encodeURIComponent(streamKey || "color")}.mjpeg`;
}

function backendSnapshotUrl(snapshotUrl) {
  const raw = String(snapshotUrl || "").trim();
  if (!raw) return "";
  let name = "";
  try {
    const parsed = new URL(raw, window.location.href);
    const parts = parsed.pathname.split("/").filter(Boolean);
    name = parts[parts.length - 1] || "";
  } catch {
    const parts = raw.split("?")[0].split("/").filter(Boolean);
    name = parts[parts.length - 1] || "";
  }
  return name ? apiRelativeUrl(`/api/v1/video/snapshots/${encodeURIComponent(name)}`) : resolveStreamUrl(raw);
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

function bytesToKbS(bytes, durationMs) {
  if (!Number.isFinite(bytes) || !Number.isFinite(durationMs) || durationMs <= 0) return Number.NaN;
  return bytes / (durationMs / 1000) / 1024;
}

function kbpsToKbS(kbps) {
  const n = Number(kbps);
  return Number.isFinite(n) ? n / 8.192 : Number.NaN;
}

function formatRateKbS(value, digits = 1) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "-";
  if (Math.abs(n) >= 1024) return `${(n / 1024).toFixed(digits)} MB/s`;
  return `${n.toFixed(digits)} KB/s`;
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

function setApiBase(nextValue, { pinned = true } = {}) {
  apiBase = normalizeApiBaseInput(nextValue);
  apiBasePinned = pinned;
  localStorage.setItem("autofleet_api_base", apiBase);
  populateApiBasePresets();
  syncApiBaseInputs(apiBase);
}

function uniqueApiBases(values) {
  return Array.from(new Set(values.map((value) => String(value || "").replace(/\/+$/, "")).filter(Boolean)));
}

function uniqueApiBasesByRoute(values) {
  const best = new Map();
  for (const value of uniqueApiBases(values)) {
    const key = apiRouteKey(value);
    const existing = best.get(key);
    if (!existing || apiHostScore(value) < apiHostScore(existing)) {
      best.set(key, value);
    }
  }
  return Array.from(best.values());
}

async function probeApiBase(candidate) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 1600);
  const started = performance.now();
  try {
    const res = await fetch(`${candidate}/health`, {
      method: "GET",
      cache: "no-store",
      signal: controller.signal,
      headers: { Accept: "application/json" },
    });
    const body = await res.json().catch(() => ({}));
    return {
      base: candidate,
      ok: res.ok && body?.status === "ok",
      latency_ms: Math.round(performance.now() - started),
      body,
    };
  } catch (err) {
    return { base: candidate, ok: false, error: String(err) };
  } finally {
    clearTimeout(timeout);
  }
}

async function detectApiEndpoints() {
  const candidates = uniqueApiBasesByRoute([apiBase, ...defaultApiBases, normalizeApiBaseInput(getStoredApiBase())]);
  setEndpointStatus(`Detecting ${candidates.length} API endpoints...`, "detecting");
  populateApiBasePresets();

  const results = await Promise.all(candidates.map((candidate) => probeApiBase(candidate)));
  const onlineByRoute = new Map();
  for (const result of results.filter((item) => item.ok)) {
    const key = apiRouteKey(result.base);
    const existing = onlineByRoute.get(key);
    const resultScore = apiHostScore(result.base);
    const existingScore = existing ? apiHostScore(existing.base) : Number.POSITIVE_INFINITY;
    if (!existing || resultScore < existingScore || (resultScore === existingScore && result.latency_ms < existing.latency_ms)) {
      onlineByRoute.set(key, result);
    }
  }
  const online = Array.from(onlineByRoute.values()).sort((a, b) => a.latency_ms - b.latency_ms);
  availableApiBases = online.map((result) => result.base);

  if (availableApiBases.length) {
    const saved = getStoredApiBase();
    const preferred = availableApiBases.includes(saved) ? saved : availableApiBases[0];
    setApiBase(preferred, { pinned: true });
    const current = online.find((result) => result.base === preferred);
    setEndpointStatus(`Using ${describeApiBase(preferred)} | ${current?.latency_ms ?? "-"}ms`, "online");
    return;
  }

  populateApiBasePresets();
  setEndpointStatus("No healthy API detected. Use a candidate or enter a custom endpoint.", "offline");
}

async function fetchWithApiFallback(path, options = {}) {
  const candidates = apiBasePinned ? [apiBase] : uniqueApiBasesByRoute([apiBase, ...availableApiBases, ...defaultApiBases]);
  let lastError = null;
  for (const candidate of candidates) {
    try {
      const res = await fetch(`${candidate}${path}`, options);
      if (candidate !== apiBase) {
        setApiBase(candidate, { pinned: false });
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

function renderVideoSettings(settings) {
  videoSettingsCache = settings;
  const current = settings?.current || {};
  if (videoQualitySelect && current.preset) {
    videoQualitySelect.value = current.preset;
  }
  if (videoQualityStatus) {
    const width = current.max_width || "-";
    const q = current.jpeg_quality || "-";
    const interval = current.mjpeg_interval_ms || "-";
    videoQualityStatus.textContent = `Video ${current.preset || "balanced"} | ${width}px | Q${q} | ${interval}ms`;
  }
}

async function loadVideoSettings() {
  if (!videoQualitySelect) return;
  try {
    const settings = await api("/video/settings");
    renderVideoSettings(settings);
  } catch (err) {
    if (videoQualityStatus) videoQualityStatus.textContent = `Video settings unavailable`;
  }
}

async function applyVideoQuality() {
  const preset = videoQualitySelect?.value || "balanced";
  if (videoQualityStatus) videoQualityStatus.textContent = `Applying ${preset}...`;
  const settings = await api("/video/settings", {
    method: "POST",
    body: JSON.stringify({ preset }),
  });
  renderVideoSettings(settings);
  videoWallRenderKey = "";
  await refreshAll({ quiet: true });
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
    throughput_kb_s: bytesToKbS(bytes, ended - started),
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
  const values = finiteValues(series.map((x) => x.throughput_kb_s));
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
      <td>${escapeHtml(formatRateKbS(sample?.throughput_kb_s))}</td>
      <td>${escapeHtml(fmtNum(sample?.control_rtt_ms, 1))}ms</td>
      <td>${escapeHtml(robot.video_status?.proxy_url || robot.video_rtsp_url || "-")}</td>
    `;
    robotTable.appendChild(tr);
  }
}

async function refreshKinectBridgeHealth() {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 900);
  try {
    const res = await fetch("http://127.0.0.1:8450/health", {
      method: "GET",
      cache: "no-store",
      signal: controller.signal,
      headers: { Accept: "application/json" },
    });
    kinectBridgeHealthCache = res.ok ? await res.json() : { status: "offline", note: `HTTP ${res.status}` };
  } catch (err) {
    kinectBridgeHealthCache = { status: "offline", note: String(err) };
  } finally {
    clearTimeout(timeout);
  }
  return kinectBridgeHealthCache;
}

function renderKinectStage(items) {
  const kinectRobots = items.filter(isKinectRobot);
  if (!kinectRobots.length) {
    kinectStageRenderKey = "";
    if (kinectStatus) kinectStatus.textContent = "Kinect not registered";
    kinectStage.innerHTML = `
      <div class="kinect-empty">
        <strong>No Windows Kinect stream in AutoFleet yet.</strong>
        <span>Device is detected by Windows, but no Kinect bridge has published color/depth/body streams.</span>
      </div>
    `;
    return;
  }

  let robot = kinectRobots.find((item) => String(item.robot_id || "") === selectedKinectRobotId) || kinectRobots[0];
  selectedKinectRobotId = String(robot.robot_id || "");
  const streamChoices = getRobotAvailableStreams(robot);
  const streamState = robot.video_status?.status || "offline";
  const bridgeFrames = Number(kinectBridgeHealthCache?.frames || 0);
  const bridgeSensorAvailable = kinectBridgeHealthCache?.diagnostics?.sensor_available;
  const bridgeNote = kinectBridgeHealthCache?.note || "";
  const bridgeState =
    kinectBridgeHealthCache?.status === "offline"
      ? "bridge offline"
      : bridgeSensorAvailable === false
      ? "sensor unavailable"
      : bridgeFrames > 0
      ? `frames ${bridgeFrames}`
      : "waiting frames";
  if (kinectStatus) {
    kinectStatus.textContent = `${selectedKinectRobotId || "Kinect"} | ${streamChoices.length} channels | ${streamState} | ${bridgeState}`;
  }
  const selectorHtml = kinectRobotSelectorHtml(kinectRobots);
  const channelHtml = streamChoices
    .map((streamKey) => {
      const normalized = normalizeStreamKey(streamKey);
      return `
        <article class="kinect-channel" data-stream="${escapeHtml(normalized)}" title="Double-click to expand">
          <div class="kinect-channel-head">
            <strong>${escapeHtml(streamDisplayName(normalized))}</strong>
            <span class="flag-pill ${severityClass(streamState === "online" ? "ok" : "warning")}">${escapeHtml(streamState)}</span>
          </div>
          <div class="kinect-channel-view">${buildStreamView(robot, normalized)}</div>
        </article>
      `;
    })
    .join("");
  const renderKey = [
    selectedKinectRobotId,
    streamState,
    robot.video_status?.proxy_url || "",
    robot.video_status?.snapshot_url || "",
    streamChoices.join(","),
    bridgeSensorAvailable,
  ].join("|");
  if (renderKey === kinectStageRenderKey && kinectStage.querySelector(".kinect-live")) {
    return;
  }
  kinectStageRenderKey = renderKey;
  kinectStage.innerHTML = `
    <article class="kinect-live" data-robot-id="${escapeHtml(selectedKinectRobotId)}">
      <div class="kinect-live-head">
        <strong>${escapeHtml(selectedKinectRobotId || "Kinect")}</strong>
        <div class="video-head-actions">${selectorHtml}</div>
      </div>
      <div class="kinect-multiview">${channelHtml}</div>
      <div class="kinect-meta">
        <span>${escapeHtml(robot.video_status?.note || "Waiting for Kinect frame status.")}</span>
        <span>${escapeHtml(`bridge: ${bridgeState}${bridgeNote ? ` | ${bridgeNote}` : ""}`)}</span>
        <span>${escapeHtml(robot.video_status?.proxy_url || robot.video_status?.source_url || robot.video_rtsp_url || "-")}</span>
      </div>
    </article>
  `;
}

function renderVideoWall(items) {
  const vehicleItems = items.filter((robot) => !isKinectRobot(robot));
  if (!vehicleItems.length) {
    videoWallRenderKey = "";
    videoWall.innerHTML = `<div class="video-empty">No connected vehicle robots yet. Publish Raspberry Pi telemetry to see vehicle streams.</div>`;
    return;
  }

  const nextRenderKey = vehicleItems
    .map((robot) =>
      [
        robot.robot_id,
        robot.state,
        getRobotSelectedStream(robot, PREFERRED_VEHICLE_STREAM_KEYS),
        getRobotAvailableStreams(robot).join(","),
        robot.video_status?.status || "offline",
        robot.video_status?.proxy_url || "",
        robot.video_status?.snapshot_url || "",
        robot.video_rtsp_url || "",
      ].join("|")
    )
    .join(";");
  if (nextRenderKey === videoWallRenderKey && videoWall.querySelector(".mjpeg-stream, .snapshot-thumb, .video-card")) {
    return;
  }
  videoWallRenderKey = nextRenderKey;

  videoWall.innerHTML = vehicleItems
    .map((robot) => {
      const pose = robot.pose || {};
      const controls = robot.controls || {};
      const motors = robot.motors || {};
      const ack = robot.latest_ack || {};
      const sample = getLatestSample(robot.robot_id) || {};
      const selectedStream = getRobotSelectedStream(robot, PREFERRED_VEHICLE_STREAM_KEYS);
      const streamChoices = getRobotAvailableStreams(robot);
      const streamHtml = buildStreamView(robot, selectedStream);
      const streamSwitcher = streamSelectorHtml(robot, streamChoices, "video-stream-select");
      const detections = robot.latest_perception?.detections || [];
      const sensorSummary = formatSensorSummary(robot.sensor_summary);
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
      <article class="video-card" data-robot-id="${escapeHtml(
        robot.robot_id
      )}">
        <div class="video-card-head">
          <strong>${escapeHtml(robot.robot_id)}</strong>
          <div class="video-head-actions">
            <span class="${robot.online ? "online" : "offline"}">${escapeHtml(robot.state ?? "UNKNOWN")}</span>
            ${streamSwitcher}
          </div>
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
          <div class="meta-item"><span class="meta-key">Throughput / RTT</span>${escapeHtml(`${formatRateKbS(sample.throughput_kb_s)} / ${fmtNum(sample.control_rtt_ms, 1)}ms`)}</div>
          <div class="meta-item"><span class="meta-key">View Profile</span>${escapeHtml(robot.video_status?.view_profile || robot.video_view_profile || "-")}</div>
          <div class="meta-item"><span class="meta-key">Perception</span>${escapeHtml(detections.length ? detections.map((d) => d.label).join(", ") : "no detection")}</div>
          <div class="meta-item"><span class="meta-key">Sensors</span>${escapeHtml(sensorSummary)}</div>
          <div class="meta-item"><span class="meta-key">Obstacles / Stream</span>${escapeHtml(`${robot.latest_perception?.obstacle_count ?? 0} / ${streamState}`)}</div>
        </div>
        <div class="video-flags">${flags}</div>
      </article>`;
    })
    .join("");
}

function formatSensorSummary(summary) {
  if (!summary) return "not reported";
  const sensors = Array.isArray(summary.sensors) ? summary.sensors : [];
  const online = sensors.filter((sensor) => sensor.status === "online").length;
  const types = [...new Set(sensors.map((sensor) => sensor.sensor_type || sensor.sensor_id).filter(Boolean))].slice(0, 4);
  const label = types.length ? types.join(", ") : `${online}/${sensors.length || 0} online`;
  return `${summary.fusion_status || "offline"}: ${label}`;
}

function buildStreamView(robot, selectedStream) {
  const proxyUrl = resolveStreamUrl(robot.video_status?.proxy_url || "");
  const hasRegisteredStream = Boolean(getRobotStreamMap(robot)[normalizeStreamKey(selectedStream)] || robot.video_rtsp_url);
  const snapshotUrl = robot.video_status?.snapshot_url || "";
  const rtspUrl = robot.video_rtsp_url || "";
  const note = robot.video_status?.note || "";
  const streamUrl = hasRegisteredStream || proxyUrl ? backendVideoStreamUrl(robot, selectedStream) : "";
  if (isKinectRobot(robot) && (hasRegisteredStream || proxyUrl || robot.video_rtsp_url)) {
    const liveUrl = kinectLiveStreamUrl(robot, selectedStream);
    return `<img class="mjpeg-stream kinect-sdk-stream" src="${escapeHtml(liveUrl)}" alt="Live SDK stream for ${escapeHtml(robot.robot_id)} ${escapeHtml(streamDisplayName(selectedStream))}">`;
  }
  if (streamUrl) {
    return `<img class="mjpeg-stream" src="${escapeHtml(streamUrl)}" alt="Live stream for ${escapeHtml(robot.robot_id)}">`;
  }
  if (snapshotUrl) {
    return `<img class="snapshot-thumb" src="${escapeHtml(backendSnapshotUrl(snapshotUrl))}" alt="Snapshot for ${escapeHtml(robot.robot_id)}">`;
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

function closeKinectFullscreen() {
  if (!kinectFullscreenOverlay) return;
  kinectFullscreenOverlay.remove();
  kinectFullscreenOverlay = null;
  document.body.classList.remove("kinect-fullscreen-open");
}

function openKinectFullscreen(channelEl) {
  const streamKey = normalizeStreamKey(channelEl.dataset.stream || "color");
  const robotId = channelEl.closest(".kinect-live")?.dataset?.robotId || selectedKinectRobotId;
  const robot = robotsCache.find((item) => String(item.robot_id || "") === robotId) || robotsCache.find(isKinectRobot);
  if (!robot) return;

  closeKinectFullscreen();
  const liveUrl = kinectLiveStreamUrl(robot, streamKey);
  const overlay = document.createElement("div");
  overlay.className = "kinect-fullscreen";
  overlay.setAttribute("role", "dialog");
  overlay.setAttribute("aria-modal", "true");
  overlay.dataset.stream = streamKey;
  overlay.innerHTML = `
    <div class="kinect-fullscreen-head">
      <strong>${escapeHtml(robot.robot_id || "Kinect")} / ${escapeHtml(streamDisplayName(streamKey))}</strong>
      <button class="btn kinect-fullscreen-close" type="button" aria-label="Close Kinect fullscreen">CLOSE</button>
    </div>
    <div class="kinect-fullscreen-view">
      <img class="mjpeg-stream kinect-sdk-stream" src="${escapeHtml(liveUrl)}" alt="Live SDK stream for ${escapeHtml(robot.robot_id)} ${escapeHtml(streamDisplayName(streamKey))}">
    </div>
  `;
  overlay.addEventListener("click", (ev) => {
    if (ev.target === overlay || ev.target?.classList?.contains("kinect-fullscreen-close")) {
      closeKinectFullscreen();
    }
  });
  overlay.addEventListener("dblclick", (ev) => {
    if (ev.target instanceof Element && ev.target.closest(".kinect-fullscreen-view")) {
      closeKinectFullscreen();
    }
  });
  document.body.appendChild(overlay);
  document.body.classList.add("kinect-fullscreen-open");
  kinectFullscreenOverlay = overlay;
  overlay.querySelector(".kinect-fullscreen-close")?.focus();
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
    const throughput = Number.isFinite(Number(robot.network?.throughput_kb_s))
      ? finiteOrNaN(robot.network?.throughput_kb_s)
      : kbpsToKbS(robot.network?.throughput_kbps);
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
      throughput_kb_s: throughput,
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
  if (metric === "throughput_kb_s") return { label: "Throughput", unit: "KB/s", minMax: 40 };
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
  const totalThroughput = finiteValues(latestSamples.map((s) => s.throughput_kb_s)).reduce((a, b) => a + b, 0);
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
    <div class="summary-card"><span>Total Throughput</span><strong>${escapeHtml(formatRateKbS(totalThroughput))}</strong></div>
    <div class="summary-card"><span>Peak Throughput</span><strong>${escapeHtml(formatRateKbS(peakThroughput))}</strong></div>
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
          <span>thr ${escapeHtml(formatRateKbS(sample.throughput_kb_s))}</span>
          <span>max ${escapeHtml(formatRateKbS(maxThr))}</span>
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
  const seriesA = hasVideoData ? videoSamples.map((x) => x.offered_mb_s) : diagHistory.map((x) => x.latency_ms);
  const seriesB = hasVideoData ? videoSamples.map((x) => x.delivered_mb_s) : [];
  const values = finiteValues([...seriesA, ...seriesB]);
  const maxY = values.length
    ? Math.max(hasVideoData ? 1.25 : 80, ...values, hasVideoData ? stressState.simulated_capacity_mb_s : 0)
    : hasVideoData
      ? 1.25
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
    ? videoSamples.filter((x) => Number.isFinite(x.offered_mb_s))
    : diagHistory.filter((x) => Number.isFinite(x.latency_ms));
  if (pointsA.length >= 2) {
    ctx.strokeStyle = line;
    ctx.lineWidth = 2.2;
    ctx.beginPath();
    for (let i = 0; i < pointsA.length; i += 1) {
      const x = padding.left + (plotW * i) / (MAX_DIAG_POINTS - 1);
      const y = yToPx(hasVideoData ? pointsA[i].offered_mb_s : pointsA[i].latency_ms);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();
  }

  if (hasVideoData) {
    const pointsB = videoSamples.filter((x) => Number.isFinite(x.delivered_mb_s));
    if (pointsB.length >= 2) {
      ctx.strokeStyle = "#00d6ff";
      ctx.lineWidth = 2.3;
      ctx.beginPath();
      for (let i = 0; i < pointsB.length; i += 1) {
        const x = padding.left + (plotW * i) / (MAX_DIAG_POINTS - 1);
        const y = yToPx(pointsB[i].delivered_mb_s);
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.stroke();
    }

    if (Number.isFinite(stressState.simulated_capacity_mb_s) && stressState.simulated_capacity_mb_s > 0) {
      ctx.strokeStyle = "#f8ff57";
      ctx.setLineDash([6, 4]);
      ctx.lineWidth = 1.4;
      const y = yToPx(stressState.simulated_capacity_mb_s);
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
    ctx.fillText("Video stress throughput (MB/s)", padding.left + 4, 12);
    ctx.fillText(`${fmtNum(maxY, 1)}MB/s`, 2, padding.top + 10);
    ctx.fillText("0MB/s", 8, padding.top + plotH);
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
  const delivered = finiteValues(samples.map((x) => x.delivered_mb_s));
  const loss = finiteValues(samples.map((x) => x.loss_pct));
  const jitter = finiteValues(samples.map((x) => x.jitter_ms));
  const stableSamples = samples.filter((x) => x.loss_pct <= 2 && x.jitter_ms <= 20);
  const stableDelivered = finiteValues(stableSamples.map((x) => x.delivered_mb_s));
  const totalLimitMbS = stableDelivered.length ? percentile(stableDelivered, 0.95) : percentile(delivered, 0.5);
  const vehicleCount = Math.max(1, stressState.vehicle_count || 1);
  const singleLimitMbS = Number.isFinite(totalLimitMbS) ? totalLimitMbS / vehicleCount : Number.NaN;
  const avgLoss = average(loss);
  const p95Jitter = percentile(jitter, 0.95);
  const maxDeliveredMbS = delivered.length ? Math.max(...delivered) : Number.NaN;
  const currentDeliveredMbS = delivered.length ? delivered[delivered.length - 1] : Number.NaN;
  const stablePct = samples.length ? (stableSamples.length * 100) / samples.length : Number.NaN;
  const isStable = Number.isFinite(avgLoss) && Number.isFinite(p95Jitter) && avgLoss <= 2 && p95Jitter <= 20 && stablePct >= 80;
  return {
    running: stressState.running,
    duration_s: durationMs / 1000,
    vehicle_count: vehicleCount,
    sample_count: samples.length,
    simulated_capacity_mb_s: stressState.simulated_capacity_mb_s,
    total_bw_limit_mb_s: totalLimitMbS,
    per_stream_limit_mb_s: singleLimitMbS,
    avg_packet_loss_pct: avgLoss,
    p95_jitter_ms: p95Jitter,
    max_delivered_mb_s: maxDeliveredMbS,
    current_delivered_mb_s: currentDeliveredMbS,
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
  const totalTelemetryKbS = finiteValues(latestSamples.map((s) => s.throughput_kb_s)).reduce((a, b) => a + b, 0);
  const peakTelemetryKbS = Math.max(
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
    <div class="summary-card"><span>Video Total Limit</span><strong>${escapeHtml(formatRateKbS(stress.total_bw_limit_mb_s * 1024))}</strong></div>
    <div class="summary-card"><span>Video Per-stream Limit</span><strong>${escapeHtml(formatRateKbS(stress.per_stream_limit_mb_s * 1024))}</strong></div>
    <div class="summary-card"><span>Sim Avg Packet Loss</span><strong>${escapeHtml(fmtNum(stress.avg_packet_loss_pct, 2))} %</strong></div>
    <div class="summary-card"><span>Sim P95 Jitter</span><strong>${escapeHtml(fmtNum(stress.p95_jitter_ms, 1))} ms</strong></div>
  `;

  diagProtocolCards.innerHTML = `
    <div class="summary-card"><span>HTTP Pull Throughput</span><strong>${escapeHtml(formatRateKbS(snapshot.http_kb_s))}</strong></div>
    <div class="summary-card"><span>MQTT Telemetry Total</span><strong>${escapeHtml(formatRateKbS(totalTelemetryKbS))}</strong></div>
    <div class="summary-card"><span>MQTT Telemetry Peak</span><strong>${escapeHtml(formatRateKbS(peakTelemetryKbS))}</strong></div>
    <div class="summary-card"><span>RTSP Streams Online</span><strong>${escapeHtml(streamCount)}</strong></div>
    <div class="summary-card"><span>Sim Vehicles</span><strong>${escapeHtml(stress.vehicle_count)}</strong></div>
    <div class="summary-card"><span>Sim Link Capacity</span><strong>${escapeHtml(formatRateKbS(stress.simulated_capacity_mb_s * 1024))}</strong></div>
    <div class="summary-card"><span>Sim Max Delivered</span><strong>${escapeHtml(formatRateKbS(stress.max_delivered_mb_s * 1024))}</strong></div>
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
        <td>${escapeHtml(formatRateKbS(sample.throughput_kb_s))}</td>
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
    http_kb_s: bytesToKbS(health.bytes + robots.bytes, health.latency_ms + robots.latency_ms),
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
  stressState.simulated_capacity_mb_s = 0;
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
      `Video stress done: total limit ${formatRateKbS(s.total_bw_limit_mb_s * 1024)}, per stream ${formatRateKbS(s.per_stream_limit_mb_s * 1024)}, ${s.stable ? "stable" : "unstable"}`
    );
  } else {
    setDiagStatus(
      `Video stress stopped: total limit ${formatRateKbS(s.total_bw_limit_mb_s * 1024)}, per stream ${formatRateKbS(s.per_stream_limit_mb_s * 1024)}`
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
  const perStreamOfferedMbS = 0.15 + progress * 1.25;
  const offeredMbS = perStreamOfferedMbS * vehicleCount;
  const capacityMbS = Math.max(1, stressState.simulated_capacity_mb_s);
  const load = offeredMbS / capacityMbS;

  const baseLoss = 0.2 + Math.random() * 0.35;
  const overloadLoss = load > 1 ? (load - 1) * (18 + vehicleCount * 1.3) + Math.random() * 1.5 : 0;
  const lossPct = clamp(baseLoss + overloadLoss, 0, 45);

  const baseJitter = 2.0 + Math.random() * 3.5;
  const overloadJitter = load > 1 ? (load - 1) * 30 + Math.random() * 5 : 0;
  const jitterMs = clamp(baseJitter + overloadJitter, 0.5, 120);

  let deliveredMbS = offeredMbS * (1 - lossPct / 100);
  deliveredMbS = Math.min(deliveredMbS, capacityMbS * (0.95 + Math.random() * 0.05));
  deliveredMbS = Math.max(0, deliveredMbS);
  const perStreamDeliveredMbS = deliveredMbS / vehicleCount;

  return {
    t: Date.now(),
    elapsed_ms: elapsedMs,
    offered_mb_s: offeredMbS,
    delivered_mb_s: deliveredMbS,
    per_stream_delivered_mb_s: perStreamDeliveredMbS,
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
    `Video stress (${remaining}s left): delivered ${formatRateKbS(s.current_delivered_mb_s * 1024)}, loss ${fmtNum(s.avg_packet_loss_pct, 2)}%, jitter ${fmtNum(s.p95_jitter_ms, 1)}ms`
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
  stressState.simulated_capacity_mb_s = clamp(1.2 + Math.random() * 2.2 - stressState.vehicle_count * 0.075, 1.0, 4.3);
  stressState.startedAt = Date.now();
  stressState.stopAt = stressState.startedAt + 60_000;
  setDiagStatus(
    `Video simulation started: ${stressState.vehicle_count} robots, link cap ${formatRateKbS(stressState.simulated_capacity_mb_s * 1024)}`
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
  await refreshKinectBridgeHealth();
  robotsCache = robotsData.items || [];
  alertsCache = alertsData.items || [];
  healthCache = healthData;
  protocolSpecCache = protocolData;
  eventsCache = eventsData.items || [];
  updateNetworkHistory(robotsCache);
  renderRobotTable(robotsCache);
  renderKinectStage(robotsCache);
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

async function refreshAll({ quiet = true } = {}) {
  const tasks = [refreshRobots({ quiet }), refreshFormation({ quiet: true })];
  if (document.body.classList.contains("show-diagnostics")) {
    tasks.push(refreshDiagnosticsSnapshot({ quiet: true }));
  }
  const results = await Promise.allSettled(tasks);
  const failed = results.find((result) => result.status === "rejected");
  if (failed) {
    print({ warning: "Refresh after video settings failed", error: String(failed.reason) });
  }
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

videoWall.addEventListener("change", (ev) => {
  const target = ev.target;
  if (!(target instanceof HTMLSelectElement)) return;
  if (!target.classList.contains("video-stream-select")) return;
  const robotId = target.dataset.robotId || target.closest("[data-robot-id]")?.dataset?.robotId;
  if (!robotId) return;
  selectedStreamsByRobot[robotId] = normalizeStreamKey(target.value);
  renderVideoWall(robotsCache);
});

kinectStage.addEventListener("change", (ev) => {
  const target = ev.target;
  if (!(target instanceof HTMLSelectElement)) return;
  if (target.classList.contains("kinect-robot-select")) {
    selectedKinectRobotId = target.value;
    selectedKinectStream = "";
    localStorage.setItem("autofleet_kinect_robot", selectedKinectRobotId);
    localStorage.removeItem("autofleet_kinect_stream");
    renderKinectStage(robotsCache);
    return;
  }
  if (target.classList.contains("kinect-stream-select")) {
    selectedKinectStream = normalizeStreamKey(target.value);
    localStorage.setItem("autofleet_kinect_stream", selectedKinectStream);
    renderKinectStage(robotsCache);
  }
});

kinectStage.addEventListener("dblclick", (ev) => {
  if (!(ev.target instanceof Element)) return;
  const channelEl = ev.target.closest(".kinect-channel");
  if (!channelEl) return;
  ev.preventDefault();
  openKinectFullscreen(channelEl);
});

document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape" && kinectFullscreenOverlay) {
    ev.preventDefault();
    closeKinectFullscreen();
    return;
  }
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

async function applyCurrentApiBase() {
  const next = normalizeApiBaseInput(apiBaseInput.value || apiBaseCustomInput?.value || "");
  if (!next) return;
  try {
    setApiBase(next, { pinned: true });
    setEndpointStatus(`Using ${describeApiBase(next)}`, "online");
    await refreshRobots();
    await refreshFormation({ quiet: true });
    await refreshDiagnosticsSnapshot({ quiet: true });
  } catch (err) {
    setEndpointStatus(`Selected API failed: ${describeApiBase(next)}`, "offline");
    print({ error: String(err) });
  }
}

apiBaseInput.addEventListener("change", () => {
  void applyCurrentApiBase();
});

if (applyApiBaseBtn) {
  applyApiBaseBtn.onclick = () => {
    const next = normalizeApiBaseInput(apiBaseCustomInput?.value || apiBaseInput.value);
    if (!next) return;
    apiBaseInput.value = next;
    void applyCurrentApiBase();
  };
}

if (detectApiBtn) {
  detectApiBtn.onclick = async () => {
    apiBasePinned = false;
    availableApiBases = [];
    await detectApiEndpoints();
    await refreshAll({ quiet: true });
  };
}

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

if (applyVideoQualityBtn) {
  applyVideoQualityBtn.onclick = async () => {
    try {
      await applyVideoQuality();
    } catch (err) {
      if (videoQualityStatus) videoQualityStatus.textContent = "Video settings failed";
      print({ error: String(err) });
    }
  };
}

window.addEventListener("resize", () => {
  drawNetworkChart();
  drawDiagChart();
});

async function boot() {
  applyNeoTheme();
  initApiBase();
  setEndpointStatus(`Using ${describeApiBase(apiBase)} while detecting alternatives...`, "online");
  setDiagStatus("Idle.");
  setView(localStorage.getItem("autofleet_view") || "control");
  detectApiEndpoints().catch((err) => setEndpointStatus(`Endpoint detection failed: ${String(err)}`, "offline"));
  loadVideoSettings().catch(() => {});
  refreshRobots()
    .then(() => refreshDiagnosticsSnapshot({ quiet: true }))
    .catch((err) => print({ error: String(err) }));
  refreshFormation({ quiet: true }).catch((err) => print({ error: String(err) }));
}

boot().catch((err) => print({ error: String(err) }));
