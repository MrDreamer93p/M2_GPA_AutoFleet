const API_BASE = "http://127.0.0.1:8000/api/v1";

const output = document.getElementById("output");
const robotTable = document.getElementById("robotTable");

function print(obj) {
  output.textContent = JSON.stringify(obj, null, 2);
}

async function api(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const body = await res.json();
  if (!res.ok) throw new Error(body.detail || "API error");
  return body;
}

async function refreshRobots() {
  const data = await api("/robots");
  robotTable.innerHTML = "";
  for (const robot of data.items) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${robot.robot_id}</td>
      <td class="${robot.online ? "online" : "offline"}">${robot.online}</td>
      <td>${robot.state ?? "-"}</td>
      <td>${robot.battery ?? "-"}</td>
      <td>${robot.last_seen_age_s ?? "-"}</td>
      <td>${robot.video_rtsp_url ?? "-"}</td>
    `;
    robotTable.appendChild(tr);
  }
  print(data);
}

document.getElementById("refreshBtn").onclick = async () => {
  try {
    await refreshRobots();
  } catch (err) {
    print({ error: String(err) });
  }
};

document.getElementById("sendCmdBtn").onclick = async () => {
  try {
    const robotId = document.getElementById("robotId").value.trim();
    const type = document.getElementById("commandType").value.trim();
    const args = JSON.parse(document.getElementById("commandArgs").value || "{}");
    const data = await api(`/robots/${robotId}/command`, {
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
    const robotIds = document.getElementById("missionRobots").value.split(",").map(x => x.trim()).filter(Boolean);
    const payload = {
      mission_id: missionId,
      robot_ids: robotIds,
      zone: { crs: "local", polygon: [[0, 0], [12, 0], [12, 6], [0, 6]] },
      return_point: { x: 0.5, y: 0.5 },
      strategy: { pattern: "lawnmower", lane_width_m: 0.8, speed_mps: 0.4 }
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
    const data = await api(`/missions/${missionId}/return`, { method: "POST" });
    print(data);
  } catch (err) {
    print({ error: String(err) });
  }
};

document.getElementById("stopMissionBtn").onclick = async () => {
  try {
    const missionId = document.getElementById("missionId").value.trim();
    const data = await api(`/missions/${missionId}/stop`, { method: "POST" });
    print(data);
  } catch (err) {
    print({ error: String(err) });
  }
};

refreshRobots().catch((err) => print({ error: String(err) }));
