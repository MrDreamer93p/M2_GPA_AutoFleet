from __future__ import annotations

import argparse
import shutil
import urllib.request
from pathlib import Path


PRESETS = {
    "highway_forward": {
        "url": "https://assets.mixkit.co/videos/42368/42368-720.mp4",
        "output": Path("data/artifacts/demo/highway-forward.mp4"),
        "description": "Forward-facing highway driving view that better matches a robot or vehicle camera.",
    },
    "opencv_aerial": {
        "url": "https://raw.githubusercontent.com/opencv/opencv/master/samples/data/vtest.avi",
        "output": Path("data/artifacts/demo/vtest.avi"),
        "description": "Legacy OpenCV overhead sample kept as a fallback.",
    },
}
DEFAULT_PRESET = "highway_forward"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download a public demo video for AutoFleet")
    parser.add_argument("--preset", choices=sorted(PRESETS.keys()), default=DEFAULT_PRESET, help="Named demo source")
    parser.add_argument("--url", default=None, help="Public video URL to download")
    parser.add_argument("--output", default=None, help="Target file path")
    parser.add_argument("--list-presets", action="store_true", help="Print the available preset names and exit")
    parser.add_argument("--force", action="store_true", help="Redownload even when the target file already exists")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.list_presets:
        for name, meta in PRESETS.items():
            print(f"{name}: {meta['description']} ({meta['url']})")
        return 0

    preset = PRESETS[args.preset]
    source_url = args.url or str(preset["url"])
    target = Path(args.output or preset["output"]).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not args.force:
        size_mb = target.stat().st_size / (1024 * 1024)
        print(f"demo video already present at {target} ({size_mb:.2f} MiB), skipping download")
        return 0
    print(f"downloading demo video to {target}")
    request = urllib.request.Request(source_url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request) as response, target.open("wb") as out:
        shutil.copyfileobj(response, out)
    size_mb = target.stat().st_size / (1024 * 1024)
    print(f"downloaded {size_mb:.2f} MiB from {source_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
