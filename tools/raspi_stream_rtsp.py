from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


CONFIG_PATH = Path("/home/agent/robot-agent/config.env")


def load_config() -> dict[str, str]:
    values: dict[str, str] = {}
    if not CONFIG_PATH.exists():
        return values
    for raw_line in CONFIG_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def main() -> int:
    cfg = load_config()
    rtsp_port = cfg.get("RTSP_PORT", os.getenv("RTSP_PORT", "8554"))
    stream_name = cfg.get("STREAM_NAME", os.getenv("STREAM_NAME", "camera"))
    width = cfg.get("STREAM_WIDTH", os.getenv("STREAM_WIDTH", "1536"))
    height = cfg.get("STREAM_HEIGHT", os.getenv("STREAM_HEIGHT", "864"))
    fps = cfg.get("STREAM_FPS", os.getenv("STREAM_FPS", "15"))
    bitrate = cfg.get("STREAM_BITRATE", os.getenv("STREAM_BITRATE", "2000000"))
    rtsp_url = f"rtsp://localhost:{rtsp_port}/{stream_name}"

    rpicam_cmd = [
        "rpicam-vid",
        "-t",
        "0",
        "--width",
        width,
        "--height",
        height,
        "--framerate",
        fps,
        "--bitrate",
        bitrate,
        "--codec",
        "h264",
        "--libav-format",
        "rtsp",
        "--inline",
        "--profile",
        "baseline",
        "--intra",
        str(max(1, int(float(fps)))),
        "-n",
        "-o",
        rtsp_url,
    ]

    print(f"[stream] starting camera {width}x{height}@{fps} -> {rtsp_url}", flush=True)
    return subprocess.call(rpicam_cmd)


if __name__ == "__main__":
    sys.exit(main())
