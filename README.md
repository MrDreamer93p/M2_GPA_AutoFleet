## AutoFleet

Multi-robot supervision and V2X control stack for an educational fleet project.

This repository now includes:

- `frontend/`: browser control dashboard with fleet view, video wall, alerts, protocol status, diagnostics
- `backend/`: FastAPI orchestration layer, MQTT bridge, runtime aggregation, PostgreSQL persistence
- `workers/video_worker/`: RTSP/simulated stream ingestion and browser-playable MJPEG proxy
- `workers/perception_worker/`: snapshot-based hazard/obstacle perception worker publishing alerts and map summaries
- `infra/`: Docker Compose stack and Mosquitto config
- `tools/`: robot simulator publishing telemetry, heartbeats, commands ACKs, and stream metadata
- `data/`: runtime logs, mission artifacts, snapshots, alert evidence

## Protocol Stack

- Control and telemetry: `MQTT`
- Operator supervision and mission APIs: `HTTP REST`
- Video source registration: `RTSP`, `file://`, local files, public `http(s)://` media, or dataset replay manifests
- Browser video rendering: `MJPEG proxy` via `video-worker`
- Persistence: `PostgreSQL`

Topic families under `fleet/v1`:

- `fleet/v1/cmd/{robot_id}`
- `fleet/v1/telemetry/{robot_id}`
- `fleet/v1/ack/{robot_id}`
- `fleet/v1/heartbeat/{source_id}`
- `fleet/v1/video_status/{robot_id}`
- `fleet/v1/perception/{robot_id}`
- `fleet/v1/alert/{robot_id}`
- `fleet/v1/map/{robot_id}`
- `fleet/v1/coordination/{robot_id}`
- `fleet/v1/event/{robot_id}`
- `fleet/v1/mission/{mission_id}`

## Technical Architecture

### Component Roles

| Component | Responsibility |
| --- | --- |
| `frontend/` | Operator dashboard, video wall, supervision controls, diagnostics lab, simulator monitor |
| `backend/` | FastAPI API, MQTT bridge, fleet-state aggregation, command dispatch, persistence |
| `mosquitto` | Lightweight V2X message bus between supervision, workers, and robots |
| `workers/video_worker/` | Stream registration, video ingestion, view-profile cropping, MJPEG proxy, snapshot export |
| `workers/perception_worker/` | Snapshot polling, lightweight obstacle/hazard heuristics, alert and map publication |
| `tools/robot_sim.py` | Robot telemetry simulator, ACK/heartbeat publisher, video source registration publisher |
| `tools/prepare_multiview_dataset.py` | Converts A2D2 / PandaSet-style sequences into three local replay videos plus a manifest |
| `postgres` | Stores latest robot state, missions, alerts, perception summaries, coordination state |
| `data/` | Runtime logs, snapshots, alert evidence, replay assets |

### Runtime Data Flows

Control and telemetry:

`Web UI -> FastAPI backend -> MQTT broker -> robots / simulator`

Return path:

`robots / simulator -> MQTT telemetry + ack + heartbeat -> backend -> frontend`

Video and perception:

`robot camera / public demo video / dataset replay -> video-worker -> MJPEG proxy + snapshots -> perception-worker -> MQTT perception + alert + map -> backend -> frontend`

Persistence:

`backend runtime events -> JSONL logs + PostgreSQL latest-state tables`

### Technical Notes

- The backend is the orchestration layer, not the real-time motor controller.
- The robot or simulator publishes telemetry and video metadata through MQTT.
- `video-worker` is the only service that touches video sources directly.
- Browser playback is done through MJPEG proxy endpoints, so the frontend never needs native RTSP support.
- The simulator can publish a single shared source with virtual camera crops, or one source per robot through a manifest.
- The current repository covers supervision, communication, replay, and software integration. Low-level motor control, sensor drivers, and onboard autonomy stay outside this codebase.

### Default Ports

| Service | Port | Purpose |
| --- | --- | --- |
| Frontend | `3000` | Main operator UI and simulator monitor |
| Backend API | `8200` | REST API for supervision and fleet state |
| Video worker | `8400` | MJPEG streams, snapshots, worker health |
| Mosquitto MQTT | `3889` | Telemetry, commands, ACKs, alerts, video status |
| PostgreSQL | `5432` | Runtime persistence |

