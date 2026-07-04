from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from momukbot.core.observability import RecommendationEvent


class JsonlRecommendationEventRecorder:
    def __init__(self, log_dir: Path, filename: str = "recommendation-events.jsonl") -> None:
        self.path = log_dir / filename
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event: RecommendationEvent) -> None:
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(event.to_record(), ensure_ascii=False, sort_keys=True) + "\n")

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        records: list[dict[str, Any]] = []
        for line in lines[-max(1, limit) :]:
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                records.append(data)
        return records
