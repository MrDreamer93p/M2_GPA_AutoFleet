from __future__ import annotations

import time
import uuid
from typing import Any

from .config import settings
from .models import CommandEnvelope, MissionState
from .mqtt_bridge import MqttBridge
from .state import RuntimeState
from .storage import JsonlStore


class AppState:
    def __init__(self) -> None:
        self.runtime = RuntimeState()
        self.store = JsonlStore()
        self.mqtt = MqttBridge(on_message=self.on_mqtt_message)

    @property
    def topic_prefix(self) -> str:
        return settings.topic_prefix

    def on_mqtt_message(self, topic: str, payload: dict[str, Any]) -> None:
        if "/telemetry/" in topic:
            telemetry = self.runtime.upsert_telemetry(payload)
            self.store.append(f"telemetry_{telemetry.robot_id}", telemetry.model_dump())
            return
        if "/ack/" in topic:
            self.runtime.upsert_ack(payload)
            robot_id = str(payload.get("robot_id", "unknown"))
            self.store.append(f"ack_{robot_id}", payload)
            return
        if "/event/" in topic:
            robot_id = str(payload.get("robot_id", "unknown"))
            self.store.append(f"event_{robot_id}", payload)
            return
        if "/mission/" in topic:
            mission_id = str(payload.get("mission_id", "unknown"))
            self.store.append(f"mission_{mission_id}", payload)

    def build_command(self, robot_id: str, kind: str, args: dict[str, Any], ttl_ms: int) -> CommandEnvelope:
        return CommandEnvelope(
            cmd_id=f"cmd-{uuid.uuid4().hex[:10]}",
            robot_id=robot_id,
            type=kind,
            args=args,
            ttl_ms=ttl_ms,
        )

    def publish_command(self, command: CommandEnvelope) -> None:
        topic = f"{self.topic_prefix}/cmd/{command.robot_id}"
        self.mqtt.publish(topic, command.model_dump())
        self.store.append(f"command_{command.robot_id}", command.model_dump())

    def create_mission(self, mission_id: str, robots: list[str], metadata: dict[str, Any]) -> MissionState:
        now = int(time.time())
        mission = MissionState(
            mission_id=mission_id,
            status="PLANNING",
            robots=robots,
            created_at=now,
            updated_at=now,
            metadata=metadata,
        )
        self.runtime.create_mission(mission)
        self.store.init_mission_result(mission_id)
        self.store.append(
            f"mission_{mission_id}",
            {"mission_id": mission_id, "status": "PLANNING", "robots": robots, "metadata": metadata, "ts": now},
        )
        return mission

    def update_mission(self, mission_id: str, status: str, metadata: dict[str, Any] | None = None) -> MissionState | None:
        mission = self.runtime.update_mission_status(mission_id, status, metadata)
        if mission is None:
            return None
        self.store.append(
            f"mission_{mission_id}",
            {"mission_id": mission_id, "status": status, "metadata": metadata or {}, "ts": int(time.time())},
        )
        return mission
