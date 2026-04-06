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
- Video source registration: `RTSP`
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

## Architecture

Communication chain:

`Web UI -> FastAPI backend -> MQTT broker -> robots`

Return path:

`robots -> MQTT telemetry/ack/heartbeat -> backend -> frontend`

Video and perception chain:

`robot RTSP (or simulated source) -> video-worker -> MJPEG proxy + snapshots -> perception-worker -> MQTT perception/alert/map -> backend -> frontend`

Persistence chain:

`backend runtime events -> JSONL logs + PostgreSQL latest-state tables`

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

## Quick Start

### 1. Start the stack

From the repository root:

```powershell
cd infra
$env:AUTOFLEET_API_PORT='8200'
$env:AUTOFLEET_PUBLIC_HOST='127.0.0.1'
docker compose up -d
```

Why `8200`? On some Windows machines, port `8000` is reserved by the OS. The compose file supports a configurable API port through `AUTOFLEET_API_PORT`.

### 2. Start the simulator

From the repository root:

```powershell
pip install -r tools/requirements.txt
python tools/robot_sim.py --host 127.0.0.1 --port 3889 --robots R1,R2,R3
```

The Docker stack can also keep the simulator alive for you:

```powershell
cd infra
docker compose up -d robot-sim
```

To drive the video wall with a real public sample video instead of synthetic fallback frames:

```powershell
python tools/fetch_demo_video.py
python tools/robot_sim.py --host 127.0.0.1 --port 3889 --robots R1,R2,R3 --video-source-template file:///artifacts/demo/vtest.avi --video-view-mode convoy3
```

The bundled demo downloader pulls OpenCV's public `vtest.avi` sample into `data/artifacts/demo/`, which is already mounted into the `video-worker` container as `/artifacts/demo/`.
When several robots share one video source, `--video-view-mode convoy3` applies three virtual camera crops: `front_left`, `front_center`, and `front_right`.

To drive the wall from a real multiview dataset sequence such as A2D2 or PandaSet:

```powershell
python tools/prepare_multiview_dataset.py --dataset a2d2 --dataset-root D:\datasets\A2D2 --sequence 20180810_150607
python tools/robot_sim.py --host 127.0.0.1 --port 3889 --robots R1,R2,R3 --video-source-map data\artifacts\datasets\current\multiview_manifest.json
```

The preparation tool exports `R1.avi`, `R2.avi`, `R3.avi` plus `multiview_manifest.json` under `data/artifacts/datasets/current/`. The manifest already contains `source_url` values pointing at `/artifacts/...`, so the existing `video-worker` can consume it without extra changes.

### 3. Open the dashboard

- Frontend: `http://127.0.0.1:3000`
- Simulator monitor: `http://127.0.0.1:3000/sim-monitor.html`
- Backend health: `http://127.0.0.1:8200/api/v1/health`
- Video worker health: `http://127.0.0.1:8400/health`

### 4. Hard refresh the browser

If you updated the frontend while the container was already running, perform a hard refresh so the new JavaScript is loaded.

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
