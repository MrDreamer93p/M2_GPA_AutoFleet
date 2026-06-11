from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import paho.mqtt.client as mqtt
from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse


MQTT_HOST = os.getenv("AUTOFLEET_MQTT_HOST", "mosquitto")
MQTT_PORT = int(os.getenv("AUTOFLEET_MQTT_PORT", "3889"))
MQTT_KEEPALIVE = int(os.getenv("AUTOFLEET_MQTT_KEEPALIVE", "30"))
TOPIC_PREFIX = os.getenv("AUTOFLEET_TOPIC_PREFIX", "fleet/v1")
VIDEO_PUBLIC_BASE = os.getenv("AUTOFLEET_VIDEO_PUBLIC_BASE", "http://127.0.0.1:8400").rstrip("/")
SNAPSHOT_DIR = Path(os.getenv("AUTOFLEET_VIDEO_SNAPSHOT_DIR", "/artifacts/snapshots"))
HEARTBEAT_INTERVAL_MS = int(os.getenv("AUTOFLEET_HEARTBEAT_INTERVAL_MS", "2000"))
CAPTURE_INTERVAL_MS = int(os.getenv("AUTOFLEET_VIDEO_CAPTURE_INTERVAL_MS", "50"))
MJPEG_INTERVAL_MS = int(os.getenv("AUTOFLEET_MJPEG_INTERVAL_MS", "50"))
FFMPEG_DIRECT_MJPEG = os.getenv("AUTOFLEET_FFMPEG_DIRECT_MJPEG", "1").strip().lower() not in {"0", "false", "no"}
FFMPEG_RTSP_TRANSPORT = os.getenv("AUTOFLEET_FFMPEG_RTSP_TRANSPORT", "udp").strip().lower()
FFMPEG_MJPEG_FPS = int(os.getenv("AUTOFLEET_FFMPEG_MJPEG_FPS", "20"))
FFMPEG_MJPEG_WIDTH = int(os.getenv("AUTOFLEET_FFMPEG_MJPEG_WIDTH", "640"))
MJPEG_JPEG_QUALITY = int(os.getenv("AUTOFLEET_MJPEG_JPEG_QUALITY", "62"))
FFMPEG_EXE = os.getenv("AUTOFLEET_FFMPEG_EXE", "").strip()
_FFMPEG_CACHE: str | None = None

VIDEO_QUALITY_PRESETS: dict[str, dict[str, Any]] = {
    "ultra_low_latency": {
        "label": "Ultra Low Latency",
        "max_width": 360,
        "jpeg_quality": 54,
        "mjpeg_interval_ms": 40,
        "capture_interval_ms": 30,
        "direct_mjpeg": False,
    },
    "balanced": {
        "label": "Balanced",
        "max_width": 640,
        "jpeg_quality": 68,
        "mjpeg_interval_ms": 30,
        "capture_interval_ms": 20,
        "direct_mjpeg": False,
    },
    "quality": {
        "label": "Quality",
        "max_width": 960,
        "jpeg_quality": 76,
        "mjpeg_interval_ms": 35,
        "capture_interval_ms": 25,
        "direct_mjpeg": False,
    },
    "high_quality": {
        "label": "High Quality",
        "max_width": 1280,
        "jpeg_quality": 82,
        "mjpeg_interval_ms": 45,
        "capture_interval_ms": 35,
        "direct_mjpeg": False,
    },
}


