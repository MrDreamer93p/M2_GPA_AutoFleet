from __future__ import annotations

import json
import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import paho.mqtt.client as mqtt
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse


MQTT_HOST = os.getenv("AUTOFLEET_MQTT_HOST", "mosquitto")
MQTT_PORT = int(os.getenv("AUTOFLEET_MQTT_PORT", "3889"))
MQTT_KEEPALIVE = int(os.getenv("AUTOFLEET_MQTT_KEEPALIVE", "30"))
TOPIC_PREFIX = os.getenv("AUTOFLEET_TOPIC_PREFIX", "fleet/v1")
VIDEO_PUBLIC_BASE = os.getenv("AUTOFLEET_VIDEO_PUBLIC_BASE", "http://127.0.0.1:8400").rstrip("/")
SNAPSHOT_DIR = Path(os.getenv("AUTOFLEET_VIDEO_SNAPSHOT_DIR", "/artifacts/snapshots"))
HEARTBEAT_INTERVAL_MS = int(os.getenv("AUTOFLEET_HEARTBEAT_INTERVAL_MS", "2000"))

SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)


class StreamRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._robots: dict[str, dict[str, Any]] = {}

    def update_from_telemetry(self, payload: dict[str, Any]) -> None:
        robot_id = str(payload.get("robot_id", "")).strip()
        if not robot_id:
            return
        with self._lock:
            entry = self._robots.setdefault(robot_id, {})
            entry.update(
                {
                    "robot_id": robot_id,
                    "video_rtsp_url": payload.get("video_rtsp_url"),
                    "state": payload.get("state"),
                    "battery": payload.get("battery"),
                    "pose": payload.get("pose"),
                    "network": payload.get("network"),
                    "last_seen_ts": int(payload.get("ts", int(time.time()))),
                }
            )

    def update_status(self, robot_id: str, status: dict[str, Any]) -> None:
        with self._lock:
            entry = self._robots.setdefault(robot_id, {"robot_id": robot_id})
            entry["video_status"] = status

    def get(self, robot_id: str) -> dict[str, Any] | None:
        with self._lock:
            entry = self._robots.get(robot_id)
            return dict(entry) if entry else None

    def robot_ids(self) -> list[str]:
        with self._lock:
            return sorted(self._robots.keys())

    def list_streams(self) -> list[dict[str, Any]]:
        with self._lock:
            out: list[dict[str, Any]] = []
            for robot_id in sorted(self._robots.keys()):
                entry = self._robots[robot_id]
                status = dict(entry.get("video_status") or {})
                out.append(
                    {
                        "robot_id": robot_id,
                        "source_url": entry.get("video_rtsp_url"),
                        "state": entry.get("state"),
                        "status": status.get("status", "offline"),
                        "proxy_url": status.get("proxy_url"),
                        "snapshot_url": status.get("snapshot_url"),
                        "fps": status.get("fps"),
                        "bitrate_kbps": status.get("bitrate_kbps"),
                        "note": status.get("note"),
                    }
                )
            return out


