from __future__ import annotations

import shutil
import subprocess

from momukbot.config import Settings
from momukbot.search.kakao import KakaoLocalCandidateProvider
from momukbot.search.naver import NaverBlogEvidenceProvider
from momukbot.telegram_ops import (
    DEFAULT_BOT_COMMANDS,
    EXPECTED_BOT_COMMANDS,
    REGISTERED_CHAT_BOT_COMMANDS,
    REGISTER_CHAT_ROOM_COMMAND,
    TelegramApiClient,
    chat_command_scope,
    command_menu_is_synced,
    format_legacy_room_conflict,
    legacy_room_was_copied_to_momuk,
    read_room_state,
)


def run_doctor(
    settings: Settings,
    telegram_api: TelegramApiClient | None = None,
    kakao_provider: KakaoLocalCandidateProvider | None = None,
) -> tuple[int, str]:
    lines: list[str] = []
    failures = 0

    if settings.telegram_bot_token:
        lines.append("[OK] TELEGRAM_BOT_TOKEN is set")
    else:
        lines.append("[WARN] TELEGRAM_BOT_TOKEN is not set")

    if settings.telegram_allowed_chat_ids:
        lines.append("[OK] TELEGRAM_ALLOWED_CHAT_IDS is set")
    elif settings.telegram_allow_all_chats:
        lines.append("[WARN] MOMUK_ALLOW_ALL_CHATS=true; every chat can use the bot")
    else:
        lines.append("[OK] TELEGRAM_ALLOWED_CHAT_IDS is empty; only explicitly registered momuk room can use the bot")

    if settings.telegram_admin_user_ids:
        lines.append("[OK] TELEGRAM_ADMIN_USER_IDS is set")
    else:
        lines.append("[WARN] TELEGRAM_ADMIN_USER_IDS is empty; admin Telegram commands are disabled")

    room_failures, room_lines = describe_telegram_room_state(settings)
    failures += room_failures
    lines.extend(room_lines)
    telegram_failures, telegram_lines = describe_telegram_api_state(settings, telegram_api)
    failures += telegram_failures
    lines.extend(telegram_lines)

    if settings.naver_client_id and settings.naver_client_secret:
        lines.append("[OK] Naver Blog API credentials are set")
    else:
        lines.append("[WARN] NAVER_CLIENT_ID/NAVER_CLIENT_SECRET are not set")

    if settings.kakao_rest_api_key:
        lines.append("[OK] KAKAO_REST_API_KEY is set")
        kakao = kakao_provider or KakaoLocalCandidateProvider(settings)
        try:
            kakao.check_connection()
            lines.append("[OK] Kakao Local API connection succeeded")
        except Exception as exc:
            failures += 1
            lines.append(f"[FAIL] Kakao Local API connection failed: {exc}")
    else:
        failures += 1
        lines.append("[FAIL] KAKAO_REST_API_KEY is not set")

    provider = NaverBlogEvidenceProvider(settings)
    quota = provider.quota.status()
    lines.append(
        f"[OK] Naver Blog quota status: date={quota.date} count={quota.count} "
        f"soft_limit={quota.soft_limit} remaining={quota.remaining}"
    )

    codex_path = shutil.which(settings.codex_bin)
    if codex_path:
        lines.append(f"[OK] Codex CLI found: {codex_path}")
        try:
            proc = subprocess.run(
                [settings.codex_bin, "--version"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
            )
            version = (proc.stdout or proc.stderr).strip().splitlines()
            if proc.returncode == 0 and version:
                lines.append(f"[OK] Codex CLI version: {version[0]}")
            else:
                lines.append("[WARN] Codex CLI exists but version check did not return cleanly")
        except Exception as exc:
            lines.append(f"[WARN] Codex CLI version check failed: {exc}")
    else:
        failures += 1
        lines.append(f"[FAIL] Codex CLI not found: {settings.codex_bin}")

    settings.state_dir.mkdir(parents=True, exist_ok=True)
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    lines.append(f"[OK] state_dir={settings.state_dir}")
    lines.append(f"[OK] log_dir={settings.log_dir}")

    return (1 if failures else 0), "\n".join(lines)


def describe_telegram_room_state(settings: Settings) -> tuple[int, list[str]]:
    state = read_room_state(settings)
    if state.unreadable_error:
        return 0, [f"[WARN] telegram room state is unreadable: {state.unreadable_error}"]

    if legacy_room_was_copied_to_momuk(state):
        return 1, [format_legacy_room_conflict(state)]

    if state.momuk_chat_id:
        lines = [f"[OK] momuk_chat_id is registered: {state.momuk_chat_id}"]
        if state.momuk_chat_id in settings.telegram_allowed_chat_ids:
            lines.append("[OK] momuk_chat_id is also listed in TELEGRAM_ALLOWED_CHAT_IDS")
        else:
            lines.append(
                "[OK] momuk_chat_id is allowed by runtime registration; TELEGRAM_ALLOWED_CHAT_IDS does not need to include it"
            )
        return 0, lines

    if state.legacy_reminder_chat_id:
        return 0, [
            f"[WARN] legacy reminder_chat_id is present but not used by momukbot; run {REGISTER_CHAT_ROOM_COMMAND}"
        ]

    return 0, [
        f"[WARN] momuk_chat_id is not registered; use {REGISTER_CHAT_ROOM_COMMAND} in the Telegram chat"
    ]


def describe_telegram_api_state(
    settings: Settings,
    telegram_api: TelegramApiClient | None = None,
) -> tuple[int, list[str]]:
    if not settings.telegram_bot_token:
        return 0, ["[WARN] Telegram API checks skipped because TELEGRAM_BOT_TOKEN is not set"]

    failures = 0
    lines: list[str] = []
    api = telegram_api or TelegramApiClient(settings.telegram_bot_token)

    try:
        me = api.get_me()
        result = me.get("result") if isinstance(me.get("result"), dict) else me
        username = ""
        if isinstance(result, dict):
            username = str(result.get("username") or "")
        lines.append(f"[OK] Telegram getMe: @{username}" if username else "[OK] Telegram getMe succeeded")
    except Exception as exc:
        failures += 1
        lines.append(f"[FAIL] Telegram getMe failed: {exc}")

    try:
        commands = api.get_my_commands()
        if command_menu_is_synced(commands, DEFAULT_BOT_COMMANDS):
            lines.append("[OK] Telegram default command menu is synced")
        else:
            expected = ", ".join(f"/{item['command']}" for item in EXPECTED_BOT_COMMANDS)
            actual = ", ".join(f"/{item.get('command', '')}" for item in commands) or "(empty)"
            lines.append(f"[WARN] Telegram default command menu is out of sync: expected={expected} actual={actual}")
    except Exception as exc:
        lines.append(f"[WARN] Telegram default command menu check failed: {exc}")

    state = read_room_state(settings)
    if state.momuk_chat_id and not legacy_room_was_copied_to_momuk(state):
        try:
            commands = api.get_my_commands(scope=chat_command_scope(state.momuk_chat_id))
            if command_menu_is_synced(commands, REGISTERED_CHAT_BOT_COMMANDS):
                lines.append("[OK] Telegram registered chat command menu is synced")
            else:
                expected = ", ".join(f"/{item['command']}" for item in REGISTERED_CHAT_BOT_COMMANDS)
                actual = ", ".join(f"/{item.get('command', '')}" for item in commands) or "(empty)"
                lines.append(
                    f"[WARN] Telegram registered chat command menu is out of sync: "
                    f"expected={expected} actual={actual}"
                )
        except Exception as exc:
            lines.append(f"[WARN] Telegram registered chat command menu check failed: {exc}")

    return failures, lines
