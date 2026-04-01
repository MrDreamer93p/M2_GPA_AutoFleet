from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    mqtt_host: str = os.getenv("AUTOFLEET_MQTT_HOST", "127.0.0.1")
    mqtt_port: int = int(os.getenv("AUTOFLEET_MQTT_PORT", "3889"))
    mqtt_keepalive: int = int(os.getenv("AUTOFLEET_MQTT_KEEPALIVE", "30"))
    topic_prefix: str = os.getenv("AUTOFLEET_TOPIC_PREFIX", "fleet/v1")
    robot_timeout_seconds: int = int(os.getenv("AUTOFLEET_ROBOT_TIMEOUT_SECONDS", "6"))
    service_timeout_seconds: int = int(os.getenv("AUTOFLEET_SERVICE_TIMEOUT_SECONDS", "10"))
    log_dir: str = os.getenv("AUTOFLEET_LOG_DIR", "../data/logs")
    result_dir: str = os.getenv("AUTOFLEET_RESULT_DIR", "../data/results")
    artifact_dir: str = os.getenv("AUTOFLEET_ARTIFACT_DIR", "../data/artifacts")
    database_dsn: str = os.getenv("AUTOFLEET_DATABASE_DSN", "")
    command_timeout_ms: int = int(os.getenv("AUTOFLEET_COMMAND_TIMEOUT_MS", "1200"))
    command_retry_limit: int = int(os.getenv("AUTOFLEET_COMMAND_RETRY_LIMIT", "1"))
    heartbeat_interval_ms: int = int(os.getenv("AUTOFLEET_HEARTBEAT_INTERVAL_MS", "2000"))
    telemetry_history_limit: int = int(os.getenv("AUTOFLEET_TELEMETRY_HISTORY_LIMIT", "120"))
    recent_alert_limit: int = int(os.getenv("AUTOFLEET_RECENT_ALERT_LIMIT", "100"))
    recent_event_limit: int = int(os.getenv("AUTOFLEET_RECENT_EVENT_LIMIT", "200"))
    protocol_schema_version: str = os.getenv("AUTOFLEET_PROTOCOL_SCHEMA_VERSION", "1.1")
    video_public_base: str = os.getenv("AUTOFLEET_VIDEO_PUBLIC_BASE", "http://127.0.0.1:8400")
    video_worker_base: str = os.getenv("AUTOFLEET_VIDEO_WORKER_BASE", "http://video-worker:8090")
    video_snapshot_dir: str = os.getenv("AUTOFLEET_VIDEO_SNAPSHOT_DIR", "../data/artifacts/snapshots")
    alert_snapshot_dir: str = os.getenv("AUTOFLEET_ALERT_SNAPSHOT_DIR", "../data/artifacts/alerts")
    collision_warning_distance_m: float = float(os.getenv("AUTOFLEET_COLLISION_WARNING_DISTANCE_M", "1.2"))
    collision_critical_distance_m: float = float(os.getenv("AUTOFLEET_COLLISION_CRITICAL_DISTANCE_M", "0.6"))
    backend_service_id: str = os.getenv("AUTOFLEET_BACKEND_SERVICE_ID", "backend")


settings = Settings()