class FrameProvider:
    def __init__(self, registry: StreamRegistry) -> None:
        self.registry = registry
        self._lock = threading.Lock()
        self._captures: dict[str, tuple[str, cv2.VideoCapture]] = {}

    def _release_capture(self, robot_id: str) -> None:
        with self._lock:
            existing = self._captures.pop(robot_id, None)
        if existing:
            _, cap = existing
            cap.release()

    def _get_capture(self, robot_id: str, source_url: str | None) -> cv2.VideoCapture | None:
        if not source_url or not source_url.lower().startswith("rtsp://"):
            return None
        with self._lock:
            existing = self._captures.get(robot_id)
            if existing and existing[0] == source_url and existing[1].isOpened():
                return existing[1]
        cap = cv2.VideoCapture(source_url)
        if not cap.isOpened():
            cap.release()
            return None
        with self._lock:
            previous = self._captures.pop(robot_id, None)
            if previous:
                previous[1].release()
            self._captures[robot_id] = (source_url, cap)
        return cap

    def _synthetic_frame(self, robot_id: str, entry: dict[str, Any]) -> np.ndarray:
        h, w = 360, 640
        img = np.zeros((h, w, 3), dtype=np.uint8)
        img[:] = (18, 22, 30)
        accent = ((hash(robot_id) >> 8) & 0x7F) + 100
        cv2.rectangle(img, (18, 18), (w - 18, h - 18), (accent, 160, 240), 2)
        cv2.putText(img, f"AUTOFLEET {robot_id}", (28, 56), cv2.FONT_HERSHEY_SIMPLEX, 1.05, (255, 255, 255), 2)
        cv2.putText(img, f"State: {entry.get('state') or 'UNKNOWN'}", (28, 98), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (160, 220, 255), 2)
        cv2.putText(img, f"Battery: {entry.get('battery', '-')}", (28, 132), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (190, 255, 180), 2)
        pose = entry.get("pose") or {}
        cv2.putText(
            img,
            f"Pose: {pose.get('x', '-')} , {pose.get('y', '-')} , {pose.get('yaw', '-')}",
            (28, 166),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.62,
            (220, 220, 220),
            2,
        )
        cv2.putText(img, time.strftime("%H:%M:%S"), (28, h - 26), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 215, 120), 2)

        t = int(time.time())
        center_x = 110 + (t * 13 + abs(hash(robot_id)) % 160) % (w - 180)
        center_y = 210 + int(34 * np.sin(t / 2))
        cv2.circle(img, (center_x, center_y), 26, (90, 200, 255), -1)
        cv2.putText(img, "TARGET", (center_x - 36, center_y + 48), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (120, 210, 255), 2)

        hazard_on = (t // 5 + abs(hash(robot_id))) % 4 == 0
        if hazard_on:
            cv2.rectangle(img, (w - 190, 72), (w - 40, 172), (0, 0, 255), -1)
            cv2.putText(img, "HAZARD", (w - 174, 128), cv2.FONT_HERSHEY_SIMPLEX, 0.95, (255, 255, 255), 2)
        else:
            cv2.rectangle(img, (w - 190, 72), (w - 40, 172), (20, 120, 30), -1)
            cv2.putText(img, "CLEAR", (w - 160, 128), cv2.FONT_HERSHEY_SIMPLEX, 0.95, (255, 255, 255), 2)
        return img

    def get_frame(self, robot_id: str) -> tuple[np.ndarray, str, str]:
        entry = self.registry.get(robot_id)
        if entry is None:
            raise KeyError(robot_id)
        source_url = str(entry.get("video_rtsp_url") or "")
        cap = self._get_capture(robot_id, source_url)
        note = ""
        status = "offline"
        frame = None
        if cap is not None:
            ok, frame = cap.read()
            if ok and frame is not None:
                status = "online"
                note = "RTSP ingested successfully"
            else:
                self._release_capture(robot_id)
                frame = None
                status = "degraded"
                note = "RTSP source unavailable, fallback to synthetic proxy frame"
        if frame is None:
            frame = self._synthetic_frame(robot_id, entry)
            if source_url:
                status = "degraded"
                note = note or "Synthetic proxy frame generated while waiting for RTSP"
            else:
                status = "offline"
                note = "No upstream RTSP yet, synthetic preview only"
        return frame, status, note


registry = StreamRegistry()
provider = FrameProvider(registry)
mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
stop_event = threading.Event()
publisher_thread: threading.Thread | None = None


def publish_service_heartbeat() -> None:
    payload = {
        "v": 1,
        "schema": "autofleet.heartbeat.v1",
        "source_id": "video-worker",
        "source_type": "video_worker",
        "status": "OK",
        "ts": int(time.time()),
        "meta": {"known_streams": len(registry.robot_ids())},
    }
    mqtt_client.publish(f"{TOPIC_PREFIX}/heartbeat/video-worker", json.dumps(payload), qos=0)


def publish_video_status(robot_id: str, status: str, note: str, frame: np.ndarray, source_url: str | None) -> None:
    h, w = frame.shape[:2]
    snapshot_name = f"{robot_id}.jpg"
    snapshot_path = SNAPSHOT_DIR / snapshot_name
    cv2.imwrite(str(snapshot_path), frame)
    payload = {
        "v": 1,
        "schema": "autofleet.video_status.v1",
        "robot_id": robot_id,
        "ts": int(time.time()),
        "source_url": source_url,
        "proxy_url": f"{VIDEO_PUBLIC_BASE}/streams/{robot_id}.mjpeg",
        "snapshot_url": f"{VIDEO_PUBLIC_BASE}/snapshots/{snapshot_name}",
        "status": status,
        "codec": "mjpeg",
        "fps": 6.0,
        "bitrate_kbps": round((w * h * 3 * 6.0) / 1000 / 5, 1),
        "width": int(w),
        "height": int(h),
        "note": note,
    }
    registry.update_status(robot_id, payload)
    mqtt_client.publish(f"{TOPIC_PREFIX}/video_status/{robot_id}", json.dumps(payload), qos=0)


def publisher_loop() -> None:
    interval_s = max(0.5, HEARTBEAT_INTERVAL_MS / 1000)
    while not stop_event.wait(interval_s):
        publish_service_heartbeat()
        for robot_id in registry.robot_ids():
            try:
                frame, status, note = provider.get_frame(robot_id)
            except KeyError:
                continue
            source_url = (registry.get(robot_id) or {}).get("video_rtsp_url")
            publish_video_status(robot_id, status, note, frame, source_url)


def on_connect(client: mqtt.Client, *_: Any) -> None:
    client.subscribe(f"{TOPIC_PREFIX}/telemetry/+", qos=0)


def on_message(_: mqtt.Client, __: Any, msg: mqtt.MQTTMessage) -> None:
    try:
        payload = json.loads(msg.payload.decode("utf-8"))
    except json.JSONDecodeError:
        return
    registry.update_from_telemetry(payload)


mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message


@asynccontextmanager
async def lifespan(_: FastAPI):
    global publisher_thread
    stop_event.clear()
    mqtt_client.connect(MQTT_HOST, MQTT_PORT, MQTT_KEEPALIVE)
    mqtt_client.loop_start()
    publisher_thread = threading.Thread(target=publisher_loop, daemon=True, name="video-worker-publisher")
    publisher_thread.start()
    try:
        yield
    finally:
        stop_event.set()
        if publisher_thread and publisher_thread.is_alive():
            publisher_thread.join(timeout=1.5)
        mqtt_client.loop_stop()
        mqtt_client.disconnect()


app = FastAPI(title="AutoFleet Video Worker", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "known_streams": len(registry.robot_ids()), "ts": int(time.time())}


@app.get("/streams")
def streams() -> dict[str, Any]:
    return {"items": registry.list_streams()}


@app.get("/snapshots/{name}")
def snapshot(name: str) -> FileResponse:
    target = SNAPSHOT_DIR / name
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"Snapshot {name} not found")
    return FileResponse(target, media_type="image/jpeg")


def mjpeg_generator(robot_id: str):
    while True:
        frame, _, _ = provider.get_frame(robot_id)
        ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
        if not ok:
            time.sleep(0.15)
            continue
        chunk = encoded.tobytes()
        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n"
            b"Content-Length: " + str(len(chunk)).encode("ascii") + b"\r\n\r\n" + chunk + b"\r\n"
        )
        time.sleep(0.15)


@app.get("/streams/{robot_id}.mjpeg")
def stream(robot_id: str) -> StreamingResponse:
    if registry.get(robot_id) is None:
        raise HTTPException(status_code=404, detail=f"Robot {robot_id} not found")
    return StreamingResponse(mjpeg_generator(robot_id), media_type="multipart/x-mixed-replace; boundary=frame")
