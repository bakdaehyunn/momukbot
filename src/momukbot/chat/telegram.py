from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from momukbot.config import Settings
from momukbot.core.service import RecommendationService


class TelegramBot:
    def __init__(self, settings: Settings, service: RecommendationService) -> None:
        self.settings = settings
        self.service = service
        if not settings.telegram_bot_token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")

    def run_polling(self, poll_interval_sec: float = 1.0) -> None:
        offset: int | None = None
        while True:
            updates = self.get_updates(offset=offset, timeout=30)
            for update in updates:
                offset = max(offset or 0, int(update.get("update_id", 0)) + 1)
                self.handle_update(update)
            time.sleep(poll_interval_sec)

    def handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message")
        if not isinstance(message, dict):
            return
        chat = message.get("chat")
        text = message.get("text")
        if not isinstance(chat, dict) or not isinstance(text, str):
            return
        chat_id = str(chat.get("id") or "")
        if not self.is_allowed(chat_id):
            return
        result = self.service.handle_text(chat_id, text)
        if not result:
            return
        self.send_chat_action(chat_id, "typing")
        self.send_long_message(chat_id, result)

    def is_allowed(self, chat_id: str) -> bool:
        allowed = self.settings.telegram_allowed_chat_ids
        return not allowed or chat_id in allowed

    def get_updates(self, offset: int | None, timeout: int = 30) -> list[dict[str, Any]]:
        params: dict[str, str | int] = {"timeout": timeout}
        if offset is not None:
            params["offset"] = offset
        payload = self._api("getUpdates", params)
        result = payload.get("result")
        return result if isinstance(result, list) else []

    def send_message(self, chat_id: str, text: str) -> None:
        self._api(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": "true",
            },
            method="POST",
        )

    def send_long_message(self, chat_id: str, text: str) -> None:
        for chunk in chunk_text(text, 3500):
            self.send_message(chat_id, chunk)

    def send_chat_action(self, chat_id: str, action: str) -> None:
        self._api("sendChatAction", {"chat_id": chat_id, "action": action}, method="POST")

    def _api(self, method_name: str, params: dict[str, str | int], method: str = "GET") -> dict[str, Any]:
        url = f"https://api.telegram.org/bot{self.settings.telegram_bot_token}/{method_name}"
        data = None
        if method == "GET":
            url = f"{url}?{urlencode(params)}"
        else:
            data = urlencode(params).encode("utf-8")
        req = Request(url, data=data, method=method)
        with urlopen(req, timeout=35) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        if not isinstance(payload, dict) or not payload.get("ok"):
            raise RuntimeError(f"Telegram API failed: {payload}")
        return payload


def chunk_text(text: str, size: int) -> list[str]:
    if len(text) <= size:
        return [text]
    chunks: list[str] = []
    rest = text
    while len(rest) > size:
        window = rest[:size]
        cut = max(window.rfind("\n\n"), window.rfind("\n"), window.rfind(" "))
        if cut < 500:
            cut = size
        chunks.append(rest[:cut].strip())
        rest = rest[cut:].lstrip()
    if rest:
        chunks.append(rest)
    return chunks
