from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import settings


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class JsonlStore:
    def __init__(self, log_dir: str = settings.log_dir, result_dir: str = settings.result_dir) -> None:
        self.log_dir = Path(log_dir)
        self.result_dir = Path(result_dir)
        self.artifact_dir = Path(settings.artifact_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.result_dir.mkdir(parents=True, exist_ok=True)
        self.artifact_dir.mkdir(parents=True, exist_ok=True)

    def append(self, stream_name: str, payload: dict[str, Any]) -> None:
        target = self.log_dir / f"{stream_name}.jsonl"
        enriched = {"ingested_at": _utc_iso(), **payload}
        with target.open("a", encoding="utf-8") as f:
            f.write(json.dumps(enriched, ensure_ascii=True) + os.linesep)

    def init_mission_result(self, mission_id: str) -> Path:
        mission_dir = self.result_dir / mission_id
        mission_dir.mkdir(parents=True, exist_ok=True)
        summary = mission_dir / "summary.json"
        if not summary.exists():
            summary.write_text(
                json.dumps(
                    {
                        "mission_id": mission_id,
                        "status": "INIT",
                        "note": "Result pipeline placeholder. Plug quantification job here.",
                        "created_at": _utc_iso(),
                    },
                    ensure_ascii=True,
                    indent=2,
                ),
                encoding="utf-8",
            )
        return mission_dir
