from __future__ import annotations

import argparse
import getpass
import json
import os
import shutil
import sys
from pathlib import Path

from momukbot.chat.telegram import TelegramBot
from momukbot.config import DEFAULT_ENV_FILE, ROOT, get_settings, load_env
from momukbot.core.formatter import format_recommendation_message
from momukbot.core.models import ParsedRequest, RecommendationItem
from momukbot.doctor import run_doctor
from momukbot.factory import build_service
from momukbot.search.naver import NaverSearchProvider
from momukbot.storage.sqlite import RecommendationStore
from momukbot.telegram_ops import (
    DEFAULT_BOT_COMMANDS,
    REGISTERED_CHAT_BOT_COMMANDS,
    TelegramApiClient,
    chat_command_scope,
    discover_chat_candidates,
    format_rooms_report,
    format_setup_telegram_report,
    legacy_room_was_copied_to_momuk,
    read_room_state,
)


def main(argv: list[str] | None = None) -> int:
    load_env()
    parser = argparse.ArgumentParser(prog="momuk")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="Create .env from .env.example")
    p_setup = sub.add_parser("setup", help="Configure local env and first-run checks")
    p_setup.add_argument("--dry-run", action="store_true")
    p_setup.add_argument("--non-interactive", action="store_true")
    p_setup.add_argument("--telegram-bot-token")
    p_setup.add_argument("--telegram-allowed-chat-ids")
    p_setup.add_argument("--telegram-admin-user-ids")
    p_setup.add_argument("--allow-all-chats", action="store_true")
    p_setup.add_argument("--naver-client-id")
    p_setup.add_argument("--naver-client-secret")
    p_setup.add_argument("--codex-bin")
    p_setup.add_argument("--sync-telegram-commands", action="store_true")
    sub.add_parser("doctor", help="Check Telegram, Naver, Codex, and local state settings")

    p_recommend = sub.add_parser("recommend", help="Run a recommendation from the CLI")
    p_recommend.add_argument("text", nargs="?")
    p_recommend.add_argument("--area", default="")
    p_recommend.add_argument("--topic", default="")
    p_recommend.add_argument("--count", type=int, default=None)
    p_recommend.add_argument("--dry-run", action="store_true")

    sub.add_parser("rooms", help="Show registered Telegram momuk room status")

    p_discover_chat = sub.add_parser("discover-chat", help="Find Telegram chat id from recent bot updates")
    p_discover_chat.add_argument("--plain", action="store_true")
    p_discover_chat.add_argument("--json", action="store_true")

    p_send_test = sub.add_parser("send-test", help="Send one explicit Telegram test message")
    p_send_test.add_argument("--chat-id")
    p_send_test.add_argument("--registered", action="store_true", help="Use the registered momuk room")
    p_send_test.add_argument("--allowed", action="store_true", help="Use the only TELEGRAM_ALLOWED_CHAT_IDS value")
    p_send_test.add_argument("--dry-run", action="store_true")

    p_commands = sub.add_parser("telegram-commands", help="Show or sync Telegram bot command menu")
    commands_sub = p_commands.add_subparsers(dest="telegram_commands_cmd", required=True)
    commands_sub.add_parser("show", help="Show current Telegram bot command menu")
    commands_sub.add_parser("sync", help="Sync Telegram bot command menu")

    sub.add_parser("setup-telegram", help="Guide Telegram setup steps")

    sub.add_parser("telegram", help="Run Telegram polling bot")
    sub.add_parser("quota", help="Show Naver API quota status")

    p_history = sub.add_parser("history", help="Manage local recommendation history")
    history_sub = p_history.add_subparsers(dest="history_cmd", required=True)
    p_history_clear = history_sub.add_parser("clear", help="Delete local recommendation history")
    p_history_clear.add_argument("--yes", action="store_true")

    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)
    settings = get_settings()

    if args.cmd == "init":
        return init_env()
    if args.cmd == "setup":
        return setup_cmd(
            settings,
            dry_run=args.dry_run,
            non_interactive=args.non_interactive,
            telegram_bot_token=args.telegram_bot_token,
            telegram_allowed_chat_ids=args.telegram_allowed_chat_ids,
            telegram_admin_user_ids=args.telegram_admin_user_ids,
            allow_all_chats=args.allow_all_chats,
            naver_client_id=args.naver_client_id,
            naver_client_secret=args.naver_client_secret,
            codex_bin=args.codex_bin,
            sync_telegram_commands=args.sync_telegram_commands,
        )
    if args.cmd == "doctor":
        code, text = run_doctor(settings)
        print(text)
        return code
    if args.cmd == "recommend":
        if args.text and (args.area or args.topic or args.count is not None):
            print(
                "momuk recommend: error: cannot use natural text together with --area, --topic, or --count",
                file=sys.stderr,
            )
            return 2
        if not args.text and not args.area:
            print("momuk recommend: error: provide natural text or --area", file=sys.stderr)
            return 2
        service = build_service(settings, persist=not args.dry_run)
        if args.text:
            result = service.handle_text("cli", args.text, dry_run=args.dry_run)
            if not result:
                print("요청을 이해하지 못했어요. 예: `서면에서 해장 국밥 추천해줘`")
                return 1
            print(result)
            return 0
        parsed = ParsedRequest(
            intent="start",
            area=args.area,
            topic=args.topic,
            count=max(1, min(30, args.count or settings.default_count)),
        )
        print(
            service.recommend(
                chat_id="cli",
                request_text=f"{args.area} {args.topic}".strip(),
                parsed=parsed,
                dry_run=args.dry_run,
            )
        )
        return 0
    if args.cmd == "rooms":
        code, text = format_rooms_report(settings)
        print(text)
        return code
    if args.cmd == "discover-chat":
        return discover_chat_cmd(settings, plain=args.plain, json_output=args.json)
    if args.cmd == "send-test":
        return send_test_cmd(
            settings,
            chat_id=args.chat_id,
            use_registered=args.registered,
            use_allowed=args.allowed,
            dry_run=args.dry_run,
        )
    if args.cmd == "telegram-commands":
        if not settings.telegram_bot_token:
            print("[FAIL] TELEGRAM_BOT_TOKEN is not set")
            return 1
        api = TelegramApiClient(settings.telegram_bot_token)
        if args.telegram_commands_cmd == "show":
            try:
                commands = api.get_my_commands()
            except Exception as exc:
                print(f"[FAIL] getMyCommands failed: {exc}")
                return 1
            print("[default]")
            print_commands(commands)
            state = read_room_state(settings)
            if state.momuk_chat_id and not legacy_room_was_copied_to_momuk(state):
                try:
                    chat_commands = api.get_my_commands(scope=chat_command_scope(state.momuk_chat_id))
                except Exception as exc:
                    print(f"[FAIL] getMyCommands for registered chat failed: {exc}")
                    return 1
                print(f"[registered chat {state.momuk_chat_id}]")
                print_commands(chat_commands)
            return 0
        if args.telegram_commands_cmd == "sync":
            return sync_telegram_commands_cmd(settings)
    if args.cmd == "setup-telegram":
        api = TelegramApiClient(settings.telegram_bot_token) if settings.telegram_bot_token else None
        code, text = format_setup_telegram_report(settings, api)
        print(text)
        return code
    if args.cmd == "telegram":
        service = build_service(settings, persist=True)
        TelegramBot(settings, service).run_polling()
        return 0
    if args.cmd == "quota":
        status = NaverSearchProvider(settings).quota.status()
        print(
            f"configured={status.configured} date={status.date} count={status.count} "
            f"soft_limit={status.soft_limit} remaining={status.remaining}"
        )
        return 0
    if args.cmd == "history":
        if args.history_cmd == "clear":
            if not args.yes:
                print("momuk history clear: error: pass --yes to delete local history", file=sys.stderr)
                return 2
            deleted = RecommendationStore(settings.state_dir).clear()
            print(f"deleted {deleted} recommendation history rows")
            return 0
    return 2


