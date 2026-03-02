from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .config import settings
from .models import MissionState, Telemetry


@dataclass
class RuntimeState:
    latest_telemetry: dict[str, Telemetry] = field(default_factory=dict)
    latest_ack: dict[str, dict[str, Any]] = field(default_factory=dict)
    active_missions: dict[str, MissionState] = field(default_factory=dict)

    def upsert_telemetry(self, payload: dict[str, Any]) -> Telemetry:
        telemetry = Telemetry.model_validate({**payload, "raw": payload})
        self.latest_telemetry[telemetry.robot_id] = telemetry
        return telemetry

    def upsert_ack(self, payload: dict[str, Any]) -> None:
        robot_id = str(payload.get("robot_id", "unknown"))
        self.latest_ack[robot_id] = payload

    def list_robots(self) -> list[dict[str, Any]]:
        now = int(time.time())
        out: list[dict[str, Any]] = []
        for robot_id, telem in sorted(self.latest_telemetry.items()):
            last_seen_age = max(0, now - telem.ts)
            out.append(
                {
                    "robot_id": robot_id,
                    "state": telem.state,
                    "battery": telem.battery,
                    "mission_id": telem.mission_id,
                    "video_rtsp_url": telem.video_rtsp_url,
                    "last_seen_ts": telem.ts,
                    "last_seen_age_s": last_seen_age,
                    "online": last_seen_age <= settings.robot_timeout_seconds,
                    "latest_ack": self.latest_ack.get(robot_id),
                }
            )
        return out

    def get_robot_latest(self, robot_id: str) -> dict[str, Any] | None:
        telemetry = self.latest_telemetry.get(robot_id)
        if telemetry is None:
            return None
        return {
            "telemetry": telemetry.model_dump(),
            "latest_ack": self.latest_ack.get(robot_id),
        }

    def create_mission(self, mission: MissionState) -> None:
        self.active_missions[mission.mission_id] = mission

    def get_mission(self, mission_id: str) -> MissionState | None:
        return self.active_missions.get(mission_id)

    def update_mission_status(self, mission_id: str, status: str, metadata: dict[str, Any] | None = None) -> MissionState | None:
        mission = self.active_missions.get(mission_id)
        if mission is None:
            return None
        mission.status = status  # type: ignore[assignment]
        mission.updated_at = int(time.time())
        if metadata:
            mission.metadata.update(metadata)
        return mission
