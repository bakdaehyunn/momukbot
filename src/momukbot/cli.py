from __future__ import annotations

import argparse
import json
import shutil
import sys

from momukbot.chat.telegram import TelegramBot
from momukbot.config import DEFAULT_ENV_FILE, ROOT, get_settings, load_env
from momukbot.core.formatter import format_recommendation_message
from momukbot.core.models import ParsedRequest, RecommendationItem
from momukbot.doctor import run_doctor
from momukbot.factory import build_service
from momukbot.search.naver import NaverSearchProvider
from momukbot.telegram_ops import (
    EXPECTED_BOT_COMMANDS,
    TelegramApiClient,
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

    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)
    settings = get_settings()

    if args.cmd == "init":
        return init_env()
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
            if not commands:
                print("(empty)")
            else:
                for item in commands:
                    print(f"/{item.get('command', '')} - {item.get('description', '')}")
            return 0
        if args.telegram_commands_cmd == "sync":
            try:
                api.set_my_commands(EXPECTED_BOT_COMMANDS)
            except Exception as exc:
                print(f"[FAIL] setMyCommands failed: {exc}")
                return 1
            print("synced Telegram command menu")
            return 0
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
    return 2


def init_env() -> int:
    example = ROOT / ".env.example"
    target = DEFAULT_ENV_FILE
    if target.exists():
        print(f"{target} already exists")
        return 0
    shutil.copyfile(example, target)
    print(f"created {target}")
    return 0


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
