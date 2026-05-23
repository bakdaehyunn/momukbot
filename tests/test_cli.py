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
        "MOMUK_ALLOW_ALL_CHATS",
        "MOMUK_STORE_RAW_RESPONSE",
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


def test_rooms_fails_when_legacy_room_was_copied_to_momuk_room(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    env_file = write_env(tmp_path, telegram_allowed_chat_ids="123", state_dir=tmp_path, log_dir=tmp_path)
    tmp_path.joinpath("telegram_rooms.json").write_text(
        json.dumps(
            {
                "reminder_chat_id": "-100999",
                "reminder_chat_title": "생활알림방",
                "momuk_chat_id": "-100999",
                "momuk_chat_title": "생활알림방",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MOMUK_ENV_FILE", str(env_file))

    code = cli.main(["rooms"])

    out = capsys.readouterr().out
    assert code == 1
    assert "legacy reminder_chat_id matches momuk_chat_id" in out
    assert "생활알림방" in out


def test_telegram_commands_sync_sets_expected_commands(tmp_path: Path, monkeypatch, capsys) -> None:
    env_file = write_env(tmp_path, token="token", state_dir=tmp_path, log_dir=tmp_path)
    fake = FakeTelegramApi()
    monkeypatch.setenv("MOMUK_ENV_FILE", str(env_file))
    monkeypatch.setattr(cli, "TelegramApiClient", lambda token: fake)

    code = cli.main(["telegram-commands", "sync"])

    out = capsys.readouterr().out
    assert code == 0
    assert "synced Telegram command menu: default" in out
    assert fake.synced_commands_by_scope["default"] == [
        {"command": "chatid", "description": "현재 채팅방 ID 확인"},
    ]
    assert "-100999" not in fake.synced_commands_by_scope


def test_telegram_commands_sync_sets_registered_chat_scope(tmp_path: Path, monkeypatch, capsys) -> None:
    env_file = write_env(tmp_path, token="token", state_dir=tmp_path, log_dir=tmp_path)
    tmp_path.joinpath("telegram_rooms.json").write_text(
        json.dumps({"momuk_chat_id": "-100999"}),
        encoding="utf-8",
    )
    fake = FakeTelegramApi()
    monkeypatch.setenv("MOMUK_ENV_FILE", str(env_file))
    monkeypatch.setattr(cli, "TelegramApiClient", lambda token: fake)

    code = cli.main(["telegram-commands", "sync"])

    out = capsys.readouterr().out
    assert code == 0
    assert "default and registered chat" in out
    assert fake.synced_commands_by_scope["default"] == [
        {"command": "chatid", "description": "현재 채팅방 ID 확인"},
    ]
    assert fake.synced_commands_by_scope["-100999"] == [
        {"command": "chatid", "description": "현재 채팅방 ID 확인"},
        {"command": "set_chat_room", "description": "현재 채팅방을 이 봇의 사용 방으로 등록"},
    ]


def test_discover_chat_prints_latest_chat(tmp_path: Path, monkeypatch, capsys) -> None:
    env_file = write_env(tmp_path, token="token", state_dir=tmp_path, log_dir=tmp_path)
    fake = FakeTelegramApi()
    fake.updates = {
        "ok": True,
        "result": [
            {"message": {"chat": {"id": 123, "first_name": "Dh", "type": "private"}}},
            {"message": {"chat": {"id": -100999, "title": "뭐먹봇방", "type": "group"}}},
        ],
    }
    monkeypatch.setenv("MOMUK_ENV_FILE", str(env_file))
    monkeypatch.setattr(cli, "TelegramApiClient", lambda token: fake)

    code = cli.main(["discover-chat"])

    out = capsys.readouterr().out
    assert code == 0
    assert "chat_id: -100999" in out
    assert "title: 뭐먹봇방" in out
    assert "type: group" in out


def test_discover_chat_plain_prints_latest_chat_id(tmp_path: Path, monkeypatch, capsys) -> None:
    env_file = write_env(tmp_path, token="token", state_dir=tmp_path, log_dir=tmp_path)
    fake = FakeTelegramApi()
    fake.updates = {
        "ok": True,
        "result": [{"message": {"chat": {"id": -100999, "title": "뭐먹봇방"}}}],
    }
    monkeypatch.setenv("MOMUK_ENV_FILE", str(env_file))
    monkeypatch.setattr(cli, "TelegramApiClient", lambda token: fake)

    code = cli.main(["discover-chat", "--plain"])

    assert code == 0
    assert capsys.readouterr().out.strip() == "-100999"


def test_send_test_requires_explicit_target(tmp_path: Path, monkeypatch, capsys) -> None:
    env_file = write_env(tmp_path, token="token", state_dir=tmp_path, log_dir=tmp_path)
    monkeypatch.setenv("MOMUK_ENV_FILE", str(env_file))

    code = cli.main(["send-test"])

    err = capsys.readouterr().err
    assert code == 2
    assert "choose exactly one target" in err


def test_send_test_dry_run_resolves_target_without_sending(tmp_path: Path, monkeypatch, capsys) -> None:
    env_file = write_env(tmp_path, token="token", state_dir=tmp_path, log_dir=tmp_path)
    fake = FakeTelegramApi()
    monkeypatch.setenv("MOMUK_ENV_FILE", str(env_file))
    monkeypatch.setattr(cli, "TelegramApiClient", lambda token: fake)

    code = cli.main(["send-test", "--chat-id", "-100999", "--dry-run"])

    out = capsys.readouterr().out
    assert code == 0
    assert "target_title=뭐먹봇방" in out
    assert "dry-run: message was not sent" in out
    assert fake.sent_messages == []


def test_send_test_sends_to_explicit_chat_id(tmp_path: Path, monkeypatch, capsys) -> None:
    env_file = write_env(tmp_path, token="token", state_dir=tmp_path, log_dir=tmp_path)
    fake = FakeTelegramApi()
    monkeypatch.setenv("MOMUK_ENV_FILE", str(env_file))
    monkeypatch.setattr(cli, "TelegramApiClient", lambda token: fake)

    code = cli.main(["send-test", "--chat-id", "-100999"])

    out = capsys.readouterr().out
    assert code == 0
    assert "sent test message" in out
    assert fake.sent_messages == [("-100999", fake.sent_messages[0][1])]
    assert "뭐먹봇 Telegram 테스트" in fake.sent_messages[0][1]


def test_setup_telegram_guides_next_steps(tmp_path: Path, monkeypatch, capsys) -> None:
    env_file = write_env(tmp_path, token="", admin_user_ids="", state_dir=tmp_path, log_dir=tmp_path)
    monkeypatch.setenv("MOMUK_ENV_FILE", str(env_file))

    code = cli.main(["setup-telegram"])

    out = capsys.readouterr().out
    assert code == 1
    assert "Set TELEGRAM_BOT_TOKEN" in out
    assert "Set TELEGRAM_ADMIN_USER_IDS" in out
    assert "Send /set_chat_room" in out


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


def test_history_clear_requires_confirmation(tmp_path: Path, monkeypatch, capsys) -> None:
    env_file = write_env(tmp_path, state_dir=tmp_path, log_dir=tmp_path)
    monkeypatch.setenv("MOMUK_ENV_FILE", str(env_file))

    code = cli.main(["history", "clear"])

    err = capsys.readouterr().err
    assert code == 2
    assert "--yes" in err


def test_history_clear_deletes_recommendations(tmp_path: Path, monkeypatch, capsys) -> None:
    env_file = write_env(tmp_path, state_dir=tmp_path, log_dir=tmp_path)
    store = cli.RecommendationStore(tmp_path)
    store.add_result(
        chat_id="123",
        request_text="서면 국밥 추천",
        area="서면",
        topic="국밥",
        search_keyword="서면 국밥",
        raw_response="",
        items=[],
    )
    monkeypatch.setenv("MOMUK_ENV_FILE", str(env_file))

    code = cli.main(["history", "clear", "--yes"])

    out = capsys.readouterr().out
    assert code == 0
    assert "deleted 1 recommendation history rows" in out


class FakeTelegramApi:
    def __init__(self) -> None:
        self.synced_commands_by_scope: dict[str, list[dict[str, str]]] = {}
        self.updates: dict[str, object] = {"ok": True, "result": []}
        self.sent_messages: list[tuple[str, str]] = []

    def set_my_commands(self, commands: list[dict[str, str]], scope: dict[str, str] | None = None) -> None:
        self.synced_commands_by_scope[scope_key(scope)] = commands

    def get_my_commands(self, scope: dict[str, str] | None = None) -> list[dict[str, str]]:
        return self.synced_commands_by_scope.get(scope_key(scope), [])

    def get_me(self) -> dict[str, str]:
        return {"id": "1", "username": "momukbot"}

    def get_updates(self, limit: int = 100) -> dict[str, object]:
        return self.updates

    def get_chat(self, chat_id: str) -> dict[str, object]:
        return {
            "ok": True,
            "result": {"id": chat_id, "title": "뭐먹봇방", "type": "group"},
        }

    def send_message(self, chat_id: str, text: str) -> None:
        self.sent_messages.append((chat_id, text))


def scope_key(scope: dict[str, str] | None) -> str:
    if not scope:
        return "default"
    return str(scope.get("chat_id") or "default")


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
                "MOMUK_ALLOW_ALL_CHATS=false",
                "MOMUK_STORE_RAW_RESPONSE=false",
                f"MOMUK_STATE_DIR={state_dir or tmp_path}",
                f"MOMUK_LOG_DIR={log_dir or tmp_path}",
                "CODEX_BIN=python3",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return env_file