class VideoSettings:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        preset = os.getenv("AUTOFLEET_VIDEO_QUALITY_PRESET", "balanced").strip().lower()
        if preset not in VIDEO_QUALITY_PRESETS:
            preset = "balanced"
        self._settings = dict(VIDEO_QUALITY_PRESETS[preset])
        self._settings.update(
            {
                "preset": preset,
                "max_width": int(os.getenv("AUTOFLEET_VIDEO_MAX_WIDTH", str(self._settings["max_width"]))),
                "jpeg_quality": int(os.getenv("AUTOFLEET_MJPEG_JPEG_QUALITY", str(self._settings["jpeg_quality"]))),
                "mjpeg_interval_ms": int(os.getenv("AUTOFLEET_MJPEG_INTERVAL_MS", str(self._settings["mjpeg_interval_ms"]))),
                "capture_interval_ms": int(os.getenv("AUTOFLEET_VIDEO_CAPTURE_INTERVAL_MS", str(self._settings["capture_interval_ms"]))),
                "direct_mjpeg": FFMPEG_DIRECT_MJPEG,
            }
        )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            payload = dict(self._settings)
        return {"presets": VIDEO_QUALITY_PRESETS, "current": payload}

    def current(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._settings)

    def apply(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            preset = str(payload.get("preset") or self._settings.get("preset") or "balanced").strip().lower()
            if preset not in VIDEO_QUALITY_PRESETS:
                raise ValueError(f"Unknown video quality preset: {preset}")
            next_settings = dict(VIDEO_QUALITY_PRESETS[preset])
            next_settings["preset"] = preset
            for key in ("max_width", "jpeg_quality", "mjpeg_interval_ms", "capture_interval_ms"):
                if key in payload and payload[key] is not None:
                    next_settings[key] = int(payload[key])
            if "direct_mjpeg" in payload and payload["direct_mjpeg"] is not None:
                next_settings["direct_mjpeg"] = bool(payload["direct_mjpeg"])
            next_settings["max_width"] = max(160, min(1920, int(next_settings["max_width"])))
            next_settings["jpeg_quality"] = max(35, min(92, int(next_settings["jpeg_quality"])))
            next_settings["mjpeg_interval_ms"] = max(20, min(200, int(next_settings["mjpeg_interval_ms"])))
            next_settings["capture_interval_ms"] = max(15, min(200, int(next_settings["capture_interval_ms"])))
            self._settings = next_settings
            return dict(self._settings)


video_settings = VideoSettings()

SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

for stale_snapshot in SNAPSHOT_DIR.glob("*.jpg"):
    try:
        stale_snapshot.unlink(missing_ok=True)
    except OSError:
        pass


def find_ffmpeg() -> str | None:
    global _FFMPEG_CACHE
    if _FFMPEG_CACHE:
        return _FFMPEG_CACHE
    if FFMPEG_EXE:
        target = Path(FFMPEG_EXE).expanduser()
        if target.exists():
            _FFMPEG_CACHE = str(target)
            return _FFMPEG_CACHE
    found = shutil.which("ffmpeg")
    if found:
        _FFMPEG_CACHE = found
        return _FFMPEG_CACHE
    winget_root = Path(os.getenv("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    if winget_root.exists():
        for target in winget_root.glob("Gyan.FFmpeg*/*/bin/ffmpeg.exe"):
            _FFMPEG_CACHE = str(target)
            return _FFMPEG_CACHE
    return None


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
                    "video_view_profile": payload.get("video_view_profile"),
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
                        "view_profile": entry.get("video_view_profile"),
                        "status": status.get("status", "offline"),
                        "proxy_url": status.get("proxy_url"),
                        "snapshot_url": status.get("snapshot_url"),
                        "fps": status.get("fps"),
                        "bitrate_kb_s": status.get("bitrate_kb_s"),
                        "bitrate_kbps": status.get("bitrate_kbps"),
                        "note": status.get("note"),
                    }
                )
            return out


class FrameProvider:
    def __init__(self, registry: StreamRegistry) -> None:
        self.registry = registry
        self._lock = threading.Lock()
        self._captures: dict[str, tuple[str, str, cv2.VideoCapture]] = {}
        self._latest: dict[str, tuple[np.ndarray, str, str, float, str | None, str | None]] = {}

    def _release_capture(self, robot_id: str) -> None:
        with self._lock:
            existing = self._captures.pop(robot_id, None)
        if existing:
            _, _, cap = existing
            cap.release()

    def _resolve_source(self, source_url: str | None) -> tuple[str, str, str | int] | None:
        raw = str(source_url or "").strip()
        if not raw:
            return None
        lower = raw.lower()
        if lower.startswith("rtsp://"):
            return raw, "rtsp", raw
        if lower.startswith(("http://", "https://")):
            return raw, "network", raw
        if lower.startswith("file://"):
            path = Path(raw[7:]).expanduser()
            return raw, "file", str(path.resolve(strict=False))
        if lower.startswith("camera://"):
            camera_ref = raw.split("://", 1)[1].strip()
            if not camera_ref:
                return None
            return raw, "camera", int(camera_ref) if camera_ref.isdigit() else camera_ref
        path = Path(raw).expanduser()
        return raw, "file", str(path.resolve(strict=False))

    def _open_capture(self, source_kind: str, capture_ref: str | int) -> cv2.VideoCapture | None:
        if source_kind == "file":
            target = Path(str(capture_ref))
            if not target.exists():
                return None
            cap = cv2.VideoCapture(str(target))
        else:
            cap = cv2.VideoCapture(capture_ref)
        if not cap.isOpened():
            cap.release()
            return None
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def _get_capture(self, robot_id: str, source_url: str | None) -> tuple[str, cv2.VideoCapture] | None:
        resolved = self._resolve_source(source_url)
        if resolved is None:
            return None
        source_key, source_kind, capture_ref = resolved
        with self._lock:
            existing = self._captures.get(robot_id)
            if existing and existing[0] == source_key and existing[1] == source_kind and existing[2].isOpened():
                return source_kind, existing[2]
        cap = self._open_capture(source_kind, capture_ref)
        if cap is None:
            return None
        with self._lock:
            previous = self._captures.pop(robot_id, None)
            if previous:
                previous[2].release()
            self._captures[robot_id] = (source_key, source_kind, cap)
        return source_kind, cap

    def _read_frame(self, robot_id: str, source_kind: str, cap: cv2.VideoCapture, source_url: str | None) -> np.ndarray | None:
        ok, frame = cap.read()
        if ok and frame is not None:
            return frame
        if source_kind == "file":
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = cap.read()
            if ok and frame is not None:
                return frame
        if source_kind in {"rtsp", "network"}:
            self._release_capture(robot_id)
            reopened = self._get_capture(robot_id, source_url)
            if reopened is None:
                return None
            _, retry_cap = reopened
            ok, frame = retry_cap.read()
            if ok and frame is not None:
                return frame
        return None

    @staticmethod
    def _source_label(source_kind: str) -> str:
        if source_kind == "rtsp":
            return "RTSP"
        if source_kind == "network":
            return "network"
        if source_kind == "camera":
            return "camera"
        if source_kind == "file":
            return "file"
        return "video"

    @staticmethod
    def _crop_resize(frame: np.ndarray, left: float, top: float, right: float, bottom: float) -> np.ndarray:
        h, w = frame.shape[:2]
        x0 = max(0, min(w - 1, int(left * w)))
        x1 = max(x0 + 2, min(w, int(right * w)))
        y0 = max(0, min(h - 1, int(top * h)))
        y1 = max(y0 + 2, min(h, int(bottom * h)))
        cropped = frame[y0:y1, x0:x1]
        if cropped.size == 0:
            return frame
        return cv2.resize(cropped, (w, h), interpolation=cv2.INTER_LINEAR)

    @staticmethod
    def _annotate_view(frame: np.ndarray, robot_id: str, view_profile: str | None) -> np.ndarray:
        label = robot_id if not view_profile else f"{robot_id} | {view_profile}"
        accent = (
            ((abs(hash(robot_id)) >> 0) & 0x7F) + 96,
            ((abs(hash(robot_id)) >> 7) & 0x7F) + 96,
            ((abs(hash(robot_id)) >> 14) & 0x7F) + 96,
        )
        out = frame.copy()
        h, w = out.shape[:2]
        cv2.rectangle(out, (0, 0), (w - 1, h - 1), accent, 4)
        cv2.rectangle(out, (12, 12), (12 + min(280, max(160, 13 * len(label))), 50), (12, 18, 24), -1)
        cv2.putText(out, label, (22, 38), cv2.FONT_HERSHEY_SIMPLEX, 0.72, accent, 2, cv2.LINE_AA)
        return out

    def _apply_view_profile(self, frame: np.ndarray, view_profile: str | None) -> np.ndarray:
        profile = str(view_profile or "").strip().lower()
        if not profile or profile in {"none", "raw", "full"}:
            return frame
        if profile == "front_left":
            return self._crop_resize(frame, 0.00, 0.12, 0.58, 0.94)
        if profile == "front_center":
            return self._crop_resize(frame, 0.20, 0.12, 0.80, 0.94)
        if profile == "front_right":
            return self._crop_resize(frame, 0.42, 0.12, 1.00, 0.94)
        if profile == "side_left":
            return self._crop_resize(frame, 0.00, 0.16, 0.50, 0.94)
        if profile == "side_right":
            return self._crop_resize(frame, 0.50, 0.16, 1.00, 0.94)
        if profile == "zoom_center":
            return self._crop_resize(frame, 0.28, 0.20, 0.72, 0.90)
        return frame

    @staticmethod
    def _apply_output_profile(frame: np.ndarray) -> np.ndarray:
        settings = video_settings.current()
        max_width = int(settings.get("max_width") or 640)
        h, w = frame.shape[:2]
        if w <= max_width:
            return frame
        target_h = max(1, int(h * (max_width / w)))
        return cv2.resize(frame, (max_width, target_h), interpolation=cv2.INTER_AREA)

    def get_frame(self, robot_id: str) -> tuple[np.ndarray, str, str] | None:
        entry = self.registry.get(robot_id)
        if entry is None:
            raise KeyError(robot_id)
        source_url = str(entry.get("video_rtsp_url") or "").strip()
        view_profile = str(entry.get("video_view_profile") or "").strip()
        capture_info = self._get_capture(robot_id, source_url)
        note = ""
        status = "offline"
        frame = None
        if capture_info is not None:
            source_kind, cap = capture_info
            frame = self._read_frame(robot_id, source_kind, cap, source_url)
            if frame is not None:
                frame = self._apply_view_profile(frame, view_profile)
                frame = self._annotate_view(frame, robot_id, view_profile)
                frame = self._apply_output_profile(frame)
                status = "online"
                if view_profile:
                    note = f"{self._source_label(source_kind)} source ingested successfully ({view_profile})"
                else:
                    note = f"{self._source_label(source_kind)} source ingested successfully"
            else:
                self._release_capture(robot_id)
                status = "degraded"
                note = f"{self._source_label(source_kind)} source unavailable; no proxy frame published"
        if frame is None:
            if source_url:
                status = "degraded"
                note = note or "Upstream video source is not reachable; waiting for real frames"
            else:
                status = "offline"
                note = "No upstream video source registered"
            with self._lock:
                self._latest.pop(robot_id, None)
            return None
        return frame, status, note

    def update_latest_frame(self, robot_id: str) -> tuple[np.ndarray, str, str] | None:
        try:
            result = self.get_frame(robot_id)
        except KeyError:
            return None
        if result is None:
            return None
        frame, status, note = result
        entry = self.registry.get(robot_id) or {}
        with self._lock:
            self._latest[robot_id] = (
                frame,
                status,
                note,
                time.time(),
                entry.get("video_rtsp_url"),
                entry.get("video_view_profile"),
            )
        return frame, status, note

    def latest_frame(self, robot_id: str) -> tuple[np.ndarray, str, str, float, str | None, str | None] | None:
        with self._lock:
            cached = self._latest.get(robot_id)
        if cached is None:
            self.update_latest_frame(robot_id)
            with self._lock:
                cached = self._latest.get(robot_id)
        return cached


registry = StreamRegistry()
provider = FrameProvider(registry)
mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
stop_event = threading.Event()
publisher_thread: threading.Thread | None = None
capture_thread: threading.Thread | None = None


class FfmpegFrameHub:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._streams: dict[str, dict[str, Any]] = {}

    def stop_all(self) -> None:
        with self._lock:
            robot_ids = list(self._streams)
        for robot_id in robot_ids:
            self.stop(robot_id)

    def stop(self, robot_id: str) -> None:
        with self._lock:
            stream = self._streams.pop(robot_id, None)
        if not stream:
            return
        stream["stop"].set()
        proc = stream.get("proc")
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                proc.kill()

    def ensure(self, robot_id: str, source_url: str) -> None:
        source_url = str(source_url or "").strip()
        if not source_url.lower().startswith("rtsp://"):
            return
        with self._lock:
            existing = self._streams.get(robot_id)
            if existing and existing.get("source_url") == source_url:
                proc = existing.get("proc")
                if proc is not None and proc.poll() is None:
                    return
        self.stop(robot_id)
        stop_flag = threading.Event()
        stream = {
            "source_url": source_url,
            "stop": stop_flag,
            "latest_jpeg": None,
            "latest_frame": None,
            "latest_ts": 0.0,
            "status": "offline",
            "note": "FFmpeg stream starting",
            "proc": None,
        }
        with self._lock:
            self._streams[robot_id] = stream
        thread = threading.Thread(target=self._run, args=(robot_id, stream), daemon=True, name=f"ffmpeg-hub-{robot_id}")
        stream["thread"] = thread
        thread.start()

    def latest(self, robot_id: str) -> dict[str, Any] | None:
        with self._lock:
            stream = self._streams.get(robot_id)
            if not stream:
                return None
            return {
                "source_url": stream.get("source_url"),
                "latest_jpeg": stream.get("latest_jpeg"),
                "latest_frame": stream.get("latest_frame"),
                "latest_ts": stream.get("latest_ts", 0.0),
                "status": stream.get("status", "offline"),
                "note": stream.get("note", ""),
            }

    def _run(self, robot_id: str, stream: dict[str, Any]) -> None:
        ffmpeg = find_ffmpeg()
        if ffmpeg is None:
            with self._lock:
                stream["status"] = "degraded"
                stream["note"] = "ffmpeg.exe not found"
            return
        with self._lock:
            stream["status"] = "degraded"
            stream["note"] = f"Starting ffmpeg: {Path(ffmpeg).name}"
        source_url = str(stream["source_url"])
        transport = FFMPEG_RTSP_TRANSPORT if FFMPEG_RTSP_TRANSPORT in {"udp", "tcp", "udp_multicast", "http", "https"} else "tcp"
        vf = f"fps={max(1, FFMPEG_MJPEG_FPS)},scale={max(160, FFMPEG_MJPEG_WIDTH)}:-1"
        cmd = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-avioflags",
            "direct",
            "-fflags",
            "nobuffer+discardcorrupt",
            "-flags",
            "low_delay",
            "-rtsp_transport",
            transport,
            "-reorder_queue_size",
            "0",
            "-probesize",
            "32768",
            "-analyzeduration",
            "0",
            "-max_delay",
            "250000",
            "-i",
            source_url,
            "-an",
            "-vf",
            vf,
            "-vsync",
            "0",
            "-q:v",
            "7",
            "-flush_packets",
            "1",
            "-f",
            "image2pipe",
            "-vcodec",
            "mjpeg",
            "pipe:1",
        ]
        kwargs: dict[str, Any] = {"stdout": subprocess.PIPE, "stderr": subprocess.DEVNULL, "bufsize": 0}
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        buffer = b""
        try:
            proc = subprocess.Popen(cmd, **kwargs)
            with self._lock:
                stream["proc"] = proc
                stream["status"] = "degraded"
                stream["note"] = "FFmpeg connected, waiting for frames"
            assert proc.stdout is not None
            while not stream["stop"].is_set():
                chunk = proc.stdout.read(4096)
                if not chunk:
                    break
                buffer += chunk
                while True:
                    soi = buffer.find(b"\xff\xd8")
                    eoi = buffer.find(b"\xff\xd9", soi + 2) if soi >= 0 else -1
                    if soi < 0:
                        buffer = buffer[-2:]
                        break
                    if eoi < 0:
                        buffer = buffer[soi:]
                        break
                    jpeg = buffer[soi : eoi + 2]
                    buffer = buffer[eoi + 2 :]
                    arr = np.frombuffer(jpeg, dtype=np.uint8)
                    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                    with self._lock:
                        stream["latest_jpeg"] = jpeg
                        stream["latest_frame"] = frame
                        stream["latest_ts"] = time.time()
                        stream["status"] = "online"
                        stream["note"] = f"FFmpeg RTSP stream online ({transport})"
            with self._lock:
                stream["status"] = "degraded"
                stream["note"] = "FFmpeg stream ended"
        except Exception as exc:
            with self._lock:
                stream["status"] = "degraded"
                stream["note"] = f"FFmpeg stream failed: {exc}"
        finally:
            proc = stream.get("proc")
            if proc and proc.poll() is None:
                proc.terminate()