## Main Features

- Manual teleoperation with keyboard
- Mission start / stop / return
- Leader / follower formation
- Command ACK tracking, RTT computation, timeout and retry handling
- Robot heartbeats and service heartbeats
- Video wall with browser-playable proxy streams
- Active alert list with acknowledgement
- Spatial risk panel with robot positions, coordination, and obstacle summaries
- Protocol status panel with recent V2X activity
- Network diagnostics lab

## Docker Stack

Defined in [`infra/compose.yml`](./infra/compose.yml):

- `postgres`
- `mosquitto`
- `backend`
- `video-worker`
- `perception-worker`
- `robot-sim`
- `frontend`

## Startup Guide

### Prerequisites

- Docker Desktop
- Python `3.11+` if you want to run simulator or services outside Docker
- PowerShell on Windows

### Option A: Recommended Full Stack Startup With Docker

From the repository root:

```powershell
cd infra
$env:AUTOFLEET_API_PORT='8200'
$env:AUTOFLEET_PUBLIC_HOST='127.0.0.1'
docker compose up -d
```

This starts:

- `postgres`
- `mosquitto`
- `backend`
- `video-worker`
- `perception-worker`
- `robot-sim`
- `frontend`

Why `8200`? On some Windows machines, port `8000` is reserved by the OS. The compose file already supports this through `AUTOFLEET_API_PORT`.

### Option B: Development Startup With Core Infra in Docker and App Code Local

Start only the infrastructure dependencies:

```powershell
cd infra
docker compose up -d postgres mosquitto
```

Then, from the repository root, launch each service in its own terminal:

```powershell
cd backend
pip install -r requirements.txt
$env:AUTOFLEET_MQTT_HOST='127.0.0.1'
$env:AUTOFLEET_MQTT_PORT='3889'
$env:AUTOFLEET_DATABASE_DSN='postgresql://autofleet:autofleet@127.0.0.1:5432/autofleet'
$env:AUTOFLEET_VIDEO_PUBLIC_BASE='http://127.0.0.1:8400'
$env:AUTOFLEET_VIDEO_WORKER_BASE='http://127.0.0.1:8400'
uvicorn main:app --host 0.0.0.0 --port 8200
```

```powershell
cd workers\video_worker
pip install -r requirements.txt
$env:AUTOFLEET_MQTT_HOST='127.0.0.1'
$env:AUTOFLEET_MQTT_PORT='3889'
$env:AUTOFLEET_VIDEO_PUBLIC_BASE='http://127.0.0.1:8400'
$env:AUTOFLEET_VIDEO_SNAPSHOT_DIR=(Resolve-Path ..\..\data\artifacts\snapshots)
uvicorn main:app --host 0.0.0.0 --port 8400
```

```powershell
cd workers\perception_worker
pip install -r requirements.txt
$env:AUTOFLEET_MQTT_HOST='127.0.0.1'
$env:AUTOFLEET_MQTT_PORT='3889'
$env:AUTOFLEET_VIDEO_WORKER_BASE='http://127.0.0.1:8400'
$env:AUTOFLEET_ALERT_SNAPSHOT_DIR=(Resolve-Path ..\..\data\artifacts\alerts)
python main.py
```

```powershell
cd frontend
python -m http.server 3000 --bind 0.0.0.0
```

### Health Checks After Startup

- Frontend: `http://127.0.0.1:3000`
- Simulator monitor: `http://127.0.0.1:3000/sim-monitor.html`
- Backend health: `http://127.0.0.1:8200/api/v1/health`
- Video worker health: `http://127.0.0.1:8400/health`
- Backend fleet state: `http://127.0.0.1:8200/api/v1/robots`
- Video stream registry: `http://127.0.0.1:8400/streams`

If you updated the frontend while the site container was already running, perform a hard refresh so the new JavaScript is loaded.

## Simulation and Replay Startup

### Mode 1: Default Ground-Level Demo Source

The repository now defaults to a forward-facing highway clip instead of the old overhead OpenCV sample. This is a better approximation of a robot or vehicle front camera.

If you use the Docker stack, `robot-sim` downloads and uses it automatically.

