from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


RiskLevel = Literal["NONE", "LOW", "MEDIUM", "HIGH", "CRITICAL"]
AlertSeverity = Literal["info", "warning", "critical"]
AlertStatus = Literal["active", "acknowledged", "resolved"]


class Pose(BaseModel):
    x: float
    y: float
    yaw: float


class NetworkMetrics(BaseModel):
    latency_ms: float | None = None
    packet_loss_pct: float | None = None
    throughput_kbps: float | None = None
    rssi_dbm: float | None = None


class ControlState(BaseModel):
    linear_x: float = 0.0
    angular_z: float = 0.0


class MotorState(BaseModel):
    left_rpm: float = 0.0
    right_rpm: float = 0.0


class MapObstacle(BaseModel):
    obstacle_id: str
    x: float
    y: float
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    label: str = "obstacle"


class MapSummary(BaseModel):
    v: int = 1
    schema: str = "autofleet.map.v1"
    robot_id: str
    ts: int
    obstacle_count: int = 0
    local_free_ratio: float | None = None
    risk_level: RiskLevel = "NONE"
    note: str | None = None
    obstacles: list[MapObstacle] = Field(default_factory=list)


class ObstacleSummary(BaseModel):
    obstacle_count: int = 0
    min_distance_m: float | None = None
    risk_level: RiskLevel = "NONE"


class Telemetry(BaseModel):
    v: int = 1
    schema: str = "autofleet.telemetry.v1"
    robot_id: str
    ts: int
    pose: Pose
    battery: float = Field(ge=0.0, le=1.0)
    state: str
    mission_id: str | None = None
    video_rtsp_url: str | None = None
    controls: ControlState | None = None
    motors: MotorState | None = None
    network: NetworkMetrics | None = None
    obstacle_summary: ObstacleSummary | None = None
    map_summary: MapSummary | None = None
    raw: dict[str, Any] | None = None


class CommandRequest(BaseModel):
    type: str
    args: dict[str, Any] = Field(default_factory=dict)
    ttl_ms: int = 2_000
    correlation_id: str | None = None


class TeleopRequest(BaseModel):
    linear_x: float = Field(default=0.0, ge=-1.0, le=1.0)
    angular_z: float = Field(default=0.0, ge=-1.0, le=1.0)
    ttl_ms: int = 300


class CommandEnvelope(BaseModel):
    v: int = 1
    schema: str = "autofleet.command.v1"
    cmd_id: str
    robot_id: str
    type: str
    args: dict[str, Any] = Field(default_factory=dict)
    ttl_ms: int = 2_000
    sent_ts: int | None = None
    attempt: int = 1
    source: str = "backend"
    correlation_id: str | None = None


class Ack(BaseModel):
    v: int = 1
    schema: str = "autofleet.ack.v1"
    cmd_id: str
    robot_id: str
    status: Literal["ACCEPTED", "REJECTED", "DONE", "FAILED", "TIMEOUT", "RETRYING"] | str
    ts: int
    attempt: int | None = None
    correlation_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    rtt_ms: int | None = None


class Heartbeat(BaseModel):
    v: int = 1
    schema: str = "autofleet.heartbeat.v1"
    source_id: str
    source_type: Literal["robot", "backend", "video_worker", "perception_worker", "service"] | str
    robot_id: str | None = None
    status: str = "OK"
    ts: int
    meta: dict[str, Any] = Field(default_factory=dict)


class VideoStreamStatus(BaseModel):
    v: int = 1
    schema: str = "autofleet.video_status.v1"
    robot_id: str
    ts: int
    source_url: str | None = None
    proxy_url: str | None = None
    snapshot_url: str | None = None
    status: Literal["online", "degraded", "offline"] | str = "offline"
    codec: str | None = None
    fps: float | None = None
    bitrate_kbps: float | None = None
    width: int | None = None
    height: int | None = None
    note: str | None = None


class BoundingBox(BaseModel):
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    w: float = Field(ge=0.0, le=1.0)
    h: float = Field(ge=0.0, le=1.0)


class Detection(BaseModel):
    label: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    bbox: BoundingBox | None = None
    severity: AlertSeverity = "info"


class PerceptionSummary(BaseModel):
    v: int = 1
    schema: str = "autofleet.perception.v1"
    robot_id: str
    ts: int
    risk_level: RiskLevel = "NONE"
    obstacle_count: int = 0
    detections: list[Detection] = Field(default_factory=list)
    snapshot_url: str | None = None
    frame_source: str | None = None
    note: str | None = None


class AlertEvent(BaseModel):
    v: int = 1
    schema: str = "autofleet.alert.v1"
    alert_id: str
    robot_id: str
    ts: int
    alert_type: str
    severity: AlertSeverity = "warning"
    status: AlertStatus = "active"
    message: str
    source: str = "backend"
    evidence_url: str | None = None
    position: Pose | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


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


class FormationFollowStartRequest(BaseModel):
    leader_id: str
    follower_ids: list[str]


class FormationState(BaseModel):
    enabled: bool = False
    leader_id: str | None = None
    follower_ids: list[str] = Field(default_factory=list)
    updated_at: int = 0


class NeighborState(BaseModel):
    robot_id: str
    distance_m: float
    bearing_deg: float
    risk_level: RiskLevel = "NONE"


class CoordinationSummary(BaseModel):
    v: int = 1
    schema: str = "autofleet.coordination.v1"
    robot_id: str
    ts: int
    leader_id: str | None = None
    role: Literal["leader", "follower", "independent"] | str = "independent"
    min_peer_distance_m: float | None = None
    collision_risk: RiskLevel = "NONE"
    neighbors: list[NeighborState] = Field(default_factory=list)


class AlertAckRequest(BaseModel):
    status: AlertStatus = "acknowledged"

