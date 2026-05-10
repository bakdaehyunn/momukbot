import json
from pathlib import Path

from momukbot.config import Settings
from momukbot.doctor import run_doctor


def test_doctor_reports_registered_momuk_chat_id(tmp_path: Path) -> None:
    tmp_path.joinpath("telegram_rooms.json").write_text(
        json.dumps({"momuk_chat_id": "-100999"}),
        encoding="utf-8",
    )

    code, text = run_doctor(make_settings(tmp_path, allowed_chat_ids=("123",)), FakeTelegramApi())

    assert code == 0
    assert "[OK] momuk_chat_id is registered: -100999" in text
    assert "allowed by runtime registration" in text


def test_doctor_reports_missing_momuk_chat_id(tmp_path: Path) -> None:
    code, text = run_doctor(make_settings(tmp_path, allowed_chat_ids=("123",)), FakeTelegramApi())

    assert code == 0
    assert "momuk_chat_id is not registered" in text


def test_doctor_reports_legacy_reminder_chat_id(tmp_path: Path) -> None:
    tmp_path.joinpath("telegram_rooms.json").write_text(
        json.dumps({"reminder_chat_id": "-100999"}),
        encoding="utf-8",
    )

    code, text = run_doctor(make_settings(tmp_path, allowed_chat_ids=("123",)), FakeTelegramApi())

    assert code == 0
    assert "legacy reminder_chat_id is present" in text


def test_doctor_checks_telegram_get_me_and_commands(tmp_path: Path) -> None:
    code, text = run_doctor(make_settings(tmp_path), FakeTelegramApi())

    assert code == 0
    assert "[OK] Telegram getMe: @momukbot" in text
    assert "[OK] Telegram command menu is synced" in text


def test_doctor_warns_when_telegram_commands_are_out_of_sync(tmp_path: Path) -> None:
    api = FakeTelegramApi(commands=[{"command": "start", "description": "Start"}])

    code, text = run_doctor(make_settings(tmp_path), api)

    assert code == 0
    assert "[WARN] Telegram command menu is out of sync" in text


class FakeTelegramApi:
    def __init__(self, commands: list[dict[str, str]] | None = None) -> None:
        self.commands = commands or [
            {"command": "chatid", "description": "현재 채팅방 ID 확인"},
            {"command": "set_momuk_room", "description": "현재 채팅방을 momukbot 채팅방으로 등록"},
        ]

    def get_me(self) -> dict[str, str]:
        return {"id": "1", "username": "momukbot"}

    def get_my_commands(self) -> list[dict[str, str]]:
        return self.commands


def make_settings(
    tmp: Path,
    allowed_chat_ids: tuple[str, ...] = (),
) -> Settings:
    return Settings(
        telegram_bot_token="token",
        telegram_allowed_chat_ids=allowed_chat_ids,
        telegram_admin_user_ids=("42",),
        naver_client_id="",
        naver_client_secret="",
        naver_daily_soft_limit=10,
        blog_allowed_domains=("blog.naver.com",),
        agent_provider="codex_cli",
        codex_bin="python3",
        codex_workdir=tmp,
        codex_sandbox="read-only",
        codex_timeout_sec=60,
        default_count=30,
        state_dir=tmp,
        log_dir=tmp,
    )
