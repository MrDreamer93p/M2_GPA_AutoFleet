from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from autofleet_backend.app_state import AppState
from autofleet_backend.models import CommandRequest, FormationFollowStartRequest, MissionStartRequest, TeleopRequest


state = AppState()


@asynccontextmanager
async def lifespan(_: FastAPI):
    state.mqtt.connect()
    try:
        yield
    finally:
        state.mqtt.disconnect()


app = FastAPI(title="AutoFleet Backend", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/v1/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "ts": int(time.time())}


@app.get("/api/v1/robots")
def list_robots() -> dict[str, Any]:
    return {"items": state.runtime.list_robots()}


@app.get("/api/v1/robots/{robot_id}/latest")
def get_robot_latest(robot_id: str) -> dict[str, Any]:
    latest = state.runtime.get_robot_latest(robot_id)
    if latest is None:
        raise HTTPException(status_code=404, detail=f"Robot {robot_id} not found")
    return latest


@app.post("/api/v1/robots/{robot_id}/command")
def post_command(robot_id: str, req: CommandRequest) -> dict[str, Any]:
    envelope = state.build_command(robot_id=robot_id, kind=req.type, args=req.args, ttl_ms=req.ttl_ms)
    state.publish_command(envelope)
    return {"published": True, "command": envelope.model_dump()}


@app.post("/api/v1/teleop/{robot_id}")
def post_teleop(robot_id: str, req: TeleopRequest) -> dict[str, Any]:
    result = state.publish_teleop(robot_id=robot_id, linear_x=req.linear_x, angular_z=req.angular_z, ttl_ms=req.ttl_ms)
    return {"published": True, "teleop": result}


@app.post("/api/v1/missions/start")
def start_mission(req: MissionStartRequest) -> dict[str, Any]:
    mission = state.create_mission(
        mission_id=req.mission_id,
        robots=req.robot_ids,
        metadata={"zone": req.zone, "return_point": req.return_point, "strategy": req.strategy},
    )
    state.update_mission(req.mission_id, "RUNNING")

    for robot_id in req.robot_ids:
        envelope = state.build_command(
            robot_id=robot_id,
            kind="START_MISSION",
            args={
                "mission_id": req.mission_id,
                "zone": req.zone,
                "return_point": req.return_point,
                "strategy": req.strategy,
            },
            ttl_ms=3_000,
        )
        state.publish_command(envelope)

    return {"mission": mission.model_dump(), "status": "RUNNING"}


@app.post("/api/v1/missions/{mission_id}/stop")
def stop_mission(mission_id: str) -> dict[str, Any]:
    mission = state.runtime.get_mission(mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail=f"Mission {mission_id} not found")

    state.update_mission(mission_id, "STOPPED")
    for robot_id in mission.robots:
        envelope = state.build_command(robot_id=robot_id, kind="STOP", args={"mission_id": mission_id}, ttl_ms=2_000)
        state.publish_command(envelope)

    return {"mission_id": mission_id, "status": "STOPPED"}


@app.post("/api/v1/missions/{mission_id}/return")
def return_mission(mission_id: str) -> dict[str, Any]:
    mission = state.runtime.get_mission(mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail=f"Mission {mission_id} not found")

    state.update_mission(mission_id, "RETURNING")
    return_point = mission.metadata.get("return_point", {})
    for robot_id in mission.robots:
        envelope = state.build_command(
            robot_id=robot_id,
            kind="RETURN_HOME",
            args={"mission_id": mission_id, "return_point": return_point},
            ttl_ms=2_500,
        )
        state.publish_command(envelope)

    return {"mission_id": mission_id, "status": "RETURNING"}


@app.get("/api/v1/missions/{mission_id}")
def get_mission(mission_id: str) -> dict[str, Any]:
    mission = state.runtime.get_mission(mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail=f"Mission {mission_id} not found")
    return mission.model_dump()


@app.get("/api/v1/formation")
def get_formation() -> dict[str, Any]:
    return state.get_formation().model_dump()


@app.post("/api/v1/formation/follow/start")
def start_follow_formation(req: FormationFollowStartRequest) -> dict[str, Any]:
    if not req.follower_ids:
        raise HTTPException(status_code=400, detail="follower_ids must not be empty")
    formation = state.start_follow_formation(leader_id=req.leader_id, follower_ids=req.follower_ids)
    return {"formation": formation.model_dump()}


@app.post("/api/v1/formation/follow/stop")
def stop_follow_formation() -> dict[str, Any]:
    formation = state.stop_follow_formation()
    return {"formation": formation.model_dump()}
