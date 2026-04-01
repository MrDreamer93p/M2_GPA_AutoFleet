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
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_mqtt_message
        self._client.reconnect_delay_set(min_delay=1, max_delay=8)
        self._lock = threading.Lock()
        self._connected = False

    def connect(self) -> None:
        self._client.connect(settings.mqtt_host, settings.mqtt_port, settings.mqtt_keepalive)
        self._client.loop_start()

    def disconnect(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()

    def is_connected(self) -> bool:
        return self._connected

    def _subscribe(self, client: mqtt.Client, suffix: str, qos: int = 1) -> None:
        client.subscribe(f"{settings.topic_prefix}/{suffix}/+", qos=qos)

    def _on_connect(self, client: mqtt.Client, _: Any, __: Any, rc: int, ___: Any = None) -> None:
        self._connected = rc == 0
        if not self._connected:
            return
        self._subscribe(client, "telemetry", qos=0)
        self._subscribe(client, "event", qos=1)
        self._subscribe(client, "ack", qos=1)
        self._subscribe(client, "mission", qos=1)
        self._subscribe(client, "heartbeat", qos=0)
        self._subscribe(client, "video_status", qos=0)
        self._subscribe(client, "perception", qos=1)
        self._subscribe(client, "alert", qos=1)
        self._subscribe(client, "map", qos=1)
        self._subscribe(client, "coordination", qos=1)

    def _on_disconnect(self, *_: Any) -> None:
        self._connected = False

    def _on_mqtt_message(self, _: mqtt.Client, __: Any, msg: mqtt.MQTTMessage) -> None:
        topic = msg.topic
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except json.JSONDecodeError:
            return
        self._on_message(topic, payload)

    def publish(self, topic: str, payload: dict[str, Any], qos: int = 1, retain: bool = False) -> None:
        with self._lock:
            self._client.publish(topic, json.dumps(payload, ensure_ascii=True), qos=qos, retain=retain)

