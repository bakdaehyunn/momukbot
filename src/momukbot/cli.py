from __future__ import annotations

import argparse
import shutil
import sys

from momukbot.chat.telegram import TelegramBot
from momukbot.config import DEFAULT_ENV_FILE, ROOT, get_settings, load_env
from momukbot.core.models import ParsedRequest
from momukbot.doctor import run_doctor
from momukbot.factory import build_service
from momukbot.search.naver import NaverSearchProvider
from momukbot.telegram_ops import (
    EXPECTED_BOT_COMMANDS,
    TelegramApiClient,
    format_rooms_report,
    format_setup_telegram_report,
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


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