def print_commands(commands: list[dict[str, str]]) -> None:
    if not commands:
        print("(empty)")
        return
    for item in commands:
        print(f"/{item.get('command', '')} - {item.get('description', '')}")


def init_env() -> int:
    example = ROOT / ".env.example"
    target = current_env_file()
    if target.exists():
        print(f"{target} already exists")
        return 0
    shutil.copyfile(example, target)
    print(f"created {target}")
    return 0


def setup_cmd(
    settings,
    dry_run: bool,
    non_interactive: bool,
    telegram_bot_token: str | None,
    telegram_allowed_chat_ids: str | None,
    telegram_admin_user_ids: str | None,
    allow_all_chats: bool,
    naver_client_id: str | None,
    naver_client_secret: str | None,
    codex_bin: str | None,
    sync_telegram_commands: bool,
) -> int:
    env_file = current_env_file()
    print("==> Creating local env if needed")
    if dry_run:
        print(f"[dry-run] ensure {env_file}")
    elif not env_file.exists():
        shutil.copyfile(ROOT / ".env.example", env_file)
        print(f"created {env_file}")
    else:
        print(f"{env_file} already exists")

    env = read_env_values(env_file)
    token = choose_setup_value(
        "Telegram bot token",
        env.get("TELEGRAM_BOT_TOKEN", ""),
        telegram_bot_token,
        secret=True,
        non_interactive=non_interactive,
    )
    allowed_chat_ids = choose_setup_value(
        "Telegram allowed chat ids",
        env.get("TELEGRAM_ALLOWED_CHAT_IDS", ""),
        telegram_allowed_chat_ids,
        secret=False,
        non_interactive=non_interactive,
    )
    admin_user_ids = choose_setup_value(
        "Telegram admin user ids",
        env.get("TELEGRAM_ADMIN_USER_IDS", ""),
        telegram_admin_user_ids,
        secret=False,
        non_interactive=non_interactive,
    )
    allow_all = choose_setup_bool(
        "Allow every Telegram chat",
        current=env.get("MOMUK_ALLOW_ALL_CHATS", "false").lower() in {"1", "true", "yes", "y", "on"},
        provided=allow_all_chats,
        non_interactive=non_interactive,
    )
    naver_id = choose_setup_value(
        "Naver client id",
        env.get("NAVER_CLIENT_ID", ""),
        naver_client_id,
        secret=False,
        non_interactive=non_interactive,
    )
    naver_secret = choose_setup_value(
        "Naver client secret",
        env.get("NAVER_CLIENT_SECRET", ""),
        naver_client_secret,
        secret=True,
        non_interactive=non_interactive,
    )
    codex = choose_setup_value(
        "Codex CLI command",
        env.get("CODEX_BIN", settings.codex_bin or "codex"),
        codex_bin,
        secret=False,
        non_interactive=non_interactive,
    )

    if token and not allowed_chat_ids and not allow_all:
        discovered_chat_id = discover_allowed_chat_for_setup(token, dry_run=dry_run, non_interactive=non_interactive)
        if discovered_chat_id:
            allowed_chat_ids = discovered_chat_id

    missing = []
    if not token:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not naver_id:
        missing.append("NAVER_CLIENT_ID")
    if not naver_secret:
        missing.append("NAVER_CLIENT_SECRET")
    if not codex:
        missing.append("CODEX_BIN")
    if missing and not dry_run:
        print(f"[FAIL] required setup values are missing: {', '.join(missing)}")
        return 1
    if missing:
        print(f"[dry-run] required values would be checked: {', '.join(missing)}")

    values = dict(env)
    values.update(
        {
            "TELEGRAM_BOT_TOKEN": token,
            "TELEGRAM_ALLOWED_CHAT_IDS": allowed_chat_ids,
            "TELEGRAM_ADMIN_USER_IDS": admin_user_ids,
            "MOMUK_ALLOW_ALL_CHATS": "true" if allow_all else "false",
            "NAVER_CLIENT_ID": naver_id,
            "NAVER_CLIENT_SECRET": naver_secret,
            "CODEX_BIN": codex,
        }
    )

    print("==> Writing configuration")
    if dry_run:
        print(f"[dry-run] write {env_file}")
    else:
        write_setup_env(env_file, values)
        apply_env_values(values)

    configured = get_settings()
    should_sync = sync_telegram_commands
    if not non_interactive and not sync_telegram_commands and token:
        should_sync = ask_yes_no("Sync Telegram command menu now?", default=True)
    if should_sync:
        print("==> Syncing Telegram command menu")
        if dry_run:
            print("[dry-run] momuk telegram-commands sync")
        else:
            sync_status = sync_telegram_commands_cmd(configured)
            if sync_status != 0:
                return sync_status
    else:
        print("skipped Telegram command menu sync")

    print("==> Checking configuration")
    if dry_run:
        print("[dry-run] momuk doctor")
        doctor_code = 0
    else:
        api = TelegramApiClient(configured.telegram_bot_token) if configured.telegram_bot_token else None
        doctor_code, doctor_text = run_doctor(configured, api)
        print(doctor_text)

    print("==> Readiness checklist")
    print(f"[OK] env_file={env_file}")
    if allowed_chat_ids:
        print(f"[OK] Telegram allowed chat ids configured: {allowed_chat_ids}")
        test_hint = (
            "momuk send-test --allowed --dry-run"
            if "," not in allowed_chat_ids
            else "momuk send-test --chat-id <telegram-chat-id> --dry-run"
        )
    elif allow_all:
        print("[WARN] all Telegram chats are allowed for testing")
        test_hint = "momuk send-test --chat-id <telegram-chat-id> --dry-run"
    else:
        print("[TODO] Run `momuk telegram`, then send `/set_chat_room` from an admin Telegram user")
        test_hint = "momuk send-test --registered --dry-run"
    print(f"Next verification command: {test_hint}")

    if doctor_code != 0:
        print("setup configured but not ready; fix the doctor items above.")
        return doctor_code
    print("setup complete")
    return 0