ffmpeg_hub = FfmpegFrameHub()


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


def publish_video_status(
    robot_id: str,
    status: str,
    note: str,
    frame: np.ndarray,
    source_url: str | None,
    view_profile: str | None,
) -> None:
    h, w = frame.shape[:2]
    snapshot_name = f"{robot_id}.jpg"
    snapshot_path = SNAPSHOT_DIR / snapshot_name
    settings = video_settings.current()
    jpeg_quality = int(settings.get("jpeg_quality") or MJPEG_JPEG_QUALITY)
    cv2.imwrite(str(snapshot_path), frame, [int(cv2.IMWRITE_JPEG_QUALITY), jpeg_quality])
    fps = round(1000 / max(1, int(settings.get("mjpeg_interval_ms") or MJPEG_INTERVAL_MS)), 1)
    bitrate_kb_s = round((w * h * 3 * fps) / 1024 / max(1.0, 100 - jpeg_quality), 1)
    payload = {
        "v": 1,
        "schema": "autofleet.video_status.v1",
        "robot_id": robot_id,
        "ts": int(time.time()),
        "source_url": source_url,
        "proxy_url": f"{VIDEO_PUBLIC_BASE}/streams/{robot_id}.mjpeg",
        "snapshot_url": f"{VIDEO_PUBLIC_BASE}/snapshots/{snapshot_name}",
        "view_profile": view_profile,
        "status": status,
        "codec": "mjpeg",
        "fps": fps,
        "bitrate_kb_s": bitrate_kb_s,
        "bitrate_kbps": round(bitrate_kb_s * 8192 / 1000, 1),
        "width": int(w),
        "height": int(h),
        "note": f"{note}; quality={settings.get('preset')}",
    }
    registry.update_status(robot_id, payload)
    mqtt_client.publish(f"{TOPIC_PREFIX}/video_status/{robot_id}", json.dumps(payload), qos=0)


