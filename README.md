## AutoFleet (Technical MVP)

Module-first implementation for backend/control development, ignoring business slides.

### Protocol stack

- Control/telemetry: `MQTT` (`fleet/v1/...`)
- Management APIs: `HTTP REST` (FastAPI)
- Video: `RTSP URL` registered in telemetry (transcoding not included in MVP)

### Modules

- `infra/`: docker compose + mosquitto config
- `backend/`: REST API, MQTT bridge, mission state machine, JSONL logging
- `frontend/`: lightweight control panel for commands and missions
- `tools/`: robot simulator (`R1,R2,R3`)
- `data/`: logs, raw data, mission results

### Quick start

1. Start infra stack:

```bash
cd infra
docker compose up -d
```

2. Run robot simulator (local):

```bash
pip install paho-mqtt
python tools/robot_sim.py --host 127.0.0.1 --port 1883 --robots R1,R2,R3
```

3. Open control panel:

- `http://127.0.0.1:3000`

4. Check backend API:

- `http://127.0.0.1:8000/api/v1/health`

### API summary

- `GET /api/v1/health`
- `GET /api/v1/robots`
- `GET /api/v1/robots/{robot_id}/latest`
- `POST /api/v1/robots/{robot_id}/command`
- `POST /api/v1/missions/start`
- `POST /api/v1/missions/{mission_id}/return`
- `POST /api/v1/missions/{mission_id}/stop`
- `GET /api/v1/missions/{mission_id}`
