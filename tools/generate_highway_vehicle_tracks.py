import argparse
import json
import math
from pathlib import Path

import cv2
import numpy as np


VEHICLE_LABELS = {"car", "bus", "truck", "motorbike", "motorcycle"}


def iou(a, b):
    ax1, ay1, aw, ah = a
    bx1, by1, bw, bh = b
    ax2, ay2 = ax1 + aw, ay1 + ah
    bx2, by2 = bx1 + bw, by1 + bh
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    union = aw * ah + bw * bh - inter
    return inter / union if union else 0.0


def center(box):
    x, y, w, h = box
    return x + w / 2, y + h / 2


def assign_tracks(frames, max_center_dist=90):
    active = []
    next_id = 1
    for frame in frames:
        detections = sorted(frame["detections"], key=lambda d: d["confidence"], reverse=True)
        used_tracks = set()
        for det in detections:
            box = det["bbox"]
            best = None
            best_score = -1
            cx, cy = center(box)
            for track in active:
                if track["id"] in used_tracks:
                    continue
                tcx, tcy = center(track["bbox"])
                dist = math.hypot(cx - tcx, cy - tcy)
                overlap = iou(box, track["bbox"])
                score = overlap * 2.0 + max(0, 1.0 - dist / max_center_dist)
                if dist < max_center_dist and score > best_score:
                    best = track
                    best_score = score
            if best is None:
                best = {"id": next_id, "bbox": box, "last_frame": frame["frame"]}
                next_id += 1
                active.append(best)
            best["bbox"] = box
            best["last_frame"] = frame["frame"]
            det["track_id"] = best["id"]
            used_tracks.add(best["id"])
        active = [track for track in active if frame["frame"] - track["last_frame"] <= 10]


def detect_video(video, cfg, weights, names, output, preview):
    labels = [line.strip() for line in Path(names).read_text(encoding="utf-8").splitlines() if line.strip()]
    net = cv2.dnn.readNetFromDarknet(str(cfg), str(weights))
    net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
    net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
    out_names = net.getUnconnectedOutLayersNames()

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video}")

    fps = float(cap.get(cv2.CAP_PROP_FPS) or 24.0)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frames = []
    preview_frames = []

    frame_idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        blob = cv2.dnn.blobFromImage(frame, 1 / 255.0, (416, 416), swapRB=True, crop=False)
        net.setInput(blob)
        layer_outputs = net.forward(out_names)

        boxes = []
        confidences = []
        class_ids = []
        for output_layer in layer_outputs:
            for row in output_layer:
                scores = row[5:]
                class_id = int(np.argmax(scores))
                confidence = float(scores[class_id])
                label = labels[class_id] if class_id < len(labels) else str(class_id)
                if confidence < 0.18 or label not in VEHICLE_LABELS:
                    continue
                cx, cy, w, h = row[:4] * np.array([width, height, width, height])
                x = int(cx - w / 2)
                y = int(cy - h / 2)
                boxes.append([x, y, int(w), int(h)])
                confidences.append(confidence)
                class_ids.append(class_id)

        keep = cv2.dnn.NMSBoxes(boxes, confidences, 0.18, 0.35)
        detections = []
        if len(keep):
            for k in np.array(keep).flatten():
                x, y, w, h = boxes[int(k)]
                if w < 14 or h < 10:
                    continue
                detections.append(
                    {
                        "label": labels[class_ids[int(k)]],
                        "confidence": round(float(confidences[int(k)]), 4),
                        "bbox": [
                            max(0, int(x)),
                            max(0, int(y)),
                            min(width - max(0, int(x)), int(w)),
                            min(height - max(0, int(y)), int(h)),
                        ],
                    }
                )

        frames.append({"frame": frame_idx, "t": round(frame_idx / fps, 4), "detections": detections})
        if frame_idx % max(1, total // 8) == 0:
            preview_frames.append((frame_idx, frame.copy(), detections))
        frame_idx += 1

    cap.release()
    assign_tracks(frames)

    payload = {
        "schema": "autofleet.highway_vehicle_tracks.v1",
        "source": str(video).replace("\\", "/"),
        "fps": fps,
        "width": width,
        "height": height,
        "frames": frames,
    }
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")

    if preview:
        thumbs = []
        for frame_idx, frame, detections in preview_frames:
            for det in detections:
                x, y, w, h = det["bbox"]
                cv2.rectangle(frame, (x, y), (x + w, y + h), (255, 98, 191), 2)
                cv2.putText(
                    frame,
                    f"{det.get('track_id', '?')} {det['label']} {det['confidence']:.2f}",
                    (x, max(14, y - 4)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.45,
                    (255, 255, 255),
                    1,
                    cv2.LINE_AA,
                )
            thumb = cv2.resize(frame, (320, 180))
            cv2.putText(thumb, f"frame {frame_idx}", (8, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
            thumbs.append(thumb)
        cols = 2
        rows = math.ceil(len(thumbs) / cols)
        sheet = np.zeros((rows * 180, cols * 320, 3), dtype=np.uint8)
        for i, thumb in enumerate(thumbs):
            r, c = divmod(i, cols)
            sheet[r * 180 : (r + 1) * 180, c * 320 : (c + 1) * 320] = thumb
        Path(preview).parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(preview), sheet)

    print(json.dumps({"frames": len(frames), "output": str(output), "preview": str(preview) if preview else None}, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", default="frontend/assets/highway-forward.mp4")
    parser.add_argument("--cfg", default="data/artifacts/models/yolov4-tiny.cfg")
    parser.add_argument("--weights", default="data/artifacts/models/yolov4-tiny.weights")
    parser.add_argument("--names", default="data/artifacts/models/coco.names")
    parser.add_argument("--output", default="frontend/assets/highway-vehicle-tracks.json")
    parser.add_argument("--preview", default="data/artifacts/highway_vehicle_tracks_preview.jpg")
    args = parser.parse_args()
    detect_video(Path(args.video), Path(args.cfg), Path(args.weights), Path(args.names), Path(args.output), Path(args.preview))


if __name__ == "__main__":
    main()
