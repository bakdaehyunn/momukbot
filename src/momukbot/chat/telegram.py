from __future__ import annotations

import json
import logging
import queue
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from momukbot.config import Settings
from momukbot.core.service import RecommendationService
from momukbot.telegram_ops import is_chat_allowed


@dataclass(frozen=True)
class TelegramJob:
    chat_id: str
    text: str


class TelegramBot:
    def __init__(self, settings: Settings, service: RecommendationService) -> None:
        self.settings = settings
        self.service = service
        if not settings.telegram_bot_token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")
        self.jobs: queue.Queue[TelegramJob] = queue.Queue()
        self.busy_chats: set[str] = set()
        self.busy_lock = threading.Lock()
        self.worker_started = False
        self.worker_thread: threading.Thread | None = None
        self.logger = build_logger(settings)

    def run_polling(self, poll_interval_sec: float = 1.0) -> None:
        self.start_worker()
        offset: int | None = None
        while True:
            try:
                updates = self.get_updates(offset=offset, timeout=30)
                for update in updates:
                    offset = max(offset or 0, int(update.get("update_id", 0)) + 1)
                    self.handle_update(update)
            except Exception:
                self.logger.exception("telegram polling failed")
                time.sleep(max(1.0, poll_interval_sec))
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
        command = parse_command(text)
        if command in {"/chatid", "/set_momuk_room"}:
            self.handle_admin_command(command, chat_id, chat, message)
            return
        if command:
            return
        if not self.is_allowed(chat_id):
            return
        self.enqueue_job(TelegramJob(chat_id=chat_id, text=text))

    def handle_admin_command(
        self,
        command: str,
        chat_id: str,
        chat: dict[str, Any],
        message: dict[str, Any],
    ) -> None:
        if not self.is_admin_message(message):
            return
        chat_type = str(chat.get("type") or "")
        chat_title = chat_display_name(chat)
        if command == "/chatid":
            self.send_message(
                chat_id,
                f"chat_id: {chat_id}\ntype: {chat_type}\ntitle: {chat_title}",
            )
            return
        if command == "/set_momuk_room":
            self.save_momuk_room(chat_id, chat_type, chat_title, message)
            self.send_message(
                chat_id,
                f"momukbot 채팅방으로 등록했습니다.\nchat_id: {chat_id}\ntype: {chat_type}\ntitle: {chat_title}",
            )

    def is_admin_message(self, message: dict[str, Any]) -> bool:
        allowed = self.settings.telegram_admin_user_ids
        if not allowed:
            return False
        user = message.get("from")
        if not isinstance(user, dict):
            return False
        user_id = str(user.get("id") or "")
        return user_id in allowed

    def save_momuk_room(
        self,
        chat_id: str,
        chat_type: str,
        chat_title: str,
        message: dict[str, Any],
    ) -> None:
        user = message.get("from")
        user_id = ""
        if isinstance(user, dict):
            user_id = str(user.get("id") or "")
        self.settings.state_dir.mkdir(parents=True, exist_ok=True)
        path = self.settings.state_dir / "telegram_rooms.json"
        data = {
            "momuk_chat_id": chat_id,
            "momuk_chat_title": chat_title,
            "momuk_chat_type": chat_type,
            "registered_by_user_id": user_id,
            "registered_at": datetime.now(timezone.utc).isoformat(),
        }
        tmp_path = path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp_path.replace(path)

    def enqueue_job(self, job: TelegramJob) -> None:
        with self.busy_lock:
            if job.chat_id in self.busy_chats:
                try:
                    self.send_message(job.chat_id, "이전 추천 요청을 처리 중입니다. 잠시 후 다시 보내주세요.")
                except Exception:
                    self.logger.exception("telegram busy notice failed chat_id=%s", job.chat_id)
                return
            self.busy_chats.add(job.chat_id)
        self.jobs.put(job)

    def start_worker(self) -> None:
        if self.worker_started:
            return
        self.worker_started = True
        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker_thread.start()

    def _worker_loop(self) -> None:
        while True:
            job = self.jobs.get()
            try:
                self.process_job(job)
            except Exception:
                self.logger.exception("telegram worker failed chat_id=%s", job.chat_id)
            finally:
                with self.busy_lock:
                    self.busy_chats.discard(job.chat_id)
                self.jobs.task_done()

    def process_job(self, job: TelegramJob) -> None:
        typing = self.start_chat_action(job.chat_id, "typing")
        try:
            start = time.monotonic()
            self.logger.info("recommendation started chat_id=%s", job.chat_id)
            result = self.service.handle_text(job.chat_id, job.text)
            elapsed = time.monotonic() - start
            self.logger.info("recommendation finished chat_id=%s elapsed=%.2fs", job.chat_id, elapsed)
        except Exception:
            self.logger.exception("recommendation failed chat_id=%s", job.chat_id)
            try:
                self.send_message(job.chat_id, "추천 생성 중 오류가 났어요. 잠시 후 다시 시도해주세요.")
            except Exception:
                self.logger.exception("telegram failure notice failed chat_id=%s", job.chat_id)
            return
        finally:
            typing.stop()
        if not result:
            return
        try:
            self.send_long_message(job.chat_id, result)
        except Exception:
            self.logger.exception("telegram send failed chat_id=%s", job.chat_id)

    def is_allowed(self, chat_id: str) -> bool:
        return is_chat_allowed(self.settings, chat_id)

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

    def start_chat_action(self, chat_id: str, action: str) -> "ChatActionLoop":
        loop = ChatActionLoop(lambda: self.send_chat_action(chat_id, action))
        loop.start()
        return loop

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


class ChatActionLoop:
    def __init__(self, send_action: Callable[[], None], interval_sec: float = 4.0) -> None:
        self.send_action = send_action
        self.interval_sec = interval_sec
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._send_safely()
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=0.2)

    def _run(self) -> None:
        while not self._stop.wait(self.interval_sec):
            self._send_safely()

    def _send_safely(self) -> None:
        try:
            self.send_action()
        except Exception:
            pass


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


def parse_command(text: str) -> str:
    token = text.strip().split(maxsplit=1)[0] if text.strip() else ""
    command = token.split("@", 1)[0].lower()
    return command if command.startswith("/") else ""


def chat_display_name(chat: dict[str, Any]) -> str:
    for key in ("title", "username", "first_name"):
        value = chat.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def build_logger(settings: Settings) -> logging.Logger:
    logger = logging.getLogger("momukbot.telegram")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if logger.handlers:
        return logger
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(settings.log_dir / "telegram.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger
