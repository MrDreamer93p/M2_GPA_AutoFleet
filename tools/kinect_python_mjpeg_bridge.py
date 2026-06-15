from __future__ import annotations

import argparse
import io
import json
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import numpy as np
from PIL import Image, ImageDraw, ImageFont

try:
    import cv2  # type: ignore
except Exception:
    cv2 = None

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "tools" / "vendor"
sys.path.insert(0, str(VENDOR))

if not hasattr(time, "clock"):
    time.clock = time.perf_counter  # type: ignore[attr-defined]

from pykinect2 import PyKinectRuntime, PyKinectV2  # noqa: E402


STREAMS = ["color", "depth", "distance", "infrared", "body_index", "skeleton"]
LOW_LATENCY_INTERVALS = {
    "color": 1.0 / 24.0,
    "depth": 1.0 / 15.0,
    "infrared": 1.0 / 15.0,
    "body_index": 1.0 / 10.0,
    "body": 1.0 / 10.0,
}
BONES = [
    ("Head", "Neck"),
    ("Neck", "SpineShoulder"),
    ("SpineShoulder", "SpineMid"),
    ("SpineShoulder", "ShoulderLeft"),
    ("SpineShoulder", "ShoulderRight"),
    ("SpineMid", "SpineBase"),
    ("SpineBase", "HipLeft"),
    ("SpineBase", "HipRight"),
    ("ShoulderLeft", "ElbowLeft"),
    ("ElbowLeft", "WristLeft"),
    ("WristLeft", "HandLeft"),
    ("ShoulderRight", "ElbowRight"),
    ("ElbowRight", "WristRight"),
    ("WristRight", "HandRight"),
    ("HipLeft", "KneeLeft"),
    ("KneeLeft", "AnkleLeft"),
    ("AnkleLeft", "FootLeft"),
    ("HipRight", "KneeRight"),
    ("KneeRight", "AnkleRight"),
    ("AnkleRight", "FootRight"),
]


def enum_value(name: str) -> int:
    return int(getattr(PyKinectV2, name))


JOINTS = {name[len("JointType_") :]: int(getattr(PyKinectV2, name)) for name in dir(PyKinectV2) if name.startswith("JointType_")}


class KinectState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.condition = threading.Condition(self.lock)
        self.latest: dict[str, bytes] = {}
        self.versions: dict[str, int] = {}
        self.stream_counts: dict[str, int] = {}
        self.stream_last_ts: dict[str, float] = {}
        self.stream_fps: dict[str, float] = {}
        self.frames = 0
        self.note = "Starting Python Kinect bridge"
        self.last_error = ""
        self.diagnostics: dict[str, object] = {}
        self.running = True
        self.kinect: PyKinectRuntime.PyKinectRuntime | None = None

    def set_jpeg(self, key: str, payload: bytes) -> None:
        if not payload:
            return
        with self.condition:
            now = time.perf_counter()
            previous_ts = self.stream_last_ts.get(key)
            self.latest[key] = payload
            self.versions[key] = self.versions.get(key, 0) + 1
            self.stream_counts[key] = self.stream_counts.get(key, 0) + 1
            self.stream_last_ts[key] = now
            if previous_ts is not None and now > previous_ts:
                instant_fps = 1.0 / max(0.001, now - previous_ts)
                previous_fps = self.stream_fps.get(key, instant_fps)
                self.stream_fps[key] = previous_fps * 0.82 + instant_fps * 0.18
            self.condition.notify_all()

    def get_jpeg(self, key: str) -> bytes | None:
        with self.lock:
            return self.latest.get(key)

    def get_jpeg_with_version(self, key: str) -> tuple[bytes | None, int]:
        with self.lock:
            return self.latest.get(key), self.versions.get(key, 0)

    def stream_stats(self) -> dict[str, dict[str, float | int | None]]:
        with self.lock:
            now = time.perf_counter()
            return {
                key: {
                    "frames": self.stream_counts.get(key, 0),
                    "fps": round(self.stream_fps.get(key, 0.0), 1),
                    "age_ms": round((now - self.stream_last_ts[key]) * 1000.0, 1) if key in self.stream_last_ts else None,
                    "bytes": len(self.latest[key]) if key in self.latest else 0,
                }
                for key in STREAMS
            }

    def wait_for_jpeg(self, key: str, previous_version: int, timeout: float = 1.0) -> tuple[bytes | None, int]:
        deadline = time.perf_counter() + timeout
        with self.condition:
            while self.running and self.versions.get(key, 0) <= previous_version:
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    break
                self.condition.wait(remaining)
            return self.latest.get(key), self.versions.get(key, 0)


STATE = KinectState()


def jpeg_bytes(image: Image.Image, quality: int = 76, max_width: int | None = None) -> bytes:
    if max_width and image.width > max_width:
        height = max(1, int(image.height * (max_width / image.width)))
        image = image.resize((max_width, height), Image.Resampling.BILINEAR)
    out = io.BytesIO()
    image.convert("RGB").save(out, format="JPEG", quality=quality)
    return out.getvalue()


def ui_font(size: int = 18) -> ImageFont.ImageFont:
    for name in ("arial.ttf", "segoeui.ttf", "consola.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def draw_status_panel(draw: ImageDraw.ImageDraw, title: str, lines: list[str], width: int, height: int) -> None:
    title_font = ui_font(30)
    body_font = ui_font(22)
    panel_w = min(width - 24, 500)
    panel_h = 124 + max(0, len(lines) - 1) * 34
    x0 = max(12, (width - panel_w) // 2)
    y0 = max(12, (height - panel_h) // 2)
    x1 = x0 + panel_w
    y1 = y0 + panel_h
    draw.rectangle((x0, y0, x1, y1), fill=(18, 18, 18), outline=(180, 180, 180), width=2)
    draw.rectangle((x0, y0, x1, y0 + 46), fill=(35, 35, 35))
    draw.text((x0 + 16, y0 + 7), title, fill=(255, 255, 255), font=title_font)
    for idx, line in enumerate(lines):
        draw.text((x0 + 16, y0 + 64 + idx * 34), line, fill=(210, 210, 210), font=body_font)


def color_to_jpeg(frame: np.ndarray, width: int, height: int) -> bytes:
    bgra = frame.reshape((height, width, 4))
    if cv2 is not None:
        bgr = bgra[:, :, :3]
        target_w = 512
        target_h = max(1, int(height * (target_w / width)))
        resized = cv2.resize(bgr, (target_w, target_h), interpolation=cv2.INTER_AREA)
        ok, encoded = cv2.imencode(".jpg", resized, [int(cv2.IMWRITE_JPEG_QUALITY), 65])
        if ok:
            return encoded.tobytes()
    rgb = bgra[:, :, [2, 1, 0]]
    return jpeg_bytes(Image.fromarray(rgb, "RGB"), quality=65, max_width=512)


def depth_to_jpeg(depth: np.ndarray, width: int, height: int) -> bytes:
    mm = depth.reshape((height, width)).astype(np.float32)
    valid = mm > 0
    near_mm = 500.0
    far_mm = 4500.0
    normalized = np.clip((mm - near_mm) / (far_mm - near_mm), 0.0, 1.0)
    gray = np.where(valid, (255.0 * (1.0 - normalized)), 0).astype(np.uint8)
    rgb = np.dstack((gray, gray, gray))
    image = Image.fromarray(rgb, "RGB")
    draw = ImageDraw.Draw(image)
    font = ui_font(14)
    if valid.any():
        nearest = float(mm[valid].min() / 1000.0)
        farthest = float(mm[valid].max() / 1000.0)
        center_raw = mm[height // 2, width // 2]
        center = float(center_raw / 1000.0) if center_raw > 0 else None
        valid_pct = 100.0 * float(valid.sum()) / max(1, valid.size)
    else:
        nearest = farthest = center = None
        valid_pct = 0.0
    draw.rectangle((8, 8, 266, 72), fill=(0, 0, 0), outline=(220, 220, 220), width=1)
    draw.text((14, 14), "DEPTH MAP (metric grayscale)", fill=(255, 255, 255), font=font)
    draw.text((14, 32), f"white=near {near_mm/1000:.1f}m  black=far {far_mm/1000:.1f}m", fill=(230, 230, 230), font=font)
    if center is not None:
        draw.text((14, 50), f"center {center:.2f}m  range {nearest:.2f}-{farthest:.2f}m  valid {valid_pct:.1f}%", fill=(230, 230, 230), font=font)
    else:
        draw.text((14, 50), f"center n/a  valid {valid_pct:.1f}%", fill=(230, 230, 230), font=font)
    for meters in (1.0, 2.0, 3.0, 4.0):
        level = int(255.0 * (1.0 - np.clip((meters * 1000.0 - near_mm) / (far_mm - near_mm), 0.0, 1.0)))
        y = 94 + int((4.5 - meters) / 3.5 * 260)
        draw.line((width - 76, y, width - 18, y), fill=(level, level, level), width=4)
        draw.text((width - 112, y - 7), f"{meters:.0f}m", fill=(255, 255, 255), font=font)
    return jpeg_bytes(image, quality=68)


def distance_to_jpeg(depth: np.ndarray, width: int, height: int) -> bytes:
    mm = depth.reshape((height, width)).astype(np.float32)
    valid = mm > 0
    near_mm = 500.0
    far_mm = 4500.0
    ratio = np.clip((mm - near_mm) / (far_mm - near_mm), 0.0, 1.0)
    ratio[~valid] = 1.0
    red = np.clip(255.0 * np.maximum(0.0, 1.0 - np.abs(ratio - 0.0) * 2.2), 0, 255)
    green = np.clip(255.0 * np.maximum(0.0, 1.0 - np.abs(ratio - 0.45) * 2.0), 0, 255)
    blue = np.clip(255.0 * np.maximum(0.0, 1.0 - np.abs(ratio - 1.0) * 1.6), 0, 255)
    rgb = np.dstack((red, green, blue)).astype(np.uint8)
    rgb[~valid] = np.array([10, 10, 18], dtype=np.uint8)
    image = Image.fromarray(rgb.astype(np.uint8), "RGB")
    draw = ImageDraw.Draw(image)
    font = ui_font(14)
    nearest = float(mm[valid].min() / 1000.0) if valid.any() else None
    center = float(mm[height // 2, width // 2] / 1000.0) if mm[height // 2, width // 2] > 0 else None
    valid_pct = 100.0 * float(valid.sum()) / max(1, valid.size)
    draw.rectangle((8, 8, 292, 90), fill=(0, 0, 0), outline=(240, 240, 240), width=1)
    draw.text((14, 14), "DISTANCE HEATMAP", fill=(255, 255, 255), font=font)
    draw.text((14, 34), f"red=near  green=mid  blue=far", fill=(230, 230, 230), font=font)
    draw.text((14, 54), f"nearest {nearest:.2f}m" if nearest else "nearest n/a", fill=(230, 230, 230), font=font)
    draw.text((144, 54), f"center {center:.2f}m" if center else "center n/a", fill=(230, 230, 230), font=font)
    draw.text((14, 72), f"valid {valid_pct:.1f}%", fill=(230, 230, 230), font=font)
    legend_x = width - 34
    for i in range(160):
        r = i / 159.0
        rr = int(np.clip(255.0 * max(0.0, 1.0 - abs(r - 0.0) * 2.2), 0, 255))
        gg = int(np.clip(255.0 * max(0.0, 1.0 - abs(r - 0.45) * 2.0), 0, 255))
        bb = int(np.clip(255.0 * max(0.0, 1.0 - abs(r - 1.0) * 1.6), 0, 255))
        draw.line((legend_x, 108 + i, legend_x + 12, 108 + i), fill=(rr, gg, bb))
    draw.text((legend_x - 26, 96), "0.5m", fill=(255, 255, 255), font=font)
    draw.text((legend_x - 26, 270), "4.5m", fill=(255, 255, 255), font=font)
    cx, cy = width // 2, height // 2
    draw.rectangle((cx - 22, cy - 22, cx + 22, cy + 22), outline=(255, 255, 255), width=2)
    draw.line((cx - 36, cy, cx + 36, cy), fill=(255, 255, 255), width=2)
    draw.line((cx, cy - 36, cx, cy + 36), fill=(255, 255, 255), width=2)
    return jpeg_bytes(image, quality=70)


def infrared_to_jpeg(frame: np.ndarray, width: int, height: int) -> bytes:
    ir = frame.reshape((height, width)).astype(np.float32)
    clipped = np.clip(ir, 0.0, 65535.0)
    # Kinect IR is 16-bit. Fixed scaling is faster and avoids auto-exposure flicker in the preview.
    lo, hi = 0.0, 12000.0
    normalized = np.clip((clipped - lo) / (hi - lo), 0.0, 1.0)
    gray = (normalized * 255.0).astype(np.uint8)
    rgb = np.dstack((gray, gray, gray))
    image = Image.fromarray(rgb, "RGB")
    draw = ImageDraw.Draw(image)
    font = ui_font(14)
    draw.rectangle((8, 8, 286, 72), fill=(0, 0, 0), outline=(220, 220, 220), width=1)
    draw.text((14, 14), "INFRARED (Kinect SDK IR)", fill=(255, 255, 255), font=font)
    draw.text((14, 32), "source: FrameSourceTypes_Infrared", fill=(230, 230, 230), font=font)
    draw.text((14, 50), f"raw 16-bit fixed scale: {lo:.0f}-{hi:.0f}", fill=(230, 230, 230), font=font)
    return jpeg_bytes(image, quality=70)


def body_index_to_jpeg(frame: np.ndarray, width: int, height: int) -> bytes:
    idx = frame.reshape((height, width)).astype(np.uint8)
    palette = np.array(
        [
            [255, 70, 70],
            [72, 255, 120],
            [70, 160, 255],
            [255, 230, 70],
            [255, 70, 220],
            [80, 255, 245],
        ],
        dtype=np.uint8,
    )
    rgb = np.zeros((height, width, 3), dtype=np.uint8) + 8
    mask = idx < len(palette)
    rgb[mask] = palette[idx[mask]]
    image = Image.fromarray(rgb, "RGB")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    body_pixels = int(mask.sum())
    STATE.diagnostics["body_index_pixels"] = body_pixels
    if body_pixels <= 0:
        draw_status_panel(
            draw,
            "BODY INDEX",
            ["No tracked body pixels.", "Stand fully in view, roughly 1-4m away."],
            width,
            height,
        )
    else:
        draw.text((12, 10), f"body pixels: {body_pixels}", fill=(255, 255, 255), font=font)
    return jpeg_bytes(image, quality=68)


def skeleton_to_jpeg(bodies, kinect: PyKinectRuntime.PyKinectRuntime, width: int, height: int) -> bytes:
    image = Image.new("RGB", (width, height), "black")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    tracked = 0
    tracking_ids = []
    try:
        for body in bodies.bodies:
            if not body.is_tracked:
                continue
            tracked += 1
            tracking_ids.append(getattr(body, "tracking_id", -1))
            joint_points = kinect.body_joints_to_depth_space(body.joints)
            for a_name, b_name in BONES:
                a_idx = JOINTS.get(a_name)
                b_idx = JOINTS.get(b_name)
                if a_idx is None or b_idx is None:
                    continue
                a = joint_points[a_idx]
                b = joint_points[b_idx]
                if not np.isfinite(a.x) or not np.isfinite(a.y) or not np.isfinite(b.x) or not np.isfinite(b.y):
                    continue
                ax, ay, bx, by = int(a.x), int(a.y), int(b.x), int(b.y)
                draw.line((ax, ay, bx, by), fill=(0, 255, 80), width=4)
                draw.ellipse((ax - 4, ay - 4, ax + 4, ay + 4), fill=(80, 255, 255))
                draw.ellipse((bx - 4, by - 4, bx + 4, by + 4), fill=(80, 255, 255))
    except Exception as exc:
        STATE.last_error = str(exc)
    STATE.diagnostics["tracked_bodies"] = tracked
    STATE.diagnostics["skeleton_source"] = "Kinect SDK BodyFrame + IBody.GetJoints + CoordinateMapper.MapCameraPointToDepthSpace"
    STATE.diagnostics["skeleton_tracking_ids"] = tracking_ids
    if tracked <= 0:
        body_pixels = STATE.diagnostics.get("body_index_pixels", 0)
        draw_status_panel(
            draw,
            "KINECT SKELETON",
            [
                "SDK BodyFrame online, no tracked body.",
                f"BodyIndex pixels: {body_pixels}",
                "Stand fully in view, roughly 1-4m away.",
            ],
            width,
            height,
        )
    else:
        id_text = ",".join(str(item) for item in tracking_ids if item is not None)
        draw.text((12, 10), f"SDK skeleton | tracked bodies: {tracked} | ids: {id_text}", fill=(255, 255, 255), font=font)
    return jpeg_bytes(image, quality=72)


def skeleton_status_jpeg(title: str, detail: str, width: int, height: int) -> bytes:
    image = Image.new("RGB", (width, height), "black")
    draw = ImageDraw.Draw(image)
    STATE.diagnostics["tracked_bodies"] = 0
    STATE.diagnostics["skeleton_source"] = "Kinect SDK BodyFrame + IBody.GetJoints + CoordinateMapper.MapCameraPointToDepthSpace"
    draw_status_panel(
        draw,
        title,
        [detail, "Stand fully in view, roughly 1-4m away."],
        width,
        height,
    )
    return jpeg_bytes(image, quality=72)


def placeholder_jpeg(name: str) -> bytes:
    image = Image.new("RGB", (640, 360), "black")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    draw.text((24, 24), f"KINECT {name.upper()}", fill=(255, 255, 255), font=font)
    draw.text((24, 44), STATE.note, fill=(180, 180, 180), font=font)
    if STATE.last_error:
        draw.text((24, 64), STATE.last_error[:96], fill=(255, 120, 120), font=font)
    return jpeg_bytes(image, quality=68)


def publish_color(frame: np.ndarray, width: int, height: int) -> int:
    STATE.set_jpeg("color", color_to_jpeg(frame, width, height))
    return 1


def publish_depth_pair(depth: np.ndarray, width: int, height: int) -> int:
    STATE.set_jpeg("depth", depth_to_jpeg(depth, width, height))
    STATE.set_jpeg("distance", distance_to_jpeg(depth, width, height))
    return 2


def publish_infrared(frame: np.ndarray, width: int, height: int) -> int:
    STATE.set_jpeg("infrared", infrared_to_jpeg(frame, width, height))
    return 1


def publish_body_index(frame: np.ndarray, width: int, height: int) -> int:
    STATE.set_jpeg("body_index", body_index_to_jpeg(frame, width, height))
    return 1


def publish_skeleton(bodies, kinect: PyKinectRuntime.PyKinectRuntime, width: int, height: int) -> int:
    STATE.set_jpeg("skeleton", skeleton_to_jpeg(bodies, kinect, width, height))
    return 1


def publish_skeleton_status(title: str, detail: str, width: int, height: int) -> int:
    STATE.set_jpeg("skeleton", skeleton_status_jpeg(title, detail, width, height))
    return 1


def capture_loop() -> None:
    flags = (
        PyKinectV2.FrameSourceTypes_Color
        | PyKinectV2.FrameSourceTypes_Infrared
        | PyKinectV2.FrameSourceTypes_Depth
        | PyKinectV2.FrameSourceTypes_BodyIndex
        | PyKinectV2.FrameSourceTypes_Body
    )
    try:
        kinect = PyKinectRuntime.PyKinectRuntime(flags)
        STATE.kinect = kinect
        STATE.diagnostics = {
            "wait_handle_count": getattr(kinect, "_waitHandleCount", None),
            "max_body_count": getattr(kinect, "max_body_count", None),
        }
        color_w, color_h = kinect.color_frame_desc.Width, kinect.color_frame_desc.Height
        depth_w, depth_h = kinect.depth_frame_desc.Width, kinect.depth_frame_desc.Height
        infrared_w, infrared_h = kinect.infrared_frame_desc.Width, kinect.infrared_frame_desc.Height
        last_encoded = {
            "color": 0.0,
            "depth": 0.0,
            "infrared": 0.0,
            "body_index": 0.0,
            "body": 0.0,
        }
        intervals = LOW_LATENCY_INTERVALS
        in_flight: dict[str, Future[int]] = {}
        with ThreadPoolExecutor(max_workers=6, thread_name_prefix="kinect-encode") as executor:
            while STATE.running:
                now = time.perf_counter()
                completed = 0
                for key, future in list(in_flight.items()):
                    if not future.done():
                        continue
                    in_flight.pop(key, None)
                    try:
                        completed += int(future.result() or 0)
                    except Exception as exc:
                        STATE.last_error = repr(exc)

                try:
                    sensor_available = bool(kinect._sensor.IsAvailable)
                except Exception:
                    sensor_available = None
                STATE.diagnostics.update({
                    "sensor_available": sensor_available,
                    "wait_handle_count": getattr(kinect, "_waitHandleCount", None),
                    "encoder_in_flight": sorted(in_flight.keys()),
                    "last_color_frame_time": getattr(kinect, "_last_color_frame_time", None),
                    "last_color_frame_access": getattr(kinect, "_last_color_frame_access", None),
                    "last_depth_frame_time": getattr(kinect, "_last_depth_frame_time", None),
                    "last_depth_frame_access": getattr(kinect, "_last_depth_frame_access", None),
                    "last_infrared_frame_time": getattr(kinect, "_last_infrared_frame_time", None),
                    "last_infrared_frame_access": getattr(kinect, "_last_infrared_frame_access", None),
                    "last_body_frame_time": getattr(kinect, "_last_body_frame_time", None),
                    "last_body_frame_access": getattr(kinect, "_last_body_frame_access", None),
                    "last_body_frame_error": getattr(kinect, "_last_body_frame_error", ""),
                    "sdk_tracked_bodies": getattr(kinect, "_last_body_frame_tracked_count", 0),
                    "sdk_tracking_ids": getattr(kinect, "_last_body_frame_tracking_ids", []),
                    "last_body_index_frame_time": getattr(kinect, "_last_body_index_frame_time", None),
                    "last_body_index_frame_access": getattr(kinect, "_last_body_index_frame_access", None),
                })

                submitted = False
                if "color" not in in_flight and now - last_encoded["color"] >= intervals["color"] and kinect.has_new_color_frame():
                    color = kinect.get_last_color_frame()
                    if color is not None:
                        in_flight["color"] = executor.submit(publish_color, color, color_w, color_h)
                        submitted = True
                    last_encoded["color"] = now
                if "depth" not in in_flight and now - last_encoded["depth"] >= intervals["depth"] and kinect.has_new_depth_frame():
                    depth = kinect.get_last_depth_frame()
                    if depth is not None:
                        in_flight["depth"] = executor.submit(publish_depth_pair, depth, depth_w, depth_h)
                        submitted = True
                    last_encoded["depth"] = now
                if "infrared" not in in_flight and now - last_encoded["infrared"] >= intervals["infrared"] and kinect.has_new_infrared_frame():
                    infrared = kinect.get_last_infrared_frame()
                    if infrared is not None:
                        in_flight["infrared"] = executor.submit(publish_infrared, infrared, infrared_w, infrared_h)
                        submitted = True
                    last_encoded["infrared"] = now
                if "body_index" not in in_flight and now - last_encoded["body_index"] >= intervals["body_index"] and kinect.has_new_body_index_frame():
                    body_index = kinect.get_last_body_index_frame()
                    if body_index is not None:
                        in_flight["body_index"] = executor.submit(publish_body_index, body_index, depth_w, depth_h)
                        submitted = True
                    last_encoded["body_index"] = now
                if "body" not in in_flight and now - last_encoded["body"] >= intervals["body"]:
                    if kinect.has_new_body_frame():
                        bodies = kinect.get_last_body_frame()
                        if bodies is not None:
                            in_flight["body"] = executor.submit(publish_skeleton, bodies, kinect, depth_w, depth_h)
                            submitted = True
                        elif STATE.get_jpeg("skeleton") is None:
                            in_flight["body"] = executor.submit(
                                publish_skeleton_status,
                                "KINECT SKELETON",
                                "No body frame received yet.",
                                depth_w,
                                depth_h,
                            )
                            submitted = True
                    elif STATE.get_jpeg("skeleton") is None:
                        in_flight["body"] = executor.submit(
                            publish_skeleton_status,
                            "KINECT SKELETON",
                            "Waiting for body tracking frames.",
                            depth_w,
                            depth_h,
                        )
                        submitted = True
                    last_encoded["body"] = now

                if completed or submitted:
                    STATE.frames += completed
                    STATE.note = "Kinect frames online"
                else:
                    if STATE.frames <= 0:
                        STATE.note = "Waiting for Kinect frames"
                    elif not sensor_available:
                        STATE.note = "Kinect sensor unavailable"
                    else:
                        STATE.note = "Kinect frames online"
                    time.sleep(0.002)
    except Exception as exc:
        STATE.last_error = repr(exc)
        STATE.note = f"Kinect capture failed: {exc}"
    finally:
        if STATE.kinect is not None:
            STATE.kinect.close()


class Handler(BaseHTTPRequestHandler):
    server_version = "AutoFleetKinectPython/1.0"

    def log_message(self, *_args) -> None:
        return

    def _send(self, payload: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Accept")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_mjpeg_stream(self, name: str) -> None:
        boundary = "frame"
        self.send_response(200)
        self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={boundary}")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Accept")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.end_headers()

        jpeg, version = STATE.get_jpeg_with_version(name)
        if jpeg is None:
            jpeg = placeholder_jpeg(name)
        while STATE.running:
            try:
                header = (
                    f"--{boundary}\r\n"
                    "Content-Type: image/jpeg\r\n"
                    "Cache-Control: no-store\r\n"
                    f"Content-Length: {len(jpeg)}\r\n\r\n"
                ).encode("ascii")
                self.wfile.write(header)
                self.wfile.write(jpeg)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
                next_jpeg, next_version = STATE.wait_for_jpeg(name, version, timeout=1.5)
                if next_jpeg is not None and next_version > version:
                    jpeg = next_jpeg
                    version = next_version
            except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
                break

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Accept")
        self.end_headers()

    def do_GET(self) -> None:
        path = urlparse(self.path).path.strip("/").lower()
        if path == "health":
            payload = {
                "status": "ok",
                "source": "windows-kinect-python",
                "frames": STATE.frames,
                "note": STATE.note,
                "last_error": STATE.last_error,
                "diagnostics": STATE.diagnostics,
                "streams": STATE.stream_stats(),
            }
            self._send(json.dumps(payload).encode("utf-8"), "application/json")
            return
        if path == "streams":
            self._send(json.dumps({"items": STREAMS}).encode("utf-8"), "application/json")
            return
        if path.startswith("streams/") and path.endswith(".mjpeg"):
            name = path[len("streams/") : -len(".mjpeg")].replace("-", "_")
            self._send_mjpeg_stream(name)
            return
        if path.startswith("frames/") and path.endswith(".jpg"):
            name = path[len("frames/") : -len(".jpg")].replace("-", "_")
            jpeg = STATE.get_jpeg(name) or placeholder_jpeg(name)
            self._send(jpeg, "image/jpeg")
            return
        self.send_response(404)
        self.end_headers()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8450)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Kinect Python MJPEG bridge listening on http://{args.host}:{args.port}", flush=True)
    STATE.note = "HTTP bridge online; starting Kinect capture"
    thread = threading.Thread(target=capture_loop, name="kinect-capture", daemon=True)
    thread.start()
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        STATE.running = False
        server.server_close()


if __name__ == "__main__":
    main()
