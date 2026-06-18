import argparse
import csv
import json
import math
import shutil
import subprocess
from pathlib import Path

import cv2


VEHICLE_TAGS = {"car", "van", "lorry", "truck", "bus", "motorbike", "motorcycle"}


DEFAULT_VIEWS = [
    {
        "robot_id": "R1",
        "role": "infrastructure north",
        "view": "Infrastructure/0",
        "label": "MTID infrastructure seq0",
        "sync_group": "seq3-0000",
        "camera_label": "INFRA CAMERA",
    },
    {
        "robot_id": "R2",
        "role": "drone overhead",
        "view": "Drone/0",
        "label": "MTID drone seq0",
        "sync_group": "seq3-0000",
        "camera_label": "DRONE CAMERA",
    },
    {
        "robot_id": "R3",
        "role": "infrastructure east",
        "view": "Infrastructure/1000",
        "label": "MTID infrastructure seq1000",
        "sync_group": "seq3-1000",
        "camera_label": "INFRA CAMERA",
    },
]


def dataset_root():
    try:
        import kagglehub

        return Path(kagglehub.dataset_download("andreasmoegelmose/multiview-traffic-intersection-dataset"))
    except Exception:
        return (
            Path.home()
            / ".cache"
            / "kagglehub"
            / "datasets"
            / "andreasmoegelmose"
            / "multiview-traffic-intersection-dataset"
            / "versions"
            / "1"
        )


def image_frame_number(path):
    digits = "".join(ch for ch in path.stem.split("_")[-1] if ch.isdigit())
    return int(digits or 0)


def parse_annotations(csv_path, frame_names, out_width, out_height, src_width, src_height):
    boxes_by_name = {name: [] for name in frame_names}
    scale_x = out_width / src_width
    scale_y = out_height / src_height
    with csv_path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        reader = csv.reader(handle)
        next(reader, None)
        for raw_row in reader:
            if not raw_row:
                continue
            row = ";".join(raw_row).split(";")
            if len(row) < 7:
                continue
            rgb_file = Path(row[1]).name
            if rgb_file not in boxes_by_name:
                continue
            tag = row[3].strip()
            if tag.lower() not in VEHICLE_TAGS:
                continue
            points_raw = row[6].strip()
            if not points_raw:
                continue
            values = [float(v) for v in points_raw.split() if v.strip()]
            if len(values) < 4 or len(values) % 2:
                continue
            xs = values[0::2]
            ys = values[1::2]
            x1 = max(0, min(xs)) * scale_x
            y1 = max(0, min(ys)) * scale_y
            x2 = min(src_width, max(xs)) * scale_x
            y2 = min(src_height, max(ys)) * scale_y
            w = max(1, x2 - x1)
            h = max(1, y2 - y1)
            if w * h < 90:
                continue
            boxes_by_name[rgb_file].append(
                {
                    "label": tag,
                    "confidence": 1.0,
                    "track_id": row[2].strip(),
                    "bbox": [round(x1, 1), round(y1, 1), round(w, 1), round(h, 1)],
                    "source": "mtid-annotation",
                }
            )
    return boxes_by_name


def run_ffmpeg(frame_pattern, start_number, frames, fps, width, output):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required to build browser-playable H.264 MTID clips")
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-framerate",
        str(fps),
        "-start_number",
        str(start_number),
        "-i",
        str(frame_pattern),
        "-frames:v",
        str(frames),
        "-vf",
        f"scale={width}:-2",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-tune",
        "zerolatency",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(output),
    ]
    subprocess.run(cmd, check=True)


def prepare_view(root, out_dir, view, frames, fps, width):
    source_dir = root / view["view"]
    jpgs = sorted(source_dir.glob("*.jpg"), key=image_frame_number)[:frames]
    if not jpgs:
        raise RuntimeError(f"No JPG frames found in {source_dir}")
    first = cv2.imread(str(jpgs[0]))
    if first is None:
        raise RuntimeError(f"Cannot read {jpgs[0]}")
    src_height, src_width = first.shape[:2]
    out_height = int(round(src_height * (width / src_width)))
    out_height += out_height % 2
    frame_names = [p.name for p in jpgs]
    boxes_by_name = parse_annotations(source_dir / "annotations.csv", frame_names, width, out_height, src_width, src_height)
    frames_payload = []
    for idx, path in enumerate(jpgs):
        frames_payload.append({"frame": idx, "t": round(idx / fps, 4), "detections": boxes_by_name.get(path.name, [])})

    video_name = f"mtid-{view['robot_id'].lower()}-{view['view'].replace('/', '-').lower()}.mp4"
    tracks_name = f"mtid-{view['robot_id'].lower()}-{view['view'].replace('/', '-').lower()}-tracks.json"
    video_out = out_dir / video_name
    tracks_out = out_dir / tracks_name
    pattern = source_dir / f"{jpgs[0].stem.rsplit('_', 1)[0]}_%07d.jpg"
    run_ffmpeg(pattern, image_frame_number(jpgs[0]), frames, fps, width, video_out)
    payload = {
        "schema": "autofleet.vehicle_tracks.v1",
        "source": str(source_dir).replace("\\", "/"),
        "source_type": "mtid-annotation",
        "fps": fps,
        "width": width,
        "height": out_height,
        "frames": frames_payload,
    }
    tracks_out.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    return {
        "robot_id": view["robot_id"],
        "label": view["label"],
        "role": view["role"],
        "camera_label": view["camera_label"],
        "sync_group": view["sync_group"],
        "video_url": f"./assets/mtid/{video_name}",
        "tracks_url": f"./assets/mtid/{tracks_name}",
        "source_type": "mtid-annotation",
        "view": view["view"],
        "frames": len(frames_payload),
    }


def main():
    parser = argparse.ArgumentParser(description="Prepare a small real MTID multiview demo pack for the AutoFleet frontend.")
    parser.add_argument("--dataset-root", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=Path("frontend/assets/mtid"))
    parser.add_argument("--manifest", type=Path, default=Path("frontend/assets/vehicle-multiview.json"))
    parser.add_argument("--frames", type=int, default=180)
    parser.add_argument("--fps", type=int, default=24)
    parser.add_argument("--width", type=int, default=960)
    ns = parser.parse_args()
    root = ns.dataset_root or dataset_root()
    if not root.exists():
        raise RuntimeError(f"MTID dataset root not found: {root}")
    ns.out_dir.mkdir(parents=True, exist_ok=True)
    views = [prepare_view(root, ns.out_dir, view, ns.frames, ns.fps, ns.width) for view in DEFAULT_VIEWS]
    manifest = {
        "schema": "autofleet.vehicle_multiview.v1",
        "name": "MTID real multiview vehicle clips",
        "dataset": "Multi-view Traffic Intersection Dataset",
        "source_url": "https://www.kaggle.com/datasets/andreasmoegelmose/multiview-traffic-intersection-dataset",
        "note": "R1/R2 are synchronized MTID seq3-0000 infrastructure/drone views. R3 is a separate MTID infrastructure segment, not a cloned feed.",
        "views": views,
    }
    ns.manifest.parent.mkdir(parents=True, exist_ok=True)
    ns.manifest.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({"manifest": str(ns.manifest), "views": views}, indent=2))


if __name__ == "__main__":
    main()