def publish_video_unavailable(
    robot_id: str,
    status: str,
    note: str,
    source_url: str | None,
    view_profile: str | None,
) -> None:
    snapshot_path = SNAPSHOT_DIR / f"{robot_id}.jpg"
    try:
        snapshot_path.unlink(missing_ok=True)
    except OSError:
        pass
    payload = {
        "v": 1,
        "schema": "autofleet.video_status.v1",
        "robot_id": robot_id,
        "ts": int(time.time()),
        "source_url": source_url,
        "proxy_url": None,
        "snapshot_url": None,
        "view_profile": view_profile,
        "status": status,
        "codec": "mjpeg",
        "fps": 0.0,
        "bitrate_kb_s": 0.0,
        "bitrate_kbps": 0.0,
        "width": 0,
        "height": 0,
        "note": note,
    }
    registry.update_status(robot_id, payload)
    mqtt_client.publish(f"{TOPIC_PREFIX}/video_status/{robot_id}", json.dumps(payload), qos=0)


def publisher_loop() -> None:
    interval_s = max(0.5, HEARTBEAT_INTERVAL_MS / 1000)
    while not stop_event.wait(interval_s):
        publish_service_heartbeat()
        for robot_id in registry.robot_ids():
            entry = registry.get(robot_id) or {}
            source_url = str(entry.get("video_rtsp_url") or "").strip()
            view_profile = entry.get("video_view_profile")
            if bool(video_settings.current().get("direct_mjpeg")) and source_url.lower().startswith("rtsp://"):
                ffmpeg_hub.ensure(robot_id, source_url)
                latest = ffmpeg_hub.latest(robot_id)
                if latest and latest.get("latest_frame") is not None:
                    frame = latest["latest_frame"]
                    publish_video_status(
                        robot_id,
                        str(latest.get("status") or "online"),
                        str(latest.get("note") or "FFmpeg RTSP stream online"),
                        frame,
                        source_url,
                        view_profile,
                    )
                    continue
                publish_video_unavailable(
                    robot_id,
                    str((latest or {}).get("status") or "degraded"),
                    str((latest or {}).get("note") or "FFmpeg RTSP stream has not produced real frames yet"),
                    source_url,
                    view_profile,
                )
                continue
            latest = provider.latest_frame(robot_id)
            if latest is None:
                publish_video_unavailable(
                    robot_id,
                    "degraded" if source_url else "offline",
                    "Upstream video source is not reachable; waiting for real frames" if source_url else "No upstream video source registered",
                    source_url,
                    view_profile,
                )
                continue
            frame, status, note, _, source_url, view_profile = latest
            publish_video_status(robot_id, status, note, frame, source_url, view_profile)


