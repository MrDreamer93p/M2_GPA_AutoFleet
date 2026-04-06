from __future__ import annotations

import argparse
import shutil
import urllib.request
from pathlib import Path


DEFAULT_URL = "https://raw.githubusercontent.com/opencv/opencv/master/samples/data/vtest.avi"
DEFAULT_OUTPUT = Path("data/artifacts/demo/vtest.avi")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download a public demo video for AutoFleet")
    parser.add_argument("--url", default=DEFAULT_URL, help="Public video URL to download")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Target file path")
    parser.add_argument("--force", action="store_true", help="Redownload even when the target file already exists")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target = Path(args.output).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not args.force:
        size_mb = target.stat().st_size / (1024 * 1024)
        print(f"demo video already present at {target} ({size_mb:.2f} MiB), skipping download")
        return 0
    print(f"downloading demo video to {target}")
    with urllib.request.urlopen(args.url) as response, target.open("wb") as out:
        shutil.copyfileobj(response, out)
    size_mb = target.stat().st_size / (1024 * 1024)
    print(f"downloaded {size_mb:.2f} MiB from {args.url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
