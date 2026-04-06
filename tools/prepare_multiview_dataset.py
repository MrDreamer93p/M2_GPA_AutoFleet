from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import cv2


IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff")
DEFAULT_ROBOT_IDS = ("R1", "R2", "R3")
DEFAULT_CAMERA_ORDER = {
    "a2d2": ("front_left", "front_center", "front_right"),
    "pandaset": ("front_left", "front_center", "front_right"),
}
CAMERA_CANDIDATES = {
    "a2d2": {
        "front_left": (
            "cam_front_left",
            "front_left",
            "camera_front_left",
            "cams/front_left",
            "camera/cam_front_left",
            "camera/front_left",
        ),
        "front_center": (
            "cam_front_center",
            "front_center",
            "camera_front_center",
            "cams/front_center",
            "camera/cam_front_center",
            "camera/front_center",
        ),
        "front_right": (
            "cam_front_right",
            "front_right",
            "camera_front_right",
            "cams/front_right",
            "camera/cam_front_right",
            "camera/front_right",
        ),
    },
    "pandaset": {
        "front_left": (
            "front_left_camera",
            "front_left",
            "camera/front_left_camera",
            "camera/front_left",
        ),
        "front_center": (
            "front_camera",
            "front_center_camera",
            "front_center",
            "camera/front_camera",
            "camera/front_center_camera",
        ),
        "front_right": (
            "front_right_camera",
            "front_right",
            "camera/front_right_camera",
            "camera/front_right",
        ),
    },
}


@dataclass
class CameraSequence:
    camera_name: str
    frame_paths: list[Path]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare A2D2/PandaSet multiview sequences for AutoFleet")
    parser.add_argument("--dataset", choices=("a2d2", "pandaset"), required=True)
    parser.add_argument("--dataset-root", required=True, help="Root folder of the extracted dataset")
    parser.add_argument(
        "--sequence",
        required=True,
        help="Sequence folder relative to dataset root. Example: 20180810_150607 or 001",
    )
    parser.add_argument(
        "--output-dir",
        default="data/artifacts/datasets/current",
        help="Target folder for the generated robot videos and manifest",
    )
    parser.add_argument("--fps", type=float, default=10.0)
    parser.add_argument("--max-frames", type=int, default=0, help="Optional cap on exported frames")
    parser.add_argument("--start-index", type=int, default=0)
    parser.add_argument(
        "--robot-ids",
        default=",".join(DEFAULT_ROBOT_IDS),
        help="Comma-separated robot IDs that will receive the prepared camera streams",
    )
    parser.add_argument(
        "--camera-order",
        default="",
        help="Override camera aliases order, comma-separated. Default: front_left,front_center,front_right",
    )
    return parser.parse_args()


def list_images(folder: Path) -> list[Path]:
    return sorted([p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS])


def recursive_image_dirs(root: Path) -> Iterable[Path]:
    for folder in root.rglob("*"):
        if folder.is_dir():
            images = list_images(folder)
            if images:
                yield folder


def resolve_camera_dir(sequence_root: Path, dataset: str, alias: str) -> Path:
    candidates = CAMERA_CANDIDATES[dataset][alias]
    for candidate in candidates:
        direct = sequence_root / candidate
        if direct.is_dir() and list_images(direct):
            return direct

    normalized_alias = alias.replace("_", "").lower()
    for folder in recursive_image_dirs(sequence_root):
        name = folder.name.replace("_", "").lower()
        parent = folder.parent.name.replace("_", "").lower()
        combined = f"{parent}/{name}"
        if normalized_alias in name or normalized_alias in combined:
            return folder
        for candidate in candidates:
            token = candidate.replace("_", "").lower()
            if token in name or token in combined:
                return folder

    raise FileNotFoundError(f"Could not locate image sequence for camera alias '{alias}' under {sequence_root}")


