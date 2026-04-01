from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import paho.mqtt.client as mqtt
import requests


MQTT_HOST = os.getenv("AUTOFLEET_MQTT_HOST", "mosquitto")
MQTT_PORT = int(os.getenv("AUTOFLEET_MQTT_PORT", "3889"))
MQTT_KEEPALIVE = int(os.getenv("AUTOFLEET_MQTT_KEEPALIVE", "30"))
TOPIC_PREFIX = os.getenv("AUTOFLEET_TOPIC_PREFIX", "fleet/v1")
VIDEO_WORKER_BASE = os.getenv("AUTOFLEET_VIDEO_WORKER_BASE", "http://video-worker:8090").rstrip("/")
ALERT_SNAPSHOT_DIR = Path(os.getenv("AUTOFLEET_ALERT_SNAPSHOT_DIR", "/artifacts/alerts"))
HEARTBEAT_INTERVAL_MS = int(os.getenv("AUTOFLEET_HEARTBEAT_INTERVAL_MS", "2000"))
ANALYSIS_INTERVAL_MS = int(os.getenv("AUTOFLEET_PERCEPTION_INTERVAL_MS", "2500"))

ALERT_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
stop_event = threading.Event()
worker_thread: threading.Thread | None = None
last_alert_signature: dict[str, str] = {}


def fetch_streams() -> list[dict[str, Any]]:
    try:
        res = requests.get(f"{VIDEO_WORKER_BASE}/streams", timeout=2.0)
        res.raise_for_status()
        return list(res.json().get("items") or [])
    except Exception:
        return []


def fetch_snapshot(url: str) -> np.ndarray | None:
    try:
        res = requests.get(url, timeout=3.0)
        res.raise_for_status()
        arr = np.frombuffer(res.content, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        return frame
    except Exception:
        return None


def risk_rank(level: str) -> int:
    return {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}.get(level, 0)


def merge_risk(a: str, b: str) -> str:
    return a if risk_rank(a) >= risk_rank(b) else b


def bbox_from_mask(mask: np.ndarray) -> dict[str, float] | None:
    ys, xs = np.where(mask > 0)
    if len(xs) == 0 or len(ys) == 0:
        return None
    x0, x1 = xs.min(), xs.max()
    y0, y1 = ys.min(), ys.max()
    h, w = mask.shape[:2]
    return {
        "x": round(float(x0) / max(1, w), 3),
        "y": round(float(y0) / max(1, h), 3),
        "w": round(float(x1 - x0) / max(1, w), 3),
        "h": round(float(y1 - y0) / max(1, h), 3),
    }


def analyze_frame(frame: np.ndarray, robot_id: str, snapshot_url: str | None, stream_status: str) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any]]:
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    detections: list[dict[str, Any]] = []
    risk_level = "NONE"

    red_a = cv2.inRange(hsv, np.array([0, 120, 80]), np.array([10, 255, 255]))
    red_b = cv2.inRange(hsv, np.array([160, 120, 80]), np.array([179, 255, 255]))
    red_mask = cv2.bitwise_or(red_a, red_b)
    red_ratio = float(np.count_nonzero(red_mask)) / float(red_mask.size)
    red_bbox = bbox_from_mask(red_mask)
    if red_ratio >= 0.035 and red_bbox:
        detections.append(
            {
                "label": "hazard_zone",
                "confidence": min(0.99, round(0.55 + red_ratio * 2.2, 3)),
                "severity": "critical",
                "bbox": red_bbox,
            }
        )
        risk_level = merge_risk(risk_level, "CRITICAL")

    edge_density = float(np.count_nonzero(cv2.Canny(gray, 80, 180))) / float(gray.size)
    if edge_density >= 0.08:
        detections.append(
            {
                "label": "obstacle_cluster",
                "confidence": min(0.95, round(0.45 + edge_density * 2.5, 3)),
                "severity": "warning",
            }
        )
        risk_level = merge_risk(risk_level, "MEDIUM")

    brightness = float(gray.mean())
    if brightness <= 48:
        detections.append(
            {
                "label": "low_light",
                "confidence": min(0.95, round(1 - brightness / 80, 3)),
                "severity": "warning",
            }
        )
        risk_level = merge_risk(risk_level, "LOW")

    if stream_status != "online":
        detections.append(
            {
                "label": "stream_degraded",
                "confidence": 0.92,
                "severity": "warning",
            }
        )
        risk_level = merge_risk(risk_level, "LOW")

    obstacle_count = len([d for d in detections if d["label"] in {"hazard_zone", "obstacle_cluster"}])
    note = "Frame analyzed from video-worker snapshot"
    if not detections:
        note = "No obstacle-like signal detected in the latest snapshot"

    perception = {
        "v": 1,
        "schema": "autofleet.perception.v1",
        "robot_id": robot_id,
        "ts": int(time.time()),
        "risk_level": risk_level,
        "obstacle_count": obstacle_count,
        "detections": detections,
        "snapshot_url": snapshot_url,
        "frame_source": "snapshot",
        "note": note,
    }

    alert = None
    if risk_rank(risk_level) >= risk_rank("HIGH"):
        alert = {
            "v": 1,
            "schema": "autofleet.alert.v1",
            "alert_id": f"alert-{uuid.uuid4().hex[:12]}",
            "robot_id": robot_id,
            "ts": int(time.time()),
            "alert_type": "VIDEO_HAZARD",
            "severity": "critical" if risk_level == "CRITICAL" else "warning",
            "status": "active",
            "message": f"Perception detected {obstacle_count or 1} high-risk item(s) for {robot_id}",
            "source": "perception-worker",
            "evidence_url": snapshot_url,
            "metadata": {"risk_level": risk_level, "detection_labels": [d["label"] for d in detections]},
        }

    map_summary = {
        "v": 1,
        "schema": "autofleet.map.v1",
        "robot_id": robot_id,
        "ts": int(time.time()),
        "obstacle_count": obstacle_count,
        "local_free_ratio": round(max(0.05, 1 - min(0.85, edge_density * 2 + red_ratio * 5)), 3),
        "risk_level": risk_level,
        "note": "Derived from 2D snapshot heuristics",
        "obstacles": [],
    }
    if red_bbox:
        map_summary["obstacles"].append(
            {
                "obstacle_id": f"obs-{robot_id}-1",
                "x": round(red_bbox["x"] * 5, 3),
                "y": round(red_bbox["y"] * 5, 3),
                "confidence": detections[0]["confidence"] if detections else 0.0,
                "label": "hazard_zone",
            }
        )
    return perception, alert, map_summary


