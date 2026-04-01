from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from .config import settings
from .models import (
    Ack,
    AlertEvent,
    CoordinationSummary,
    FormationState,
    Heartbeat,
    MapSummary,
    MissionState,
    NeighborState,
    PerceptionSummary,
    Telemetry,
    VideoStreamStatus,
)


def _risk_from_distance(distance: float) -> str:
    if distance <= settings.collision_critical_distance_m:
        return "CRITICAL"
    if distance <= settings.collision_warning_distance_m:
        return "HIGH"
    if distance <= settings.collision_warning_distance_m * 1.6:
        return "MEDIUM"
    return "NONE"


@dataclass
class RuntimeState:
    latest_telemetry: dict[str, Telemetry] = field(default_factory=dict)
    telemetry_history: dict[str, deque[Telemetry]] = field(default_factory=dict)
    latest_ack: dict[str, dict[str, Any]] = field(default_factory=dict)
    latest_perception: dict[str, PerceptionSummary] = field(default_factory=dict)
    latest_video_status: dict[str, VideoStreamStatus] = field(default_factory=dict)
    latest_map_summary: dict[str, MapSummary] = field(default_factory=dict)
    latest_heartbeats: dict[str, Heartbeat] = field(default_factory=dict)
    service_heartbeats: dict[str, Heartbeat] = field(default_factory=dict)
    active_missions: dict[str, MissionState] = field(default_factory=dict)
    formation: FormationState = field(default_factory=FormationState)
    pending_commands: dict[str, dict[str, Any]] = field(default_factory=dict)
    recent_alerts: deque[AlertEvent] = field(default_factory=lambda: deque(maxlen=settings.recent_alert_limit))
    alerts_by_id: dict[str, AlertEvent] = field(default_factory=dict)
    recent_events: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=settings.recent_event_limit))

    def _append_event(self, event_type: str, payload: dict[str, Any], *, robot_id: str | None = None, mission_id: str | None = None) -> None:
        self.recent_events.appendleft(
            {
                "event_type": event_type,
                "robot_id": robot_id,
                "mission_id": mission_id,
                "ts": int(time.time()),
                "payload": payload,
            }
        )

    def upsert_telemetry(self, payload: dict[str, Any]) -> Telemetry:
        telemetry = Telemetry.model_validate({**payload, "raw": payload})
        self.latest_telemetry[telemetry.robot_id] = telemetry
        history = self.telemetry_history.setdefault(telemetry.robot_id, deque(maxlen=settings.telemetry_history_limit))
        history.append(telemetry)
        if telemetry.map_summary:
            self.latest_map_summary[telemetry.robot_id] = telemetry.map_summary
        self._append_event("telemetry", telemetry.model_dump(), robot_id=telemetry.robot_id, mission_id=telemetry.mission_id)
        return telemetry

    def mark_command_sent(self, command: dict[str, Any]) -> None:
        cmd_id = str(command.get("cmd_id"))
        self.pending_commands[cmd_id] = {
            "cmd_id": cmd_id,
            "robot_id": command.get("robot_id"),
            "command": command,
            "sent_ms": int(time.time() * 1000),
            "attempt": int(command.get("attempt", 1)),
            "type": command.get("type"),
            "ttl_ms": int(command.get("ttl_ms", 0)),
            "correlation_id": command.get("correlation_id"),
        }
        self._append_event("command_sent", command, robot_id=str(command.get("robot_id", "")))

    def expired_commands(self) -> list[dict[str, Any]]:
        now_ms = int(time.time() * 1000)
        expired: list[dict[str, Any]] = []
        for cmd_id, pending in list(self.pending_commands.items()):
            if now_ms - int(pending["sent_ms"]) < settings.command_timeout_ms:
                continue
            expired.append(self.pending_commands.pop(cmd_id))
        return expired

    def upsert_ack(self, payload: dict[str, Any]) -> dict[str, Any]:
        ack = Ack.model_validate(payload).model_dump()
        cmd_id = str(ack.get("cmd_id", ""))
        pending = self.pending_commands.pop(cmd_id, None) if cmd_id else None
        if pending is not None:
            ack["rtt_ms"] = max(0, int(time.time() * 1000) - int(pending["sent_ms"]))
            if ack.get("attempt") is None:
                ack["attempt"] = pending.get("attempt")
        robot_id = str(ack.get("robot_id", "unknown"))
        self.latest_ack[robot_id] = ack
        self._append_event("ack", ack, robot_id=robot_id)
        return ack

    def upsert_heartbeat(self, payload: dict[str, Any]) -> Heartbeat:
        heartbeat = Heartbeat.model_validate(payload)
        if heartbeat.source_type == "robot":
            target_key = heartbeat.robot_id or heartbeat.source_id
            self.latest_heartbeats[target_key] = heartbeat
            self._append_event("heartbeat_robot", heartbeat.model_dump(), robot_id=target_key)
        else:
            self.service_heartbeats[heartbeat.source_id] = heartbeat
            self._append_event("heartbeat_service", heartbeat.model_dump(), robot_id=heartbeat.robot_id)
        return heartbeat

    def upsert_video_status(self, payload: dict[str, Any]) -> VideoStreamStatus:
        status = VideoStreamStatus.model_validate(payload)
        self.latest_video_status[status.robot_id] = status
        self._append_event("video_status", status.model_dump(), robot_id=status.robot_id)
        return status

    def upsert_perception(self, payload: dict[str, Any]) -> PerceptionSummary:
        summary = PerceptionSummary.model_validate(payload)
        self.latest_perception[summary.robot_id] = summary
        self._append_event("perception", summary.model_dump(), robot_id=summary.robot_id)
        return summary

    def upsert_map_summary(self, payload: dict[str, Any]) -> MapSummary:
        summary = MapSummary.model_validate(payload)
        self.latest_map_summary[summary.robot_id] = summary
        self._append_event("map", summary.model_dump(), robot_id=summary.robot_id)
        return summary

    def upsert_alert(self, payload: dict[str, Any]) -> AlertEvent:
        alert = AlertEvent.model_validate(payload)
        self.alerts_by_id[alert.alert_id] = alert
        self.recent_alerts.appendleft(alert)
        self._append_event("alert", alert.model_dump(), robot_id=alert.robot_id)
        return alert

    def update_alert_status(self, alert_id: str, status: str) -> AlertEvent | None:
        alert = self.alerts_by_id.get(alert_id)
        if alert is None:
            return None
        alert.status = status  # type: ignore[assignment]
        alert.metadata["status_updated_at"] = int(time.time())
        self.recent_alerts = deque(
            [self.alerts_by_id[a.alert_id] for a in self.recent_alerts if a.alert_id in self.alerts_by_id],
            maxlen=settings.recent_alert_limit,
        )
        self._append_event("alert_status", alert.model_dump(), robot_id=alert.robot_id)
        return alert

    def list_alerts(self, *, active_only: bool = False, robot_id: str | None = None) -> list[dict[str, Any]]:
        alerts = list(self.recent_alerts)
        if active_only:
            alerts = [a for a in alerts if a.status == "active"]
        if robot_id:
            alerts = [a for a in alerts if a.robot_id == robot_id]
        return [a.model_dump() for a in alerts]

    def list_events(self, limit: int = 100) -> list[dict[str, Any]]:
        return list(self.recent_events)[:limit]

    def get_recent_telemetry(self, robot_id: str) -> list[dict[str, Any]]:
        return [x.model_dump() for x in self.telemetry_history.get(robot_id, [])]

    def get_robot_latest(self, robot_id: str) -> dict[str, Any] | None:
        telemetry = self.latest_telemetry.get(robot_id)
        if telemetry is None:
            return None
        heartbeat = self.latest_heartbeats.get(robot_id)
        video_status = self.latest_video_status.get(robot_id)
        perception = self.latest_perception.get(robot_id)
        map_summary = self.latest_map_summary.get(robot_id)
        return {
            "telemetry": telemetry.model_dump(),
            "latest_ack": self.latest_ack.get(robot_id),
            "heartbeat": heartbeat.model_dump() if heartbeat else None,
            "video_status": video_status.model_dump() if video_status else None,
            "perception": perception.model_dump() if perception else None,
            "map_summary": map_summary.model_dump() if map_summary else None,
            "recent_alerts": self.list_alerts(robot_id=robot_id)[:10],
            "telemetry_history": self.get_recent_telemetry(robot_id),
        }

    def create_mission(self, mission: MissionState) -> None:
        self.active_missions[mission.mission_id] = mission
        self._append_event("mission_created", mission.model_dump(), mission_id=mission.mission_id)

    def list_missions(self) -> list[dict[str, Any]]:
        return [mission.model_dump() for mission in self.active_missions.values()]

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
        self._append_event("mission_status", mission.model_dump(), mission_id=mission_id)
        return mission

    def start_follow_formation(self, leader_id: str, follower_ids: list[str]) -> FormationState:
        unique_followers = sorted({rid for rid in follower_ids if rid and rid != leader_id})
        self.formation = FormationState(
            enabled=True,
            leader_id=leader_id,
            follower_ids=unique_followers,
            updated_at=int(time.time()),
        )
        self._append_event("formation_start", self.formation.model_dump(), robot_id=leader_id)
        return self.formation

    def stop_follow_formation(self) -> FormationState:
        previous = self.formation
        self.formation = FormationState(enabled=False, leader_id=None, follower_ids=[], updated_at=int(time.time()))
        self._append_event("formation_stop", previous.model_dump(), robot_id=previous.leader_id)
        return self.formation

    def get_formation(self) -> FormationState:
        return self.formation

    def followers_for_leader(self, leader_id: str) -> list[str]:
        if not self.formation.enabled or self.formation.leader_id != leader_id:
            return []
        return list(self.formation.follower_ids)

    def list_video_streams(self) -> list[dict[str, Any]]:
        robot_ids = sorted(set(self.latest_telemetry) | set(self.latest_video_status))
        out: list[dict[str, Any]] = []
        for robot_id in robot_ids:
            status = self.latest_video_status.get(robot_id)
            telem = self.latest_telemetry.get(robot_id)
            out.append(
                {
                    "robot_id": robot_id,
                    "status": status.status if status else "offline",
                    "source_url": (status.source_url if status else None) or (telem.video_rtsp_url if telem else None),
                    "proxy_url": status.proxy_url if status else None,
                    "snapshot_url": status.snapshot_url if status else None,
                    "fps": status.fps if status else None,
                    "bitrate_kbps": status.bitrate_kbps if status else None,
                    "codec": status.codec if status else None,
                    "note": status.note if status else None,
                }
            )
        return out

    def list_map_summaries(self) -> list[dict[str, Any]]:
        return [x.model_dump() for x in self.latest_map_summary.values()]

    def coordination_summaries(self) -> list[dict[str, Any]]:
        now = int(time.time())
        telemetry_items = list(self.latest_telemetry.items())
        out: list[CoordinationSummary] = []
        for robot_id, telem in telemetry_items:
            neighbors: list[NeighborState] = []
            min_distance: float | None = None
            for peer_id, peer_telem in telemetry_items:
                if peer_id == robot_id:
                    continue
                dx = peer_telem.pose.x - telem.pose.x
                dy = peer_telem.pose.y - telem.pose.y
                distance = math.sqrt(dx * dx + dy * dy)
                bearing = math.degrees(math.atan2(dy, dx))
                risk = _risk_from_distance(distance)
                neighbors.append(
                    NeighborState(
                        robot_id=peer_id,
                        distance_m=round(distance, 3),
                        bearing_deg=round(bearing, 1),
                        risk_level=risk,
                    )
                )
                min_distance = distance if min_distance is None else min(min_distance, distance)
            role = "independent"
            if self.formation.enabled and self.formation.leader_id == robot_id:
                role = "leader"
            elif self.formation.enabled and robot_id in self.formation.follower_ids:
                role = "follower"
            summary = CoordinationSummary(
                robot_id=robot_id,
                ts=now,
                leader_id=self.formation.leader_id if self.formation.enabled else None,
                role=role,
                min_peer_distance_m=round(min_distance, 3) if min_distance is not None else None,
                collision_risk=_risk_from_distance(min_distance) if min_distance is not None else "NONE",
                neighbors=sorted(neighbors, key=lambda n: n.distance_m),
            )
            out.append(summary)
        return [x.model_dump() for x in out]

    def protocol_status(self) -> dict[str, Any]:
        now = int(time.time())
        robot_heartbeats = {
            rid: max(0, now - hb.ts)
            for rid, hb in self.latest_heartbeats.items()
        }
        service_heartbeats = {
            sid: max(0, now - hb.ts)
            for sid, hb in self.service_heartbeats.items()
        }
        coordination = self.coordination_summaries()
        high_risk_pairs = sum(1 for item in coordination if item.get("collision_risk") in {"HIGH", "CRITICAL"})
        return {
            "schema_version": settings.protocol_schema_version,
            "topic_prefix": settings.topic_prefix,
            "pending_commands": len(self.pending_commands),
            "robot_heartbeats_age_s": robot_heartbeats,
            "service_heartbeats_age_s": service_heartbeats,
            "active_alerts": len([a for a in self.recent_alerts if a.status == "active"]),
            "high_collision_risk_robots": high_risk_pairs,
        }

    def list_robots(self) -> list[dict[str, Any]]:
        now = int(time.time())
        coordination_by_robot = {item["robot_id"]: item for item in self.coordination_summaries()}
        out: list[dict[str, Any]] = []
        for robot_id, telem in sorted(self.latest_telemetry.items()):
            last_seen_age = max(0, now - telem.ts)
            raw = telem.raw or {}
            heartbeat = self.latest_heartbeats.get(robot_id)
            heartbeat_age = max(0, now - heartbeat.ts) if heartbeat else None
            perception = self.latest_perception.get(robot_id)
            video_status = self.latest_video_status.get(robot_id)
            map_summary = self.latest_map_summary.get(robot_id)
            recent_alerts = self.list_alerts(active_only=True, robot_id=robot_id)[:3]
            out.append(
                {
                    "robot_id": robot_id,
                    "state": telem.state,
                    "battery": telem.battery,
                    "mission_id": telem.mission_id,
                    "video_rtsp_url": telem.video_rtsp_url,
                    "pose": telem.pose.model_dump(),
                    "controls": raw.get("controls") or (telem.controls.model_dump() if telem.controls else None),
                    "motors": raw.get("motors") or (telem.motors.model_dump() if telem.motors else None),
                    "network": raw.get("network") or (telem.network.model_dump() if telem.network else None),
                    "obstacle_summary": raw.get("obstacle_summary")
                    or (telem.obstacle_summary.model_dump() if telem.obstacle_summary else None),
                    "map_summary": map_summary.model_dump() if map_summary else None,
                    "latest_perception": perception.model_dump() if perception else None,
                    "video_status": video_status.model_dump() if video_status else None,
                    "control_rtt_ms": (self.latest_ack.get(robot_id) or {}).get("rtt_ms"),
                    "last_seen_ts": telem.ts,
                    "last_seen_age_s": last_seen_age,
                    "heartbeat_age_s": heartbeat_age,
                    "online": last_seen_age <= settings.robot_timeout_seconds
                    or (heartbeat_age is not None and heartbeat_age <= settings.robot_timeout_seconds),
                    "latest_ack": self.latest_ack.get(robot_id),
                    "recent_alerts": recent_alerts,
                    "coordination": coordination_by_robot.get(robot_id),
                }
            )
        return out