def capture_loop() -> None:
    while not stop_event.wait(max(0.015, int(video_settings.current().get("capture_interval_ms") or CAPTURE_INTERVAL_MS) / 1000)):
        for robot_id in registry.robot_ids():
            entry = registry.get(robot_id) or {}
            source_url = str(entry.get("video_rtsp_url") or "").strip()
            if bool(video_settings.current().get("direct_mjpeg")) and source_url.lower().startswith("rtsp://"):
                ffmpeg_hub.ensure(robot_id, source_url)
                continue
            provider.update_latest_frame(robot_id)


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
    global publisher_thread, capture_thread
    stop_event.clear()
    mqtt_client.connect(MQTT_HOST, MQTT_PORT, MQTT_KEEPALIVE)
    mqtt_client.loop_start()
    capture_thread = threading.Thread(target=capture_loop, daemon=True, name="video-worker-capture")
    capture_thread.start()
    publisher_thread = threading.Thread(target=publisher_loop, daemon=True, name="video-worker-publisher")
    publisher_thread.start()
    try:
        yield
    finally:
        stop_event.set()
        ffmpeg_hub.stop_all()
        if capture_thread and capture_thread.is_alive():
            capture_thread.join(timeout=1.5)
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
        settings = video_settings.current()
        interval_s = max(0.02, int(settings.get("mjpeg_interval_ms") or MJPEG_INTERVAL_MS) / 1000)
        jpeg_quality = int(settings.get("jpeg_quality") or MJPEG_JPEG_QUALITY)
        latest = provider.latest_frame(robot_id)
        if latest is None:
            time.sleep(interval_s)
            continue
        frame = latest[0]
        ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), jpeg_quality])
        if not ok:
            time.sleep(interval_s)
            continue
        chunk = encoded.tobytes()
        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n"
            b"Cache-Control: no-store, no-cache, must-revalidate, max-age=0\r\n"
            b"Pragma: no-cache\r\n"
            b"Content-Length: " + str(len(chunk)).encode("ascii") + b"\r\n\r\n" + chunk + b"\r\n"
        )
        time.sleep(interval_s)