To run the simulator locally against the running stack:

```powershell
pip install -r tools/requirements.txt
python tools/fetch_demo_video.py
python tools/robot_sim.py --host 127.0.0.1 --port 3889 --robots R1,R2,R3 --video-source-template file:///artifacts/demo/highway-forward.mp4 --video-view-mode convoy3
```

When several robots share one source, `--video-view-mode convoy3` applies three virtual camera crops:

- `R1 -> front_left`
- `R2 -> front_center`
- `R3 -> front_right`

### Mode 2: Old Overhead Demo Source

If you need the original OpenCV sample back:

```powershell
python tools/fetch_demo_video.py --preset opencv_aerial
python tools/robot_sim.py --host 127.0.0.1 --port 3889 --robots R1,R2,R3 --video-source-template file:///artifacts/demo/vtest.avi --video-view-mode convoy3
```

### Mode 3: Real Multiview Dataset Replay

To replay a real multiview sequence such as A2D2 or PandaSet:

```powershell
python tools/prepare_multiview_dataset.py --dataset a2d2 --dataset-root D:\datasets\A2D2 --sequence 20180810_150607
python tools/robot_sim.py --host 127.0.0.1 --port 3889 --robots R1,R2,R3 --video-source-map data\artifacts\datasets\current\multiview_manifest.json
```

The preparation tool exports:

- `R1.avi`
- `R2.avi`
- `R3.avi`
- `multiview_manifest.json`

under `data/artifacts/datasets/current/`.

The manifest already contains `/artifacts/...` source URLs, so the existing `video-worker` can consume it without any additional code change.

### Mode 4: Custom Public or Local Video Source

You can also point the simulator at any source accepted by `video-worker`:

- `rtsp://...`
- `file:///artifacts/...`
- local file paths
- `http(s)://...`
- `camera://0`

Example:

```powershell
python tools/robot_sim.py --host 127.0.0.1 --port 3889 --robots R1,R2,R3 --video-source-template file:///artifacts/demo/my-source.mp4 --video-view-mode convoy3
```

## Important Runtime Notes

- Browser video playback does not rely on direct RTSP anymore; `video-worker` exposes MJPEG endpoints for the frontend.
- `perception-worker` uses the latest snapshots to publish:
  - perception summaries
  - alerts
  - map summaries
- Alert snapshots are stored under `data/artifacts/alerts/`.
- Stream snapshots are stored under `data/artifacts/snapshots/`.

## API Summary

- `GET /api/v1/health`
- `GET /api/v1/protocol`
- `GET /api/v1/robots`
- `GET /api/v1/robots/{robot_id}/latest`
- `GET /api/v1/robots/{robot_id}/history`
- `POST /api/v1/robots/{robot_id}/command`
- `POST /api/v1/teleop/{robot_id}`
- `GET /api/v1/missions`
- `POST /api/v1/missions/start`
- `POST /api/v1/missions/{mission_id}/return`
- `POST /api/v1/missions/{mission_id}/stop`
- `GET /api/v1/missions/{mission_id}`
- `GET /api/v1/formation`
- `POST /api/v1/formation/follow/start`
- `POST /api/v1/formation/follow/stop`
- `GET /api/v1/alerts`
- `POST /api/v1/alerts/{alert_id}/ack`
- `GET /api/v1/video/streams`
- `GET /api/v1/perception`
- `GET /api/v1/map/summaries`
- `GET /api/v1/coordination`
- `GET /api/v1/events`

## PostgreSQL Usage

The backend persists runtime data to PostgreSQL when `AUTOFLEET_DATABASE_DSN` is configured.

Current persistence coverage includes:

- latest robot state
- alerts
- missions
- perception summaries
- video stream status
- heartbeats
- map summaries
- coordination summaries
- generic event history

The backend also keeps JSONL logs in `data/logs/` for easy offline inspection.

## Scope of the Current MVP

This repository focuses on the communication, supervision, and fleet-control side:

- V2X message design
- supervision APIs
- fleet state aggregation
- video proxy integration
- perception event ingestion
- alert management
- communication stabilization primitives

Hardware control, low-level sensor drivers, SLAM, and onboard autonomy remain outside the scope of this repository and should integrate through the MQTT protocol and the published schemas.
