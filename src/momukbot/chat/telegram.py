from __future__ import annotations

import json
import logging
import queue
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from momukbot.chat.nearby_state import NearbyState, PendingNearbySelection
from momukbot.config import Settings
from momukbot.core.models import RequestLocation
from momukbot.core.parser import parse_request
from momukbot.core.service import RecommendationService
from momukbot.telegram_ops import (
    LEGACY_REGISTER_CHAT_ROOM_COMMAND,
    NEARBY_COMMAND,
    REGISTERED_CHAT_BOT_COMMANDS,
    REGISTER_CHAT_ROOM_COMMAND,
    TelegramApiClient,
    chat_command_scope,
    is_chat_allowed,
    legacy_room_was_copied_to_momuk,
    read_room_state,
)


@dataclass(frozen=True)
class TelegramJob:
    chat_id: str
    text: str
    chat_type: str = ""
    location: RequestLocation | None = None


class TelegramBot:
    def __init__(
        self,
        settings: Settings,
        service: RecommendationService,
        api: TelegramApiClient | None = None,
    ) -> None:
        self.settings = settings
        self.service = service
        if not settings.telegram_bot_token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")
        self.api = api or TelegramApiClient(settings.telegram_bot_token)
        self.jobs: queue.Queue[TelegramJob] = queue.Queue()
        self.busy_chats: set[str] = set()
        self.busy_lock = threading.Lock()
        self.nearby_state = NearbyState()
        self.pending_location_text = self.nearby_state.pending_location_text
        self.pending_nearby_selection = self.nearby_state.pending_nearby_selection
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
        if not isinstance(chat, dict):
            return
        chat_id = str(chat.get("id") or "")
        chat_type = str(chat.get("type") or "")
        text = message.get("text")
        if isinstance(text, str):
            command = parse_command(text)
            if command in {"/chatid", REGISTER_CHAT_ROOM_COMMAND, LEGACY_REGISTER_CHAT_ROOM_COMMAND}:
                self.handle_admin_command(command, chat_id, chat, message, text)
                return
            if command == NEARBY_COMMAND:
                if not self.is_allowed(chat_id):
                    return
                nearby_text = format_nearby_command_text(text)
                self.nearby_state.request_location(chat_id, nearby_text)
                self.send_location_request(chat_id)
                return
            if command:
                return
        if not self.is_allowed(chat_id):
            return
        location = message.get("location")
        if isinstance(location, dict):
            latitude = _float_or_none(location.get("latitude"))
            longitude = _float_or_none(location.get("longitude"))
            if latitude is None or longitude is None:
                return
            self.nearby_state.start_selection(
                chat_id,
                RequestLocation(latitude=latitude, longitude=longitude),
                chat_type=chat_type,
            )
            self.send_nearby_first_choice_request(chat_id)
            return
        if not isinstance(text, str):
            return
        if self.handle_nearby_selection_text(chat_id, chat_type, text):
            return
        parsed = parse_request(text, default_count=self.settings.default_count)
        if parsed.intent == "needs_location":
            self.nearby_state.request_location(chat_id, text)
            self.send_location_request(chat_id)
            return
        self.enqueue_job(TelegramJob(chat_id=chat_id, text=text, chat_type=chat_type))

    def handle_admin_command(
        self,
        command: str,
        chat_id: str,
        chat: dict[str, Any],
        message: dict[str, Any],
        text: str = "",
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
        if command in {REGISTER_CHAT_ROOM_COMMAND, LEGACY_REGISTER_CHAT_ROOM_COMMAND}:
            if not self.can_overwrite_registered_room(chat_id, chat_title, text):
                return
            self.save_momuk_room(chat_id, chat_type, chat_title, message)
            self.send_message(
                chat_id,
                f"이 봇의 사용 방으로 등록했습니다.\nchat_id: {chat_id}\ntype: {chat_type}\ntitle: {chat_title}",
            )
            self.sync_registered_chat_commands(chat_id)

    def can_overwrite_registered_room(self, chat_id: str, chat_title: str, text: str) -> bool:
        state = read_room_state(self.settings)
        if not state.momuk_chat_id or legacy_room_was_copied_to_momuk(state):
            return True
        if state.momuk_chat_id == chat_id:
            return True
        if command_argument(text) == "confirm":
            return True
        current_title = state.momuk_chat_title or "(empty)"
        next_title = chat_title or "(empty)"
        self.send_message(
            chat_id,
            (
                "이미 다른 방이 이 봇의 사용 방으로 등록되어 있습니다.\n"
                f"기존: {state.momuk_chat_id} / {current_title}\n"
                f"새 방: {chat_id} / {next_title}\n"
                f"정말 바꾸려면 {REGISTER_CHAT_ROOM_COMMAND} confirm 을 보내주세요."
            ),
        )
        return False

    def sync_registered_chat_commands(self, chat_id: str) -> None:
        try:
            self.api.set_my_commands(REGISTERED_CHAT_BOT_COMMANDS, scope=chat_command_scope(chat_id))
        except Exception:
            self.logger.exception("failed to sync registered chat Telegram command menu")

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
                    self.logger.exception("telegram busy notice failed chat_id=%s", mask_chat_id(job.chat_id))
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
                self.logger.exception("telegram worker failed chat_id=%s", mask_chat_id(job.chat_id))
            finally:
                with self.busy_lock:
                    self.busy_chats.discard(job.chat_id)
                self.jobs.task_done()

    def process_job(self, job: TelegramJob) -> None:
        typing = self.start_chat_action(job.chat_id, "typing")
        try:
            start = time.monotonic()
            masked_chat_id = mask_chat_id(job.chat_id)
            self.logger.info("recommendation started chat_id=%s", masked_chat_id)
            if job.location is not None:
                result = self.service.handle_location(
                    job.chat_id,
                    latitude=job.location.latitude,
                    longitude=job.location.longitude,
                    text=job.text,
                )
            else:
                result = self.service.handle_text(job.chat_id, job.text)
            elapsed = time.monotonic() - start
            self.logger.info(
                "recommendation finished chat_id=%s elapsed=%.2fs",
                masked_chat_id,
                elapsed,
            )
        except Exception:
            self.logger.exception("recommendation failed chat_id=%s", mask_chat_id(job.chat_id))
            try:
                self.send_message(job.chat_id, "추천 생성 중 오류가 났어요. 잠시 후 다시 시도해주세요.")
            except Exception:
                self.logger.exception("telegram failure notice failed chat_id=%s", mask_chat_id(job.chat_id))
            return
        finally:
            typing.stop()
        if not result:
            if job.chat_type == "private":
                try:
                    self.send_message(job.chat_id, usage_guidance_message())
                except Exception:
                    self.logger.exception("telegram guidance send failed chat_id=%s", mask_chat_id(job.chat_id))
            return
        try:
            self.send_long_message(job.chat_id, result)
        except Exception:
            self.logger.exception("telegram send failed chat_id=%s", mask_chat_id(job.chat_id))

    def is_allowed(self, chat_id: str) -> bool:
        return is_chat_allowed(self.settings, chat_id)

    def get_updates(self, offset: int | None, timeout: int = 30) -> list[dict[str, Any]]:
        payload = self.api.get_updates(offset=offset, timeout=timeout, limit=None)
        result = payload.get("result")
        return result if isinstance(result, list) else []

    def send_message(self, chat_id: str, text: str, reply_markup: dict[str, Any] | None = None) -> None:
        self.api.send_message(chat_id, text, reply_markup=reply_markup)

    def send_location_request(self, chat_id: str) -> None:
        self.send_message(
            chat_id,
            (
                "현재 위치를 보내주세요. Telegram의 위치 공유 버튼으로 받은 좌표만 이번 추천에 사용합니다.\n"
                "버튼이 안 되면 모바일 Telegram에서 다시 시도하거나, 첨부 메뉴에서 위치를 직접 보내주세요."
            ),
            reply_markup=location_request_keyboard(),
        )

    def send_nearby_first_choice_request(self, chat_id: str) -> None:
        self.send_message(
            chat_id,
            "어떤 기준으로 찾을까요?",
            reply_markup=nearby_first_choice_keyboard(),
        )

    def send_nearby_second_choice_request(self, chat_id: str, first_choice: str) -> None:
        message = "식사 종류를 골라주세요." if first_choice == "식사" else "한잔하기 좋은 곳을 골라주세요."
        self.send_message(
            chat_id,
            message,
            reply_markup=nearby_second_choice_keyboard(first_choice),
        )

    def handle_nearby_selection_text(self, chat_id: str, chat_type: str, text: str) -> bool:
        selection = self.pending_nearby_selection.get(chat_id)
        if selection is None:
            return False
        choice = text.strip()
        if not selection.first_choice:
            if choice not in NEARBY_FIRST_CHOICES:
                self.nearby_state.clear_selection(chat_id)
                return False
            self.nearby_state.update_first_choice(chat_id, selection, choice, chat_type)
            self.send_nearby_second_choice_request(chat_id, choice)
            return True
        request_text = nearby_request_text(selection.first_choice, choice)
        if not request_text:
            self.nearby_state.clear_selection(chat_id)
            return False
        self.nearby_state.clear_selection(chat_id)
        self.enqueue_job(
            TelegramJob(
                chat_id=chat_id,
                text=request_text,
                chat_type=chat_type or selection.chat_type,
                location=selection.location,
            )
        )
        return True

    def send_long_message(self, chat_id: str, text: str) -> None:
        chunks = chunk_text(text, 3500)
        if len(chunks) == 1:
            self.send_message(chat_id, chunks[0])
            return
        total = len(chunks)
        for index, chunk in enumerate(chunks, start=1):
            self.send_message(chat_id, f"({index}/{total})\n{chunk}")

    def send_chat_action(self, chat_id: str, action: str) -> None:
        self.api.send_chat_action(chat_id, action)

    def start_chat_action(self, chat_id: str, action: str) -> "ChatActionLoop":
        loop = ChatActionLoop(lambda: self.send_chat_action(chat_id, action))
        loop.start()
        return loop

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


def command_argument(text: str) -> str:
    parts = text.strip().split(maxsplit=1)
    if len(parts) < 2:
        return ""
    return parts[1].strip().lower()


def format_nearby_command_text(text: str) -> str:
    argument = command_argument(text)
    if not argument:
        return "내 주변 맛집 추천"
    if "맛집" in argument or "식당" in argument or "밥" in argument:
        return f"내 주변 {argument} 추천"
    return f"내 주변 {argument} 맛집 추천"


NEARBY_FIRST_CHOICES = ("식사", "한잔")
NEARBY_SECOND_CHOICES: dict[str, tuple[str, ...]] = {
    "식사": ("한식", "중식", "일식", "양식", "패스트푸드", "아무거나"),
    "한잔": ("이자카야", "바", "포차", "아무거나"),
}
NEARBY_REQUEST_TEXTS: dict[tuple[str, str], str] = {
    ("식사", "한식"): "내 주변 한식 맛집 추천",
    ("식사", "중식"): "내 주변 중식 맛집 추천",
    ("식사", "일식"): "내 주변 일식 맛집 추천",
    ("식사", "양식"): "내 주변 양식 맛집 추천",
    ("식사", "패스트푸드"): "내 주변 패스트푸드 추천",
    ("식사", "아무거나"): "내 주변 맛집 추천",
    ("한잔", "이자카야"): "내 주변 이자카야 술집 추천",
    ("한잔", "바"): "내 주변 바 술집 추천",
    ("한잔", "포차"): "내 주변 포차 술집 추천",
    ("한잔", "아무거나"): "내 주변 술집 안주 맛집 추천",
}


def nearby_request_text(first_choice: str, second_choice: str) -> str:
    return NEARBY_REQUEST_TEXTS.get((first_choice.strip(), second_choice.strip()), "")


def nearby_first_choice_keyboard() -> dict[str, object]:
    return {
        "keyboard": _button_rows(NEARBY_FIRST_CHOICES, columns=2),
        "resize_keyboard": True,
        "one_time_keyboard": True,
    }


def nearby_second_choice_keyboard(first_choice: str) -> dict[str, object]:
    choices = NEARBY_SECOND_CHOICES.get(first_choice.strip(), ())
    return {
        "keyboard": _button_rows(choices, columns=3),
        "resize_keyboard": True,
        "one_time_keyboard": True,
    }


def _button_rows(choices: tuple[str, ...], columns: int) -> list[list[dict[str, str]]]:
    rows: list[list[dict[str, str]]] = []
    for index in range(0, len(choices), columns):
        rows.append([{"text": choice} for choice in choices[index : index + columns]])
    return rows


def location_request_keyboard() -> dict[str, object]:
    return {
        "keyboard": [[{"text": "현재 위치 보내기", "request_location": True}]],
        "resize_keyboard": True,
        "one_time_keyboard": True,
    }


def _float_or_none(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def chat_display_name(chat: dict[str, Any]) -> str:
    for key in ("title", "username", "first_name"):
        value = chat.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def usage_guidance_message() -> str:
    return "지역과 먹고 싶은 메뉴를 같이 보내주세요.\n예: 서면에서 해장 국밥 추천해줘"


def mask_chat_id(chat_id: str) -> str:
    text = str(chat_id)
    if len(text) <= 4:
        return "***"
    return "***" + text[-4:]


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
