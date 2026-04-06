const defaultHost = window.location.hostname || "127.0.0.1";
const defaultProto = window.location.protocol === "https:" ? "https:" : "http:";
const defaultApiBases = [
  `${defaultProto}//${defaultHost}:8200/api/v1`,
  `${defaultProto}//${defaultHost}:8000/api/v1`,
  `${defaultProto}//${defaultHost}:8001/api/v1`,
];
let apiBase = (localStorage.getItem("autofleet_api_base") || defaultApiBases[0]).replace(/\/+$/, "");

const apiBaseInput = document.getElementById("apiBase");
const refreshBtn = document.getElementById("refreshBtn");
const autoRefreshToggle = document.getElementById("autoRefreshToggle");
const summaryGrid = document.getElementById("summaryGrid");
const streamGrid = document.getElementById("streamGrid");
const robotsJson = document.getElementById("robotsJson");
const streamsJson = document.getElementById("streamsJson");
const eventsJson = document.getElementById("eventsJson");

apiBaseInput.value = apiBase;

let refreshTimer = null;

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

async function api(path) {
  let lastError = null;
  for (const base of Array.from(new Set([apiBase, ...defaultApiBases]))) {
    try {
      const res = await fetch(`${base}${path}`);
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
      apiBase = base.replace(/\/+$/, "");
      localStorage.setItem("autofleet_api_base", apiBase);
      apiBaseInput.value = apiBase;
      return body;
    } catch (err) {
      lastError = err;
    }
  }
  throw lastError || new Error(`Failed to fetch ${path}`);
}

function renderSummary(robots, streams, events) {
  const online = robots.filter((r) => r.online).length;
  const streamOnline = streams.filter((s) => s.status === "online").length;
  const simulatorEvents = events.filter((e) => e.event_type === "telemetry").length;
  summaryGrid.innerHTML = `
    <div class="summary-card"><span>Robots Online</span><strong>${escapeHtml(`${online}/${robots.length}`)}</strong></div>
    <div class="summary-card"><span>Streams Online</span><strong>${escapeHtml(`${streamOnline}/${streams.length}`)}</strong></div>
    <div class="summary-card"><span>Telemetry Events</span><strong>${escapeHtml(simulatorEvents)}</strong></div>
    <div class="summary-card"><span>API Endpoint</span><strong>${escapeHtml(apiBase)}</strong></div>
  `;
}

function renderStreams(robots, streams) {
  const robotsById = Object.fromEntries(robots.map((r) => [r.robot_id, r]));
  streamGrid.innerHTML = streams
    .map((stream) => {
      const robot = robotsById[stream.robot_id] || {};
      const pose = robot.pose || {};
      const network = robot.network || {};
      return `
        <article class="sim-card">
          <div class="sim-card-head">
            <strong>${escapeHtml(stream.robot_id)}</strong>
            <span class="${stream.status === "online" ? "online" : "offline"}">${escapeHtml(stream.status || "offline")}</span>
          </div>
          <dl>
            <dt>source_url</dt><dd>${escapeHtml(stream.source_url || "-")}</dd>
            <dt>view_profile</dt><dd>${escapeHtml(stream.view_profile || robot.video_view_profile || "-")}</dd>
            <dt>proxy_url</dt><dd>${escapeHtml(stream.proxy_url || "-")}</dd>
            <dt>note</dt><dd>${escapeHtml(stream.note || "-")}</dd>
            <dt>state</dt><dd>${escapeHtml(robot.state || "-")}</dd>
            <dt>battery</dt><dd>${escapeHtml(fmtNum(robot.battery, 3))}</dd>
            <dt>pose</dt><dd>${escapeHtml(`${fmtNum(pose.x, 2)}, ${fmtNum(pose.y, 2)}, ${fmtNum(pose.yaw, 2)}`)}</dd>
            <dt>network</dt><dd>${escapeHtml(`lat ${fmtNum(network.latency_ms, 1)} ms | loss ${fmtNum(network.packet_loss_pct, 1)} % | tp ${fmtNum(network.throughput_kbps, 0)} kbps`)}</dd>
          </dl>
        </article>
      `;
    })
    .join("");
}

async function refresh() {
  const [robotsData, streamsData, eventsData] = await Promise.all([
    api("/robots"),
    api("/video/streams"),
    api("/events?limit=20"),
  ]);
  const robots = robotsData.items || [];
  const streams = streamsData.items || [];
  const events = eventsData.items || [];
  renderSummary(robots, streams, events);
  renderStreams(robots, streams);
  robotsJson.textContent = JSON.stringify(robots, null, 2);
  streamsJson.textContent = JSON.stringify(streams, null, 2);
  eventsJson.textContent = JSON.stringify(events, null, 2);
}

function resetAutoRefresh() {
  if (refreshTimer) {
    clearInterval(refreshTimer);
    refreshTimer = null;
  }
  if (autoRefreshToggle.checked) {
    refreshTimer = setInterval(() => {
      refresh().catch((err) => {
        eventsJson.textContent = JSON.stringify({ error: String(err) }, null, 2);
      });
    }, 1000);
  }
}

refreshBtn.onclick = () => refresh().catch((err) => {
  eventsJson.textContent = JSON.stringify({ error: String(err) }, null, 2);
});

document.getElementById("applyApiBaseBtn").onclick = () => {
  apiBase = apiBaseInput.value.trim().replace(/\/+$/, "");
  localStorage.setItem("autofleet_api_base", apiBase);
  refresh().catch((err) => {
    eventsJson.textContent = JSON.stringify({ error: String(err) }, null, 2);
  });
};

autoRefreshToggle.onchange = resetAutoRefresh;

refresh().catch((err) => {
  eventsJson.textContent = JSON.stringify({ error: String(err) }, null, 2);
});
resetAutoRefresh();