def ffmpeg_hub_mjpeg_generator(robot_id: str, source_url: str):
    ffmpeg_hub.ensure(robot_id, source_url)
    last_ts = 0.0
    while True:
        interval_s = max(0.02, int(video_settings.current().get("mjpeg_interval_ms") or MJPEG_INTERVAL_MS) / 1000)
        latest = ffmpeg_hub.latest(robot_id)
        if not latest or latest.get("latest_jpeg") is None:
            time.sleep(interval_s)
            continue
        ts = float(latest.get("latest_ts") or 0.0)
        if ts <= last_ts:
            time.sleep(interval_s)
            continue
        last_ts = ts
        chunk = latest["latest_jpeg"]
        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n"
            b"Cache-Control: no-store\r\n"
            b"Content-Length: " + str(len(chunk)).encode("ascii") + b"\r\n\r\n" + chunk + b"\r\n"
        )


@app.get("/streams/{robot_id}.mjpeg")
def stream(robot_id: str) -> StreamingResponse:
    entry = registry.get(robot_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Robot {robot_id} not found")
    source_url = str(entry.get("video_rtsp_url") or "").strip()
    if bool(video_settings.current().get("direct_mjpeg")) and source_url.lower().startswith("rtsp://") and find_ffmpeg() is not None:
        return StreamingResponse(
            ffmpeg_hub_mjpeg_generator(robot_id, source_url),
            media_type="multipart/x-mixed-replace; boundary=frame",
            headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache"},
        )
    return StreamingResponse(
        mjpeg_generator(robot_id),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache"},
    )


@app.get("/settings")
def get_settings() -> dict[str, Any]:
    return video_settings.snapshot()


@app.post("/settings")
def update_settings(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        current = video_settings.apply(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"current": current, "presets": VIDEO_QUALITY_PRESETS}
