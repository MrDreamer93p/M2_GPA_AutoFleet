from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class Pose(BaseModel):
    x: float
    y: float
    yaw: float


class Telemetry(BaseModel):
    v: int = 1
    robot_id: str
    ts: int
    pose: Pose
    battery: float = Field(ge=0.0, le=1.0)
    state: str
    mission_id: str | None = None
    video_rtsp_url: str | None = None
    raw: dict[str, Any] | None = None


class CommandRequest(BaseModel):
    type: str
    args: dict[str, Any] = Field(default_factory=dict)
    ttl_ms: int = 2_000


class CommandEnvelope(BaseModel):
    v: int = 1
    cmd_id: str
    robot_id: str
    type: str
    args: dict[str, Any] = Field(default_factory=dict)
    ttl_ms: int = 2_000


class Ack(BaseModel):
    v: int = 1
    cmd_id: str
    robot_id: str
    status: Literal["ACCEPTED", "REJECTED", "DONE", "FAILED"] | str
    ts: int


class MissionStartRequest(BaseModel):
    mission_id: str
    robot_ids: list[str]
    zone: dict[str, Any]
    return_point: dict[str, float]
    strategy: dict[str, Any]


class MissionState(BaseModel):
    mission_id: str
    status: Literal["PLANNING", "RUNNING", "RETURNING", "DONE", "FAILED", "STOPPED"]
    robots: list[str]
    created_at: int
    updated_at: int
    metadata: dict[str, Any] = Field(default_factory=dict)