def persist_alert_snapshot(robot_id: str, frame: np.ndarray, alert: dict[str, Any] | None) -> str | None:
    if alert is None:
        return None
    target = ALERT_SNAPSHOT_DIR / f"{robot_id}-{int(time.time())}.jpg"
    cv2.imwrite(str(target), frame)
    return target.name


def publish_heartbeat() -> None:
    payload = {
        "v": 1,
        "schema": "autofleet.heartbeat.v1",
        "source_id": "perception-worker",
        "source_type": "perception_worker",
        "status": "OK",
        "ts": int(time.time()),
        "meta": {"video_worker_base": VIDEO_WORKER_BASE},
    }
    client.publish(f"{TOPIC_PREFIX}/heartbeat/perception-worker", json.dumps(payload), qos=0)


def worker_loop() -> None:
    analysis_interval_s = max(0.8, ANALYSIS_INTERVAL_MS / 1000)
    heartbeat_interval_s = max(0.5, HEARTBEAT_INTERVAL_MS / 1000)
    last_hb = 0.0
    while not stop_event.wait(analysis_interval_s):
        now = time.time()
        if now - last_hb >= heartbeat_interval_s:
            publish_heartbeat()
            last_hb = now
        for stream in fetch_streams():
            robot_id = str(stream.get("robot_id", "")).strip()
            snapshot_url = stream.get("snapshot_url")
            if not robot_id or not snapshot_url:
                continue
            frame = fetch_snapshot(f"{VIDEO_WORKER_BASE}/snapshots/{robot_id}.jpg")
            if frame is None:
                continue
            perception, alert, map_summary = analyze_frame(frame, robot_id, snapshot_url, str(stream.get("status") or "offline"))
            signature = f"{perception['risk_level']}:{','.join(d['label'] for d in perception['detections'])}"
            client.publish(f"{TOPIC_PREFIX}/perception/{robot_id}", json.dumps(perception), qos=1)
            client.publish(f"{TOPIC_PREFIX}/map/{robot_id}", json.dumps(map_summary), qos=1)

            if alert is not None and last_alert_signature.get(robot_id) != signature:
                local_snapshot = persist_alert_snapshot(robot_id, frame, alert)
                if local_snapshot:
                    alert["evidence_url"] = snapshot_url
                    alert["metadata"]["alert_snapshot_name"] = local_snapshot
                client.publish(f"{TOPIC_PREFIX}/alert/{robot_id}", json.dumps(alert), qos=1)
                last_alert_signature[robot_id] = signature
            elif alert is None:
                last_alert_signature.pop(robot_id, None)


def main() -> None:
    global worker_thread
    stop_event.clear()
    client.connect(MQTT_HOST, MQTT_PORT, MQTT_KEEPALIVE)
    client.loop_start()
    worker_thread = threading.Thread(target=worker_loop, daemon=True, name="perception-worker-loop")
    worker_thread.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stop_event.set()
        if worker_thread and worker_thread.is_alive():
            worker_thread.join(timeout=1.5)
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