def sync_telegram_commands_cmd(settings) -> int:
    if not settings.telegram_bot_token:
        print("[FAIL] TELEGRAM_BOT_TOKEN is not set")
        return 1
    api = TelegramApiClient(settings.telegram_bot_token)
    try:
        api.set_my_commands(DEFAULT_BOT_COMMANDS)
        state = read_room_state(settings)
        synced_scoped = False
        if state.momuk_chat_id and not legacy_room_was_copied_to_momuk(state):
            api.set_my_commands(
                REGISTERED_CHAT_BOT_COMMANDS,
                scope=chat_command_scope(state.momuk_chat_id),
            )
            synced_scoped = True
    except Exception as exc:
        print(f"[FAIL] setMyCommands failed: {exc}")
        return 1
    if synced_scoped:
        print("synced Telegram command menu: default and registered chat")
    else:
        print("synced Telegram command menu: default")
    return 0


def current_env_file() -> Path:
    return Path(os.environ.get("MOMUK_ENV_FILE", DEFAULT_ENV_FILE)).expanduser()


def read_env_values(env_file: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not env_file.exists():
        return values
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def write_setup_env(env_file: Path, values: dict[str, str]) -> None:
    ordered_keys = [
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_ALLOWED_CHAT_IDS",
        "TELEGRAM_ADMIN_USER_IDS",
        "MOMUK_ALLOW_ALL_CHATS",
        "MOMUK_STORE_RAW_RESPONSE",
        "NAVER_CLIENT_ID",
        "NAVER_CLIENT_SECRET",
        "NAVER_DAILY_SOFT_LIMIT",
        "BLOG_ALLOWED_DOMAINS",
        "AGENT_PROVIDER",
        "CODEX_BIN",
        "CODEX_WORKDIR",
        "CODEX_SANDBOX",
        "CODEX_TIMEOUT_SEC",
        "MOMUK_DEFAULT_COUNT",
        "MOMUK_STATE_DIR",
        "MOMUK_LOG_DIR",
    ]
    defaults = read_env_values(ROOT / ".env.example")
    merged = {**defaults, **values}
    lines = [f"{key}={merged.get(key, '')}" for key in ordered_keys]
    extras = sorted(key for key in merged if key not in ordered_keys)
    lines.extend(f"{key}={merged[key]}" for key in extras)
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def apply_env_values(values: dict[str, str]) -> None:
    for key, value in values.items():
        if key:
            os.environ[key] = value


def choose_setup_value(
    label: str,
    current: str,
    provided: str | None,
    secret: bool,
    non_interactive: bool,
) -> str:
    if provided is not None:
        return provided.strip()
    if non_interactive:
        return current.strip()
    return prompt_setup_value(label, current, secret=secret)


def prompt_setup_value(label: str, current: str, secret: bool) -> str:
    if current:
        prompt = f"{label} [configured]: " if secret else f"{label} [{current}]: "
    else:
        prompt = f"{label}: "
    value = getpass.getpass(prompt) if secret else input(prompt)
    return current if not value else value.strip()


def choose_setup_bool(label: str, current: bool, provided: bool, non_interactive: bool) -> bool:
    if provided:
        return True
    if non_interactive:
        return current
    return ask_yes_no(label, default=current)


def ask_yes_no(question: str, default: bool) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    answer = input(f"{question} {suffix}: ").strip().lower()
    if not answer:
        return default
    return answer in {"y", "yes"}


def discover_allowed_chat_for_setup(token: str, dry_run: bool, non_interactive: bool) -> str:
    print("Telegram chat id can be found automatically after the bot receives one message.")
    if dry_run:
        print("[dry-run] momuk discover-chat --plain")
        return ""
    if not non_interactive and not ask_yes_no("Try Telegram chat auto discovery?", default=True):
        return ""
    if not non_interactive:
        input("Send any message to the Telegram chat that should use momukbot, then press Enter here.")
    try:
        payload = TelegramApiClient(token).get_updates()
        candidates = discover_chat_candidates(payload)
    except Exception as exc:
        print(f"could not find Telegram chat automatically: {exc}")
        return ""
    if not candidates:
        print("could not find Telegram chat automatically")
        return ""
    latest = candidates[-1]
    print("found Telegram chat candidate")
    print(f"chat_id: {latest.chat_id}")
    print(f"title: {latest.title}")
    print(f"type: {latest.chat_type or '(empty)'}")
    if non_interactive or ask_yes_no("Use this chat as TELEGRAM_ALLOWED_CHAT_IDS?", default=True):
        return latest.chat_id
    return ""


def discover_chat_cmd(settings, plain: bool, json_output: bool) -> int:
    if not settings.telegram_bot_token:
        print("[FAIL] TELEGRAM_BOT_TOKEN is not set")
        return 1
    api = TelegramApiClient(settings.telegram_bot_token)
    try:
        candidates = discover_chat_candidates(api.get_updates())
    except Exception as exc:
        print(f"[FAIL] Telegram getUpdates failed: {exc}")
        return 1
    if not candidates:
        print("[FAIL] no Telegram chat found. Send a message to the bot, then run this command again.")
        return 1
    if json_output:
        print(json.dumps([candidate.__dict__ for candidate in candidates], ensure_ascii=False, indent=2))
        return 0
    latest = candidates[-1]
    if plain:
        print(latest.chat_id)
        return 0
    print(f"chat_id: {latest.chat_id}")
    print(f"title: {latest.title}")
    print(f"type: {latest.chat_type or '(empty)'}")
    return 0


def send_test_cmd(
    settings,
    chat_id: str | None,
    use_registered: bool,
    use_allowed: bool,
    dry_run: bool,
) -> int:
    if not settings.telegram_bot_token:
        print("[FAIL] TELEGRAM_BOT_TOKEN is not set")
        return 1
    target_count = sum(1 for value in (chat_id, use_registered, use_allowed) if bool(value))
    if target_count != 1:
        print(
            "momuk send-test: error: choose exactly one target: --chat-id, --registered, or --allowed",
            file=sys.stderr,
        )
        return 2

    target_chat_id = ""
    if chat_id:
        target_chat_id = chat_id
    elif use_registered:
        state = read_room_state(settings)
        if legacy_room_was_copied_to_momuk(state):
            print("[FAIL] registered momuk room is stale legacy reminder state")
            return 1
        if not state.momuk_chat_id:
            print("[FAIL] momuk room is not registered")
            return 1
        target_chat_id = state.momuk_chat_id
    elif use_allowed:
        if len(settings.telegram_allowed_chat_ids) != 1:
            print("[FAIL] --allowed requires exactly one TELEGRAM_ALLOWED_CHAT_IDS value")
            return 1
        target_chat_id = settings.telegram_allowed_chat_ids[0]

    api = TelegramApiClient(settings.telegram_bot_token)
    try:
        chat = api.get_chat(target_chat_id)
    except Exception as exc:
        print(f"[FAIL] Telegram getChat failed: {exc}")
        return 1
    if isinstance(chat.get("result"), dict):
        chat = chat["result"]
    target_title = str(chat.get("title") or chat.get("username") or chat.get("first_name") or "(empty)")
    target_type = str(chat.get("type") or "(empty)")
    print(f"target_chat_id={target_chat_id}")
    print(f"target_title={target_title}")
    print(f"target_type={target_type}")

    message = telegram_test_message()
    if dry_run:
        print("dry-run: message was not sent")
        return 0
    try:
        api.send_message(target_chat_id, message)
    except Exception as exc:
        print(f"[FAIL] Telegram sendMessage failed: {exc}")
        return 1
    print("sent test message")
    return 0


def telegram_test_message() -> str:
    sample = format_recommendation_message(
        "서면 해장",
        [
            RecommendationItem(
                name="송정3대국밥",
                category="국밥",
                status_marker="영업 가능성 높음",
                reason="블로그 후기가 많고 해장 메뉴 언급이 반복됩니다.",
                links=[{"label": "블로그", "url": "https://blog.naver.com/a/b"}],
            ),
            RecommendationItem(
                name="청진동감자탕",
                category="감자탕",
                status_marker="영업시간 미확인",
                reason="뼈해장국 후보로 언급됩니다.",
            ),
        ],
        area="서면",
    )
    return f"뭐먹봇 Telegram 테스트\n\n{sample}"


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
