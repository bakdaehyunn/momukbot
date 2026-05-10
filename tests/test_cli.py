import json
from pathlib import Path

import pytest

from momukbot import cli


@pytest.fixture(autouse=True)
def clean_momuk_env(monkeypatch) -> None:
    for key in (
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_ALLOWED_CHAT_IDS",
        "TELEGRAM_ADMIN_USER_IDS",
        "MOMUK_STATE_DIR",
        "MOMUK_LOG_DIR",
        "CODEX_BIN",
    ):
        monkeypatch.delenv(key, raising=False)


def test_rooms_shows_registered_momuk_chat_and_allowed_status(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    env_file = write_env(
        tmp_path,
        telegram_allowed_chat_ids="123",
        state_dir=tmp_path,
        log_dir=tmp_path,
    )
    tmp_path.joinpath("telegram_rooms.json").write_text(
        json.dumps(
            {
                "momuk_chat_id": "-100999",
                "momuk_chat_title": "맛집추천방",
                "momuk_chat_type": "supergroup",
                "registered_by_user_id": "42",
                "registered_at": "2026-05-10T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MOMUK_ENV_FILE", str(env_file))

    code = cli.main(["rooms"])

    out = capsys.readouterr().out
    assert code == 0
    assert "momuk_chat_id=-100999" in out
    assert "title=맛집추천방" in out
    assert "allowed=yes" in out


def test_rooms_warns_on_legacy_reminder_chat_id(tmp_path: Path, monkeypatch, capsys) -> None:
    env_file = write_env(tmp_path, telegram_allowed_chat_ids="123", state_dir=tmp_path, log_dir=tmp_path)
    tmp_path.joinpath("telegram_rooms.json").write_text(
        json.dumps({"reminder_chat_id": "-100999"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("MOMUK_ENV_FILE", str(env_file))

    code = cli.main(["rooms"])

    out = capsys.readouterr().out
    assert code == 0
    assert "legacy reminder_chat_id is present" in out


def test_telegram_commands_sync_sets_expected_commands(tmp_path: Path, monkeypatch, capsys) -> None:
    env_file = write_env(tmp_path, token="token", state_dir=tmp_path, log_dir=tmp_path)
    fake = FakeTelegramApi()
    monkeypatch.setenv("MOMUK_ENV_FILE", str(env_file))
    monkeypatch.setattr(cli, "TelegramApiClient", lambda token: fake)

    code = cli.main(["telegram-commands", "sync"])

    out = capsys.readouterr().out
    assert code == 0
    assert "synced Telegram command menu" in out
    assert fake.synced_commands == [
        {"command": "chatid", "description": "현재 채팅방 ID 확인"},
        {"command": "set_momuk_room", "description": "현재 채팅방을 momukbot 채팅방으로 등록"},
    ]


def test_setup_telegram_guides_next_steps(tmp_path: Path, monkeypatch, capsys) -> None:
    env_file = write_env(tmp_path, token="", admin_user_ids="", state_dir=tmp_path, log_dir=tmp_path)
    monkeypatch.setenv("MOMUK_ENV_FILE", str(env_file))

    code = cli.main(["setup-telegram"])

    out = capsys.readouterr().out
    assert code == 1
    assert "Set TELEGRAM_BOT_TOKEN" in out
    assert "Set TELEGRAM_ADMIN_USER_IDS" in out
    assert "Send /set_momuk_room" in out


def test_recommend_accepts_natural_text(tmp_path: Path, monkeypatch, capsys) -> None:
    env_file = write_env(tmp_path, state_dir=tmp_path, log_dir=tmp_path)
    service = FakeService()
    monkeypatch.setenv("MOMUK_ENV_FILE", str(env_file))
    monkeypatch.setattr(cli, "build_service", lambda settings, persist: service)

    code = cli.main(["recommend", "서면 해장 국밥 추천", "--dry-run"])

    out = capsys.readouterr().out
    assert code == 0
    assert out.strip() == "natural dry run"
    assert service.handled == [("cli", "서면 해장 국밥 추천", True)]


def test_recommend_rejects_area_and_natural_text(tmp_path: Path, monkeypatch, capsys) -> None:
    env_file = write_env(tmp_path, state_dir=tmp_path, log_dir=tmp_path)
    monkeypatch.setenv("MOMUK_ENV_FILE", str(env_file))

    code = cli.main(["recommend", "서면 해장 국밥 추천", "--area", "서면"])

    err = capsys.readouterr().err
    assert code == 2
    assert "cannot use natural text together with --area, --topic, or --count" in err


class FakeTelegramApi:
    def __init__(self) -> None:
        self.synced_commands: list[dict[str, str]] = []

    def set_my_commands(self, commands: list[dict[str, str]]) -> None:
        self.synced_commands = commands

    def get_my_commands(self) -> list[dict[str, str]]:
        return self.synced_commands

    def get_me(self) -> dict[str, str]:
        return {"id": "1", "username": "momukbot"}


class FakeService:
    def __init__(self) -> None:
        self.handled: list[tuple[str, str, bool]] = []

    def handle_text(self, chat_id: str, text: str, dry_run: bool = False) -> str:
        self.handled.append((chat_id, text, dry_run))
        return "natural dry run"


def write_env(
    tmp_path: Path,
    token: str = "token",
    telegram_allowed_chat_ids: str = "",
    admin_user_ids: str = "42",
    state_dir: Path | None = None,
    log_dir: Path | None = None,
) -> Path:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                f"TELEGRAM_BOT_TOKEN={token}",
                f"TELEGRAM_ALLOWED_CHAT_IDS={telegram_allowed_chat_ids}",
                f"TELEGRAM_ADMIN_USER_IDS={admin_user_ids}",
                f"MOMUK_STATE_DIR={state_dir or tmp_path}",
                f"MOMUK_LOG_DIR={log_dir or tmp_path}",
                "CODEX_BIN=python3",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return env_file
