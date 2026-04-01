from __future__ import annotations

import threading
import time
import uuid
from typing import Any

from .config import settings
from .models import (
    AlertEvent,
    CommandEnvelope,
    CoordinationSummary,
    FormationState,
    Heartbeat,
    MapSummary,
    MissionState,
)
from .mqtt_bridge import MqttBridge
from .postgres_store import PostgresStore
from .state import RuntimeState
from .storage import JsonlStore


def now_ts() -> int:
    return int(time.time())


class AppState:
    def __init__(self) -> None:
        self.runtime = RuntimeState()
        self.store = JsonlStore()
        self.pg = PostgresStore()
        self.mqtt = MqttBridge(on_message=self.on_mqtt_message)
        self._hb_stop = threading.Event()
        self._hb_thread: threading.Thread | None = None

    @property
    def topic_prefix(self) -> str:
        return settings.topic_prefix

    def _append_event(
        self,
        stream_name: str,
        payload: dict[str, Any],
        *,
        topic: str | None = None,
        event_type: str | None = None,
        robot_id: str | None = None,
        mission_id: str | None = None,
        ts: int | None = None,
    ) -> None:
        self.store.append(stream_name, payload)
        self.pg.append_event(
            stream_name,
            payload,
            topic=topic,
            event_type=event_type,
            robot_id=robot_id,
            mission_id=mission_id,
            ts=ts,
        )

    def _heartbeat_loop(self) -> None:
        interval_s = max(0.5, settings.heartbeat_interval_ms / 1000)
        while not self._hb_stop.wait(interval_s):
            heartbeat = Heartbeat(
                source_id=settings.backend_service_id,
                source_type="backend",
                status="OK",
                ts=now_ts(),
                meta={
                    "mqtt_connected": self.mqtt.is_connected(),
                    "pending_commands": len(self.runtime.pending_commands),
                },
            )
            topic = f"{self.topic_prefix}/heartbeat/{settings.backend_service_id}"
            self.mqtt.publish(topic, heartbeat.model_dump(), qos=0)
            self.runtime.upsert_heartbeat(heartbeat.model_dump())
            self.pg.upsert_heartbeat(
                heartbeat.source_id,
                heartbeat.source_type,
                heartbeat.robot_id,
                heartbeat.status,
                heartbeat.ts,
                heartbeat.model_dump(),
            )

    def start_background(self) -> None:
        if self._hb_thread and self._hb_thread.is_alive():
            return
        self._hb_stop.clear()
        self._hb_thread = threading.Thread(target=self._heartbeat_loop, daemon=True, name="backend-heartbeat")
        self._hb_thread.start()

    def stop_background(self) -> None:
        self._hb_stop.set()
        if self._hb_thread and self._hb_thread.is_alive():
            self._hb_thread.join(timeout=1.5)
        self._hb_thread = None

    def on_mqtt_message(self, topic: str, payload: dict[str, Any]) -> None:
        if "/telemetry/" in topic:
            telemetry = self.runtime.upsert_telemetry(payload)
            dump = telemetry.model_dump()
            self._append_event(
                f"telemetry_{telemetry.robot_id}",
                dump,
                topic=topic,
                event_type="telemetry",
                robot_id=telemetry.robot_id,
                mission_id=telemetry.mission_id,
                ts=telemetry.ts,
            )
            self.pg.upsert_robot_latest(telemetry.robot_id, dump, telemetry.state, telemetry.ts)
            if telemetry.map_summary:
                map_dump = telemetry.map_summary.model_dump()
                self.pg.upsert_map_summary(
                    telemetry.robot_id,
                    telemetry.map_summary.ts,
                    telemetry.map_summary.obstacle_count,
                    telemetry.map_summary.risk_level,
                    map_dump,
                )
            return
        if "/ack/" in topic:
            ack = self.runtime.upsert_ack(payload)
            robot_id = str(ack.get("robot_id", "unknown"))
            self._append_event(
                f"ack_{robot_id}",
                ack,
                topic=topic,
                event_type="ack",
                robot_id=robot_id,
                ts=int(ack.get("ts", now_ts())),
            )
            return
        if "/heartbeat/" in topic:
            heartbeat = self.runtime.upsert_heartbeat(payload)
            hb_dump = heartbeat.model_dump()
            self._append_event(
                f"heartbeat_{heartbeat.source_id}",
                hb_dump,
                topic=topic,
                event_type="heartbeat",
                robot_id=heartbeat.robot_id,
                ts=heartbeat.ts,
            )
            self.pg.upsert_heartbeat(heartbeat.source_id, heartbeat.source_type, heartbeat.robot_id, heartbeat.status, heartbeat.ts, hb_dump)
            return
        if "/video_status/" in topic:
            status = self.runtime.upsert_video_status(payload)
            dump = status.model_dump()
            self._append_event(
                f"video_status_{status.robot_id}",
                dump,
                topic=topic,
                event_type="video_status",
                robot_id=status.robot_id,
                ts=status.ts,
            )
            self.pg.upsert_video_stream(status.robot_id, status.ts, status.status, status.proxy_url, status.snapshot_url, dump)
            return
        if "/perception/" in topic:
            summary = self.runtime.upsert_perception(payload)
            dump = summary.model_dump()
            self._append_event(
                f"perception_{summary.robot_id}",
                dump,
                topic=topic,
                event_type="perception",
                robot_id=summary.robot_id,
                ts=summary.ts,
            )
            self.pg.upsert_perception(summary.robot_id, summary.ts, summary.risk_level, dump)
            return
        if "/alert/" in topic:
            alert = self.runtime.upsert_alert(payload)
            dump = alert.model_dump()
            self._append_event(
                f"alert_{alert.robot_id}",
                dump,
                topic=topic,
                event_type="alert",
                robot_id=alert.robot_id,
                ts=alert.ts,
            )
            self.pg.upsert_alert(alert.alert_id, alert.robot_id, alert.alert_type, alert.severity, alert.status, alert.ts, dump)
            return
        if "/map/" in topic:
            summary = self.runtime.upsert_map_summary(payload)
            dump = summary.model_dump()
            self._append_event(
                f"map_{summary.robot_id}",
                dump,
                topic=topic,
                event_type="map",
                robot_id=summary.robot_id,
                ts=summary.ts,
            )
            self.pg.upsert_map_summary(summary.robot_id, summary.ts, summary.obstacle_count, summary.risk_level, dump)
            return
        if "/coordination/" in topic:
            robot_id = str(payload.get("robot_id", "unknown"))
            ts = int(payload.get("ts", now_ts()))
            self._append_event(
                f"coordination_{robot_id}",
                payload,
                topic=topic,
                event_type="coordination",
                robot_id=robot_id,
                ts=ts,
            )
            collision_risk = str(payload.get("collision_risk", "NONE"))
            self.pg.upsert_coordination(robot_id, ts, collision_risk, payload)
            return
        if "/event/" in topic:
            robot_id = str(payload.get("robot_id", "unknown"))
            self._append_event(
                f"event_{robot_id}",
                payload,
                topic=topic,
                event_type="event",
                robot_id=robot_id,
                mission_id=str(payload.get("mission_id", "")) or None,
                ts=int(payload.get("ts", now_ts())),
            )
            return
        if "/mission/" in topic:
            mission_id = str(payload.get("mission_id", "unknown"))
            self._append_event(
                f"mission_{mission_id}",
                payload,
                topic=topic,
                event_type="mission",
                mission_id=mission_id,
                ts=int(payload.get("ts", now_ts())),
            )

    def build_command(
        self,
        robot_id: str,
        kind: str,
        args: dict[str, Any],
        ttl_ms: int,
        *,
        attempt: int = 1,
        correlation_id: str | None = None,
    ) -> CommandEnvelope:
        return CommandEnvelope(
            cmd_id=f"cmd-{uuid.uuid4().hex[:10]}",
            robot_id=robot_id,
            type=kind,
            args=args,
            ttl_ms=ttl_ms,
            sent_ts=now_ts(),
            attempt=attempt,
            correlation_id=correlation_id or f"corr-{uuid.uuid4().hex[:8]}",
        )

    def publish_command(self, command: CommandEnvelope) -> None:
        topic = f"{self.topic_prefix}/cmd/{command.robot_id}"
        payload = command.model_dump()
        self.runtime.mark_command_sent(payload)
        self.mqtt.publish(topic, payload, qos=1)
        self._append_event(
            f"command_{command.robot_id}",
            payload,
            topic=topic,
            event_type="command",
            robot_id=command.robot_id,
            ts=command.sent_ts,
        )

    def _publish_protocol_alert(self, *, robot_id: str, alert_type: str, severity: str, message: str, metadata: dict[str, Any]) -> AlertEvent:
        telem = self.runtime.latest_telemetry.get(robot_id)
        alert = AlertEvent(
            alert_id=f"alert-{uuid.uuid4().hex[:12]}",
            robot_id=robot_id,
            ts=now_ts(),
            alert_type=alert_type,
            severity=severity,
            message=message,
            source="backend",
            position=telem.pose if telem else None,
            metadata=metadata,
        )
        topic = f"{self.topic_prefix}/alert/{robot_id}"
        payload = alert.model_dump()
        self.mqtt.publish(topic, payload, qos=1)
        self.runtime.upsert_alert(payload)
        self.pg.upsert_alert(alert.alert_id, alert.robot_id, alert.alert_type, alert.severity, alert.status, alert.ts, payload)
        self._append_event(
            f"alert_{robot_id}",
            payload,
            topic=topic,
            event_type="alert",
            robot_id=robot_id,
            ts=alert.ts,
        )
        return alert

    def reconcile_protocol(self) -> None:
        expired = self.runtime.expired_commands()
        for item in expired:
            robot_id = str(item.get("robot_id", "unknown"))
            command = dict(item.get("command") or {})
            attempt = int(item.get("attempt", 1))
            command_type = str(item.get("type", ""))
            if attempt <= settings.command_retry_limit and command_type not in {"TELEOP", "FOLLOW_LEADER_INPUT"}:
                retry_command = self.build_command(
                    robot_id=robot_id,
                    kind=command_type,
                    args=dict(command.get("args") or {}),
                    ttl_ms=int(item.get("ttl_ms", 0) or 0),
                    attempt=attempt + 1,
                    correlation_id=command.get("correlation_id"),
                )
                self.publish_command(retry_command)
                self._append_event(
                    f"retry_{robot_id}",
                    retry_command.model_dump(),
                    event_type="command_retry",
                    robot_id=robot_id,
                    ts=retry_command.sent_ts,
                )
                continue

            timeout_ack = {
                "cmd_id": item["cmd_id"],
                "robot_id": robot_id,
                "status": "TIMEOUT",
                "ts": now_ts(),
                "attempt": attempt,
                "correlation_id": item.get("correlation_id"),
                "error_code": "COMMAND_TIMEOUT",
                "error_message": f"No ACK received within {settings.command_timeout_ms} ms",
            }
            self.on_mqtt_message(f"{self.topic_prefix}/ack/{robot_id}", timeout_ack)
            self._publish_protocol_alert(
                robot_id=robot_id,
                alert_type="COMMAND_TIMEOUT",
                severity="warning",
                message=f"Command {item['cmd_id']} timed out for {robot_id}",
                metadata={"cmd_id": item["cmd_id"], "command_type": command_type, "attempt": attempt},
            )

    def create_mission(self, mission_id: str, robots: list[str], metadata: dict[str, Any]) -> MissionState:
        now = now_ts()
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
        self._append_event(
            f"mission_{mission_id}",
            {"mission_id": mission_id, "status": "PLANNING", "robots": robots, "metadata": metadata, "ts": now},
            event_type="mission",
            mission_id=mission_id,
            ts=now,
        )
        self.pg.upsert_mission(mission_id, mission.status, mission.updated_at, mission.model_dump())
        return mission

    def update_mission(self, mission_id: str, status: str, metadata: dict[str, Any] | None = None) -> MissionState | None:
        mission = self.runtime.update_mission_status(mission_id, status, metadata)
        if mission is None:
            return None
        self._append_event(
            f"mission_{mission_id}",
            {"mission_id": mission_id, "status": status, "metadata": metadata or {}, "ts": now_ts()},
            event_type="mission",
            mission_id=mission_id,
            ts=mission.updated_at,
        )
        self.pg.upsert_mission(mission_id, mission.status, mission.updated_at, mission.model_dump())
        return mission

    def start_follow_formation(self, leader_id: str, follower_ids: list[str]) -> FormationState:
        formation = self.runtime.start_follow_formation(leader_id=leader_id, follower_ids=follower_ids)
        now = now_ts()
        self._append_event(
            "formation",
            {
                "status": "STARTED",
                "leader_id": formation.leader_id,
                "follower_ids": formation.follower_ids,
                "ts": now,
            },
            event_type="formation",
            robot_id=leader_id,
            ts=now,
        )
        for follower_id in formation.follower_ids:
            envelope = self.build_command(
                robot_id=follower_id,
                kind="SET_MODE",
                args={"mode": "FOLLOW_LEADER", "leader_id": leader_id},
                ttl_ms=2_000,
            )
            self.publish_command(envelope)
        return formation

    def stop_follow_formation(self) -> FormationState:
        previous = self.runtime.get_formation()
        formation = self.runtime.stop_follow_formation()
        now = now_ts()
        self._append_event(
            "formation",
            {"status": "STOPPED", "follower_ids": previous.follower_ids, "ts": now},
            event_type="formation",
            robot_id=previous.leader_id,
            ts=now,
        )
        for follower_id in previous.follower_ids:
            envelope = self.build_command(robot_id=follower_id, kind="SET_MODE", args={"mode": "AUTO"}, ttl_ms=2_000)
            self.publish_command(envelope)
        return formation

    def get_formation(self) -> FormationState:
        return self.runtime.get_formation()

    def publish_teleop(self, robot_id: str, linear_x: float, angular_z: float, ttl_ms: int = 300) -> dict[str, Any]:
        sent_robot_ids: list[str] = []
        leader_cmd = self.build_command(
            robot_id=robot_id,
            kind="TELEOP",
            args={"linear_x": linear_x, "angular_z": angular_z},
            ttl_ms=ttl_ms,
        )
        self.publish_command(leader_cmd)
        sent_robot_ids.append(robot_id)

        followers = self.runtime.followers_for_leader(robot_id)
        for follower_id in followers:
            follower_cmd = self.build_command(
                robot_id=follower_id,
                kind="FOLLOW_LEADER_INPUT",
                args={"leader_id": robot_id, "linear_x": linear_x, "angular_z": angular_z},
                ttl_ms=ttl_ms,
            )
            self.publish_command(follower_cmd)
            sent_robot_ids.append(follower_id)

        return {
            "leader_id": robot_id,
            "followers": followers,
            "linear_x": linear_x,
            "angular_z": angular_z,
            "ttl_ms": ttl_ms,
            "sent_robot_ids": sent_robot_ids,
        }

    def acknowledge_alert(self, alert_id: str, status: str) -> dict[str, Any] | None:
        updated = self.runtime.update_alert_status(alert_id, status)
        if updated is None:
            return None
        payload = updated.model_dump()
        self.pg.upsert_alert(updated.alert_id, updated.robot_id, updated.alert_type, updated.severity, updated.status, updated.ts, payload)
        self._append_event(
            f"alert_{updated.robot_id}",
            payload,
            event_type="alert_status",
            robot_id=updated.robot_id,
            ts=updated.ts,
        )
        return payload

    def protocol_spec(self) -> dict[str, Any]:
        return {
            "schema_version": settings.protocol_schema_version,
            "topic_prefix": self.topic_prefix,
            "topics": {
                "commands": {"pattern": f"{self.topic_prefix}/cmd/{{robot_id}}", "qos": 1, "schema": "autofleet.command.v1"},
                "telemetry": {"pattern": f"{self.topic_prefix}/telemetry/{{robot_id}}", "qos": 0, "schema": "autofleet.telemetry.v1"},
                "ack": {"pattern": f"{self.topic_prefix}/ack/{{robot_id}}", "qos": 1, "schema": "autofleet.ack.v1"},
                "heartbeat": {"pattern": f"{self.topic_prefix}/heartbeat/{{source_id}}", "qos": 0, "schema": "autofleet.heartbeat.v1"},
                "video_status": {"pattern": f"{self.topic_prefix}/video_status/{{robot_id}}", "qos": 0, "schema": "autofleet.video_status.v1"},
                "perception": {"pattern": f"{self.topic_prefix}/perception/{{robot_id}}", "qos": 1, "schema": "autofleet.perception.v1"},
                "alert": {"pattern": f"{self.topic_prefix}/alert/{{robot_id}}", "qos": 1, "schema": "autofleet.alert.v1"},
                "map": {"pattern": f"{self.topic_prefix}/map/{{robot_id}}", "qos": 1, "schema": "autofleet.map.v1"},
                "coordination": {"pattern": f"{self.topic_prefix}/coordination/{{robot_id}}", "qos": 1, "schema": "autofleet.coordination.v1"},
            },
            "reliability": {
                "command_timeout_ms": settings.command_timeout_ms,
                "command_retry_limit": settings.command_retry_limit,
                "heartbeat_interval_ms": settings.heartbeat_interval_ms,
                "robot_timeout_seconds": settings.robot_timeout_seconds,
            },
            "error_codes": [
                "COMMAND_TIMEOUT",
                "STREAM_UNAVAILABLE",
                "PERCEPTION_DEGRADED",
                "COLLISION_RISK_HIGH",
            ],
        }

