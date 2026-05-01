from __future__ import annotations

import fcntl
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


class QuotaExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class QuotaStatus:
    date: str
    count: int
    soft_limit: int
    remaining: int
    configured: bool


class JsonQuotaGuard:
    def __init__(self, state_dir: Path, soft_limit: int, configured: bool, name: str = "naver") -> None:
        self.state_dir = state_dir
        self.soft_limit = soft_limit
        self.configured = configured
        self.file = state_dir / f"{name}-quota.json"
        self.lock_file = state_dir / f"{name}-quota.lock"

    def status(self) -> QuotaStatus:
        today = datetime.now().strftime("%Y-%m-%d")
        data = self._read()
        if data.get("date") != today:
            data = {"date": today, "count": 0}
        count = int(data.get("count") or 0)
        return QuotaStatus(
            date=today,
            count=count,
            soft_limit=self.soft_limit,
            remaining=max(0, self.soft_limit - count),
            configured=self.configured,
        )

    def reserve(self, endpoint: str, query: str, units: int = 1) -> None:
        if self.soft_limit <= 0:
            raise QuotaExceeded("daily soft limit is disabled or set to zero")
        self.state_dir.mkdir(parents=True, exist_ok=True)
        today = datetime.now().strftime("%Y-%m-%d")
        with self.lock_file.open("w", encoding="utf-8") as lock_fp:
            fcntl.flock(lock_fp.fileno(), fcntl.LOCK_EX)
            data = self._read()
            if data.get("date") != today:
                data = {"date": today, "count": 0}
            count = int(data.get("count") or 0)
            if count + units > self.soft_limit:
                raise QuotaExceeded(f"daily soft limit reached ({count}/{self.soft_limit})")
            data.update(
                {
                    "date": today,
                    "count": count + units,
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                    "last_endpoint": endpoint,
                    "last_query": query[:120],
                }
            )
            tmp = self.file.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.file)

    def _read(self) -> dict[str, Any]:
        if not self.file.exists():
            return {}
        try:
            data = json.loads(self.file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
