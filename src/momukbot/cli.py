from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from momukbot.chat.telegram import TelegramBot
from momukbot.config import DEFAULT_ENV_FILE, ROOT, get_settings, load_env
from momukbot.core.models import ParsedRequest
from momukbot.doctor import run_doctor
from momukbot.factory import build_service
from momukbot.search.naver import NaverSearchProvider


def main(argv: list[str] | None = None) -> int:
    load_env()
    parser = argparse.ArgumentParser(prog="momuk")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="Create .env from .env.example")
    sub.add_parser("doctor", help="Check Telegram, Naver, Codex, and local state settings")

    p_recommend = sub.add_parser("recommend", help="Run a recommendation from the CLI")
    p_recommend.add_argument("--area", required=True)
    p_recommend.add_argument("--topic", default="")
    p_recommend.add_argument("--count", type=int, default=None)
    p_recommend.add_argument("--dry-run", action="store_true")

    sub.add_parser("telegram", help="Run Telegram polling bot")
    sub.add_parser("quota", help="Show Naver API quota status")

    args = parser.parse_args(argv)
    settings = get_settings()

    if args.cmd == "init":
        return init_env()
    if args.cmd == "doctor":
        code, text = run_doctor(settings)
        print(text)
        return code
    if args.cmd == "recommend":
        service = build_service(settings, persist=not args.dry_run)
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
