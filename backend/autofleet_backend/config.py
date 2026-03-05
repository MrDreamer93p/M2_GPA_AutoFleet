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
    log_dir: str = os.getenv("AUTOFLEET_LOG_DIR", "../data/logs")
    result_dir: str = os.getenv("AUTOFLEET_RESULT_DIR", "../data/results")


settings = Settings()
