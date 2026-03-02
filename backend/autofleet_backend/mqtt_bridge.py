from __future__ import annotations

import json
import threading
from typing import Any, Callable

import paho.mqtt.client as mqtt

from .config import settings


MessageHandler = Callable[[str, dict[str, Any]], None]


class MqttBridge:
    def __init__(self, on_message: MessageHandler) -> None:
        self._on_message = on_message
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_mqtt_message
        self._lock = threading.Lock()
        self._connected = False

    def connect(self) -> None:
        self._client.connect(settings.mqtt_host, settings.mqtt_port, settings.mqtt_keepalive)
        self._client.loop_start()

    def disconnect(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()

    def _on_connect(self, client: mqtt.Client, _: Any, __: Any, rc: int, ___: Any = None) -> None:
        self._connected = rc == 0
        if not self._connected:
            return
        client.subscribe(f"{settings.topic_prefix}/telemetry/+")
        client.subscribe(f"{settings.topic_prefix}/event/+")
        client.subscribe(f"{settings.topic_prefix}/ack/+")
        client.subscribe(f"{settings.topic_prefix}/mission/+")

    def _on_mqtt_message(self, _: mqtt.Client, __: Any, msg: mqtt.MQTTMessage) -> None:
        topic = msg.topic
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except json.JSONDecodeError:
            return
        self._on_message(topic, payload)

    def publish(self, topic: str, payload: dict[str, Any]) -> None:
        with self._lock:
            self._client.publish(topic, json.dumps(payload, ensure_ascii=True), qos=1)