def load_camera_sequence(sequence_root: Path, dataset: str, alias: str, start_index: int, max_frames: int) -> CameraSequence:
    folder = resolve_camera_dir(sequence_root, dataset, alias)
    frames = list_images(folder)
    if start_index:
        frames = frames[start_index:]
    if max_frames > 0:
        frames = frames[:max_frames]
    if not frames:
        raise ValueError(f"No frames found for camera '{alias}' in {folder}")
    return CameraSequence(camera_name=alias, frame_paths=frames)


def ensure_uniform_size(image, target_size: tuple[int, int]):
    width, height = target_size
    if image.shape[1] == width and image.shape[0] == height:
        return image
    return cv2.resize(image, target_size, interpolation=cv2.INTER_LINEAR)


def write_video(frame_paths: list[Path], output_path: Path, fps: float) -> tuple[int, tuple[int, int]]:
    first = cv2.imread(str(frame_paths[0]))
    if first is None:
        raise ValueError(f"Failed to read first frame: {frame_paths[0]}")
    height, width = first.shape[:2]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(output_path), cv2.VideoWriter_fourcc(*"MJPG"), fps, (width, height))
    if not writer.isOpened():
        raise RuntimeError(f"Failed to open video writer for {output_path}")

    count = 0
    try:
        for frame_path in frame_paths:
            frame = cv2.imread(str(frame_path))
            if frame is None:
                continue
            frame = ensure_uniform_size(frame, (width, height))
            writer.write(frame)
            count += 1
    finally:
        writer.release()

    if count == 0:
        raise RuntimeError(f"No valid frames were written to {output_path}")
    return count, (width, height)


def as_file_url(output_dir: Path, filename: str) -> str:
    parts = list(output_dir.parts)
    if "artifacts" in parts:
        idx = parts.index("artifacts")
        rel = Path(*parts[idx + 1 :]) / filename
        container_path = Path("/artifacts") / rel
    else:
        container_path = Path("/artifacts/datasets") / output_dir.name / filename
    return f"file://{container_path.as_posix()}"


def main() -> int:
    args = parse_args()
    dataset_root = Path(args.dataset_root).expanduser().resolve()
    sequence_root = (dataset_root / args.sequence).resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    robot_ids = [x.strip() for x in args.robot_ids.split(",") if x.strip()]
    camera_order = [x.strip() for x in args.camera_order.split(",") if x.strip()] or list(DEFAULT_CAMERA_ORDER[args.dataset])

    if len(robot_ids) != len(camera_order):
        raise ValueError("robot count and camera count must match")
    if not sequence_root.exists():
        raise FileNotFoundError(f"Sequence path not found: {sequence_root}")

    sequences = [
        load_camera_sequence(sequence_root, args.dataset, camera_alias, args.start_index, args.max_frames)
        for camera_alias in camera_order
    ]
    frame_count = min(len(seq.frame_paths) for seq in sequences)
    if frame_count <= 0:
        raise ValueError("No overlapping frames available across the selected cameras")

    manifest: dict[str, object] = {
        "dataset": args.dataset,
        "dataset_root": str(dataset_root),
        "sequence": args.sequence,
        "fps": args.fps,
        "frame_count": frame_count,
        "robots": {},
    }

    for robot_id, sequence in zip(robot_ids, sequences):
        output_name = f"{robot_id}.avi"
        output_path = output_dir / output_name
        written, (width, height) = write_video(sequence.frame_paths[:frame_count], output_path, args.fps)
        manifest["robots"][robot_id] = {
            "camera_name": sequence.camera_name,
            "source_file": str(output_path),
            "source_url": as_file_url(output_dir, output_name),
            "video_view_profile": sequence.camera_name,
            "frames_written": written,
            "width": width,
            "height": height,
        }

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "multiview_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(f"Prepared {args.dataset} sequence '{args.sequence}' into {output_dir}")
    print(f"Manifest: {manifest_path}")
    for robot_id in robot_ids:
        robot_cfg = manifest["robots"][robot_id]
        print(f"{robot_id}: {robot_cfg['camera_name']} -> {robot_cfg['source_url']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
