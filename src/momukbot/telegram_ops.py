from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from momukbot.config import Settings


EXPECTED_BOT_COMMANDS = [
    {"command": "chatid", "description": "현재 채팅방 ID 확인"},
    {"command": "set_chat_room", "description": "현재 채팅방을 이 봇의 사용 방으로 등록"},
]
REGISTER_CHAT_ROOM_COMMAND = "/set_chat_room"
LEGACY_REGISTER_CHAT_ROOM_COMMAND = "/set_momuk_room"


@dataclass(frozen=True)
class TelegramRoomState:
    momuk_chat_id: str = ""
    momuk_chat_title: str = ""
    momuk_chat_type: str = ""
    registered_by_user_id: str = ""
    registered_at: str = ""
    legacy_reminder_chat_id: str = ""
    unreadable_error: str = ""


@dataclass(frozen=True)
class TelegramChatCandidate:
    chat_id: str
    title: str
    chat_type: str


class TelegramApiClient:
    def __init__(self, token: str) -> None:
        self.token = token

    def get_me(self) -> dict[str, Any]:
        return self._api("getMe")

    def get_updates(
        self,
        offset: int | None = None,
        timeout: int | None = None,
        limit: int | None = 100,
    ) -> dict[str, Any]:
        params: dict[str, str | int] = {}
        if offset is not None:
            params["offset"] = offset
        if timeout is not None:
            params["timeout"] = timeout
        if limit is not None:
            params["limit"] = limit
        request_timeout = (timeout + 5) if timeout is not None else 15
        return self._api("getUpdates", params, request_timeout=request_timeout)

    def get_chat(self, chat_id: str) -> dict[str, Any]:
        payload = self._api("getChat", {"chat_id": chat_id})
        result = payload.get("result")
        return result if isinstance(result, dict) else {}

    def get_my_commands(self) -> list[dict[str, str]]:
        payload = self._api("getMyCommands")
        result = payload.get("result")
        if not isinstance(result, list):
            return []
        commands: list[dict[str, str]] = []
        for item in result:
            if not isinstance(item, dict):
                continue
            commands.append(
                {
                    "command": str(item.get("command") or ""),
                    "description": str(item.get("description") or ""),
                }
            )
        return commands

    def set_my_commands(self, commands: list[dict[str, str]]) -> None:
        self._api("setMyCommands", {"commands": json.dumps(commands, ensure_ascii=False)}, method="POST")

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

    def send_chat_action(self, chat_id: str, action: str) -> None:
        self._api("sendChatAction", {"chat_id": chat_id, "action": action}, method="POST")

    def _api(
        self,
        method_name: str,
        params: dict[str, str | int] | None = None,
        method: str = "GET",
        request_timeout: int = 15,
    ) -> dict[str, Any]:
        if not self.token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")
        params = params or {}
        url = f"https://api.telegram.org/bot{self.token}/{method_name}"
        data = None
        if method == "GET":
            if params:
                url = f"{url}?{urlencode(params)}"
        else:
            data = urlencode(params).encode("utf-8")
        req = Request(url, data=data, method=method)
        with urlopen(req, timeout=request_timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        if not isinstance(payload, dict) or not payload.get("ok"):
            raise RuntimeError(f"Telegram API failed: {payload}")
        return payload


def read_room_state(settings: Settings) -> TelegramRoomState:
    path = settings.state_dir / "telegram_rooms.json"
    if not path.exists():
        return TelegramRoomState()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return TelegramRoomState(unreadable_error=str(exc))
    if not isinstance(payload, dict):
        return TelegramRoomState(unreadable_error="telegram room state is not a JSON object")
    return TelegramRoomState(
        momuk_chat_id=str(payload.get("momuk_chat_id") or "").strip(),
        momuk_chat_title=str(payload.get("momuk_chat_title") or "").strip(),
        momuk_chat_type=str(payload.get("momuk_chat_type") or "").strip(),
        registered_by_user_id=str(payload.get("registered_by_user_id") or "").strip(),
        registered_at=str(payload.get("registered_at") or "").strip(),
        legacy_reminder_chat_id=str(payload.get("reminder_chat_id") or "").strip(),
    )


def load_momuk_chat_id(settings: Settings) -> str:
    state = read_room_state(settings)
    if legacy_room_was_copied_to_momuk(state):
        return ""
    return state.momuk_chat_id


def allowed_chat_ids(settings: Settings) -> set[str]:
    allowed = set(settings.telegram_allowed_chat_ids)
    momuk_chat_id = load_momuk_chat_id(settings)
    if momuk_chat_id:
        allowed.add(momuk_chat_id)
    return allowed


def is_chat_allowed(settings: Settings, chat_id: str) -> bool:
    allowed = allowed_chat_ids(settings)
    if allowed:
        return chat_id in allowed
    return settings.telegram_allow_all_chats


def command_menu_is_synced(commands: list[dict[str, str]]) -> bool:
    normalized = [
        {
            "command": str(item.get("command") or ""),
            "description": str(item.get("description") or ""),
        }
        for item in commands
    ]
    return normalized == EXPECTED_BOT_COMMANDS


def legacy_room_was_copied_to_momuk(state: TelegramRoomState) -> bool:
    return bool(
        state.momuk_chat_id
        and state.legacy_reminder_chat_id
        and state.momuk_chat_id == state.legacy_reminder_chat_id
    )


def format_legacy_room_conflict(state: TelegramRoomState) -> str:
    title = state.momuk_chat_title or "(empty)"
    chat_type = state.momuk_chat_type or "(empty)"
    return (
        "[FAIL] legacy reminder_chat_id matches momuk_chat_id; "
        "this looks like stale honsanam reminder state. "
        f"Clear the stale room state and run {REGISTER_CHAT_ROOM_COMMAND} in the correct momukbot chat. "
        f"title={title} type={chat_type}"
    )


def discover_chat_candidates(payload: dict[str, Any]) -> list[TelegramChatCandidate]:
    result = payload.get("result", [])
    if not isinstance(result, list):
        return []

    candidates: list[TelegramChatCandidate] = []
    seen: set[str] = set()
    for update in result:
        if not isinstance(update, dict):
            continue
        for key in (
            "message",
            "edited_message",
            "channel_post",
            "edited_channel_post",
            "my_chat_member",
            "chat_member",
        ):
            event = update.get(key)
            if not isinstance(event, dict):
                continue
            chat = event.get("chat")
            if not isinstance(chat, dict):
                continue
            raw_chat_id = chat.get("id")
            if raw_chat_id is None:
                continue
            chat_id = str(raw_chat_id)
            if chat_id in seen:
                continue
            seen.add(chat_id)
            chat_type = str(chat.get("type") or "")
            title = str(chat.get("title") or chat.get("username") or chat.get("first_name") or chat_type or chat_id)
            candidates.append(TelegramChatCandidate(chat_id=chat_id, title=title, chat_type=chat_type))
    return candidates


def format_rooms_report(settings: Settings) -> tuple[int, str]:
    state = read_room_state(settings)
    lines: list[str] = []
    if state.unreadable_error:
        return 1, f"[FAIL] telegram room state is unreadable: {state.unreadable_error}"
    if legacy_room_was_copied_to_momuk(state):
        return 1, format_legacy_room_conflict(state)
    if state.momuk_chat_id:
        allowed = "yes" if is_chat_allowed(settings, state.momuk_chat_id) else "no"
        lines.extend(
            [
                f"momuk_chat_id={state.momuk_chat_id}",
                f"title={state.momuk_chat_title or '(empty)'}",
                f"type={state.momuk_chat_type or '(empty)'}",
                f"registered_by_user_id={state.registered_by_user_id or '(empty)'}",
                f"registered_at={state.registered_at or '(empty)'}",
                f"allowed={allowed}",
            ]
        )
    else:
        lines.append("[WARN] momuk_chat_id is not registered")
    if state.legacy_reminder_chat_id and not state.momuk_chat_id:
        lines.append("[WARN] legacy reminder_chat_id is present but not used by momukbot")
    return 0, "\n".join(lines)


def format_setup_telegram_report(
    settings: Settings,
    api: TelegramApiClient | None = None,
) -> tuple[int, str]:
    lines: list[str] = []
    failures = 0

    if settings.telegram_bot_token:
        lines.append("[OK] TELEGRAM_BOT_TOKEN is set")
    else:
        failures += 1
        lines.append("[TODO] Set TELEGRAM_BOT_TOKEN in .env")

    if settings.telegram_admin_user_ids:
        lines.append("[OK] TELEGRAM_ADMIN_USER_IDS is set")
    else:
        failures += 1
        lines.append("[TODO] Set TELEGRAM_ADMIN_USER_IDS in .env")

    state = read_room_state(settings)
    if legacy_room_was_copied_to_momuk(state):
        failures += 1
        lines.append(format_legacy_room_conflict(state))
    elif state.momuk_chat_id:
        lines.append(f"[OK] momuk room is registered: {state.momuk_chat_id}")
    else:
        failures += 1
        lines.append(f"[TODO] Send {REGISTER_CHAT_ROOM_COMMAND} in the Telegram chat where momukbot should work")

    if state.legacy_reminder_chat_id and not state.momuk_chat_id:
        lines.append("[WARN] legacy reminder_chat_id exists; momukbot ignores it")

    if settings.telegram_bot_token:
        try:
            api = api or TelegramApiClient(settings.telegram_bot_token)
            commands = api.get_my_commands()
            if command_menu_is_synced(commands):
                lines.append("[OK] Telegram command menu is synced")
            else:
                failures += 1
                lines.append("[TODO] Run: momuk telegram-commands sync")
        except Exception as exc:
            failures += 1
            lines.append(f"[WARN] Could not check Telegram command menu: {exc}")

    if not failures:
        lines.append("Telegram setup looks ready")
    return (1 if failures else 0), "\n".join(lines)
