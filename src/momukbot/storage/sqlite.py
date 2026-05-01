from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from momukbot.core.json_utils import dumps
from momukbot.core.models import RecommendationItem


SCHEMA = """
CREATE TABLE IF NOT EXISTS recommendations (
  id TEXT PRIMARY KEY,
  chat_id TEXT NOT NULL,
  request_text TEXT NOT NULL,
  area TEXT NOT NULL,
  topic TEXT NOT NULL,
  place_name TEXT,
  category TEXT,
  status_marker TEXT,
  reason TEXT,
  links_json TEXT,
  search_keyword TEXT,
  raw_response TEXT,
  created_at TEXT NOT NULL
);
"""


class RecommendationStore:
    def __init__(self, state_dir: Path) -> None:
        self.path = state_dir / "momukbot.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    def add_result(
        self,
        chat_id: str,
        request_text: str,
        area: str,
        topic: str,
        search_keyword: str,
        raw_response: str,
        items: list[RecommendationItem],
    ) -> None:
        self.init_db()
        now = datetime.now().isoformat(timespec="seconds")
        with self.connect() as conn:
            if not items:
                conn.execute(
                    """
                    INSERT INTO recommendations(
                      id, chat_id, request_text, area, topic, search_keyword, raw_response, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (str(uuid.uuid4()), chat_id, request_text, area, topic, search_keyword, raw_response, now),
                )
                return
            for item in items:
                conn.execute(
                    """
                    INSERT INTO recommendations(
                      id, chat_id, request_text, area, topic, place_name, category,
                      status_marker, reason, links_json, search_keyword, raw_response, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        chat_id,
                        request_text,
                        area,
                        topic,
                        item.name,
                        item.category,
                        item.status_marker,
                        item.reason,
                        dumps(item.links),
                        search_keyword,
                        raw_response,
                        now,
                    ),
                )
