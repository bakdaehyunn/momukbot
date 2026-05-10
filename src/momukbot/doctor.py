from __future__ import annotations

import shutil
import subprocess

from momukbot.config import Settings
from momukbot.search.naver import NaverSearchProvider
from momukbot.telegram_ops import (
    EXPECTED_BOT_COMMANDS,
    TelegramApiClient,
    command_menu_is_synced,
    read_room_state,
)


def run_doctor(
    settings: Settings,
    telegram_api: TelegramApiClient | None = None,
) -> tuple[int, str]:
    lines: list[str] = []
    failures = 0

    if settings.telegram_bot_token:
        lines.append("[OK] TELEGRAM_BOT_TOKEN is set")
    else:
        lines.append("[WARN] TELEGRAM_BOT_TOKEN is not set")

    if settings.telegram_allowed_chat_ids:
        lines.append("[OK] TELEGRAM_ALLOWED_CHAT_IDS is set")
    else:
        lines.append("[WARN] TELEGRAM_ALLOWED_CHAT_IDS is empty; every chat can use the bot")

    if settings.telegram_admin_user_ids:
        lines.append("[OK] TELEGRAM_ADMIN_USER_IDS is set")
    else:
        lines.append("[WARN] TELEGRAM_ADMIN_USER_IDS is empty; admin Telegram commands are disabled")

    lines.extend(describe_telegram_room_state(settings))
    telegram_failures, telegram_lines = describe_telegram_api_state(settings, telegram_api)
    failures += telegram_failures
    lines.extend(telegram_lines)

    if settings.naver_client_id and settings.naver_client_secret:
        lines.append("[OK] Naver API credentials are set")
    else:
        lines.append("[WARN] NAVER_CLIENT_ID/NAVER_CLIENT_SECRET are not set")

    provider = NaverSearchProvider(settings)
    quota = provider.quota.status()
    lines.append(
        f"[OK] Naver quota status: date={quota.date} count={quota.count} "
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


def describe_telegram_room_state(settings: Settings) -> list[str]:
    state = read_room_state(settings)
    if state.unreadable_error:
        return [f"[WARN] telegram room state is unreadable: {state.unreadable_error}"]

    if state.momuk_chat_id:
        lines = [f"[OK] momuk_chat_id is registered: {state.momuk_chat_id}"]
        if state.momuk_chat_id in settings.telegram_allowed_chat_ids:
            lines.append("[OK] momuk_chat_id is also listed in TELEGRAM_ALLOWED_CHAT_IDS")
        else:
            lines.append(
                "[OK] momuk_chat_id is allowed by runtime registration; TELEGRAM_ALLOWED_CHAT_IDS does not need to include it"
            )
        return lines

    if state.legacy_reminder_chat_id:
        return [
            "[WARN] legacy reminder_chat_id is present but not used by momukbot; run /set_momuk_room"
        ]

    return [
        "[WARN] momuk_chat_id is not registered; use /set_momuk_room in the Telegram chat"
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
        if command_menu_is_synced(commands):
            lines.append("[OK] Telegram command menu is synced")
        else:
            expected = ", ".join(f"/{item['command']}" for item in EXPECTED_BOT_COMMANDS)
            actual = ", ".join(f"/{item.get('command', '')}" for item in commands) or "(empty)"
            lines.append(f"[WARN] Telegram command menu is out of sync: expected={expected} actual={actual}")
    except Exception as exc:
        lines.append(f"[WARN] Telegram command menu check failed: {exc}")

    return failures, lines
