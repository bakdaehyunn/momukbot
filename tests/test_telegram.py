import json
from pathlib import Path
from momukbot.chat.telegram import TelegramBot, TelegramJob, chunk_text, mask_chat_id
from momukbot.config import Settings
from momukbot import telegram_ops


def test_chunk_text_splits_long_message() -> None:
    chunks = chunk_text("a" * 5000, 1000)

    assert len(chunks) == 5
    assert all(len(chunk) <= 1000 for chunk in chunks)


def test_mask_chat_id_keeps_only_short_suffix() -> None:
    assert mask_chat_id("7988775171") == "***5171"
    assert mask_chat_id("-100999") == "***0999"
    assert mask_chat_id("123") == "***"


def test_get_updates_http_timeout_exceeds_long_poll_timeout(monkeypatch) -> None:
    observed: dict[str, int] = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return b'{"ok": true, "result": []}'

    def fake_urlopen(req, timeout: int):
        observed["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(telegram_ops, "urlopen", fake_urlopen)

    payload = telegram_ops.TelegramApiClient("token").get_updates(timeout=30, limit=None)

    assert payload == {"ok": True, "result": []}
    assert observed["timeout"] > 30


def test_send_long_message_adds_part_numbers_only_when_split() -> None:
    bot = RecordingTelegramBot()

    bot.send_long_message("123", "서면 추천")
    assert bot.calls[0][1]["text"] == "서면 추천"

    bot.calls.clear()
    bot.send_long_message("123", ("서면 추천\n\n" + ("a" * 4000)))

    texts = [str(call[1]["text"]) for call in bot.calls]
    assert texts[0].startswith("(1/2)\n")
    assert texts[1].startswith("(2/2)\n")


class FakeService:
    def handle_text(self, chat_id: str, text: str) -> str:
        return "추천 결과"


class UnknownService:
    def handle_text(self, chat_id: str, text: str) -> None:
        return None


class RecordingTelegramBot(TelegramBot):
    def __init__(self, settings: Settings | None = None, service=None) -> None:
        self.recording_api = RecordingTelegramApi()
        super().__init__(
            settings or make_settings(),
            service or FakeService(),  # type: ignore[arg-type]
            api=self.recording_api,
        )

    @property
    def calls(self) -> list[tuple[str, dict[str, str | int], str]]:
        return self.recording_api.calls


class RecordingTelegramApi:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str | int], str]] = []

    def get_updates(self, offset: int | None = None, timeout: int = 30):
        params: dict[str, str | int] = {"timeout": timeout}
        if offset is not None:
            params["offset"] = offset
        self.calls.append(("getUpdates", params, "GET"))
        return {"ok": True, "result": []}

    def send_message(self, chat_id: str, text: str) -> None:
        self.calls.append(
            (
                "sendMessage",
                {"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"},
                "POST",
            )
        )

    def send_chat_action(self, chat_id: str, action: str) -> None:
        self.calls.append(("sendChatAction", {"chat_id": chat_id, "action": action}, "POST"))


def test_handle_update_enqueues_job() -> None:
    bot = RecordingTelegramBot(make_settings(allow_all_chats=True))

    bot.handle_update({"message": {"chat": {"id": 123}, "text": "서면 맛집 추천"}})

    job = bot.jobs.get_nowait()
    assert job == TelegramJob(chat_id="123", text="서면 맛집 추천")
    assert "123" in bot.busy_chats


def test_default_policy_rejects_regular_message_without_allowed_chat() -> None:
    bot = RecordingTelegramBot(make_settings())

    bot.handle_update({"message": {"chat": {"id": 123}, "text": "서면 맛집 추천"}})

    assert bot.jobs.empty()
    assert bot.calls == []


def test_env_allowed_chat_enqueues_job(tmp_path: Path) -> None:
    bot = RecordingTelegramBot(make_settings(tmp_path, allowed_chat_ids=("123",)))

    bot.handle_update({"message": {"chat": {"id": 123}, "text": "서면 맛집 추천"}})

    job = bot.jobs.get_nowait()
    assert job == TelegramJob(chat_id="123", text="서면 맛집 추천")


def test_registered_momuk_room_enqueues_job_outside_env_allowlist(tmp_path: Path) -> None:
    tmp_path.joinpath("telegram_rooms.json").write_text(
        json.dumps({"momuk_chat_id": "-100999"}),
        encoding="utf-8",
    )
    bot = RecordingTelegramBot(make_settings(tmp_path, allowed_chat_ids=("123",)))

    bot.handle_update({"message": {"chat": {"id": -100999}, "text": "서면 맛집 추천"}})

    job = bot.jobs.get_nowait()
    assert job == TelegramJob(chat_id="-100999", text="서면 맛집 추천")


def test_legacy_reminder_room_does_not_allow_regular_message(tmp_path: Path) -> None:
    tmp_path.joinpath("telegram_rooms.json").write_text(
        json.dumps({"reminder_chat_id": "-100999"}),
        encoding="utf-8",
    )
    bot = RecordingTelegramBot(make_settings(tmp_path, allowed_chat_ids=("123",)))

    bot.handle_update({"message": {"chat": {"id": -100999}, "text": "서면 맛집 추천"}})

    assert bot.jobs.empty()
    assert bot.calls == []


def test_copied_legacy_reminder_room_does_not_allow_regular_message(tmp_path: Path) -> None:
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
    bot = RecordingTelegramBot(make_settings(tmp_path, allowed_chat_ids=("123",)))

    bot.handle_update({"message": {"chat": {"id": -100999}, "text": "서면 맛집 추천"}})

    assert bot.jobs.empty()
    assert bot.calls == []


def test_process_job_sends_typing_before_message() -> None:
    bot = RecordingTelegramBot(make_settings(allow_all_chats=True))

    bot.process_job(TelegramJob(chat_id="123", text="서면 맛집 추천"))

    assert bot.calls[0][0] == "sendChatAction"
    assert bot.calls[0][1]["action"] == "typing"
    assert bot.calls[1][0] == "sendMessage"


def test_private_unknown_message_sends_usage_guidance() -> None:
    bot = RecordingTelegramBot(make_settings(allow_all_chats=True), UnknownService())

    bot.process_job(TelegramJob(chat_id="123", text="뭐 먹지", chat_type="private"))

    assert bot.calls[0][0] == "sendChatAction"
    assert bot.calls[1][0] == "sendMessage"
    assert "지역" in str(bot.calls[1][1]["text"])
    assert "서면에서 해장 국밥 추천해줘" in str(bot.calls[1][1]["text"])


def test_group_unknown_message_stays_quiet() -> None:
    bot = RecordingTelegramBot(make_settings(allow_all_chats=True), UnknownService())

    bot.process_job(TelegramJob(chat_id="-100999", text="뭐 먹지", chat_type="group"))

    assert bot.calls[0][0] == "sendChatAction"
    assert len(bot.calls) == 1


def test_enqueue_job_rejects_duplicate_chat() -> None:
    bot = RecordingTelegramBot(make_settings(allow_all_chats=True))

    bot.enqueue_job(TelegramJob(chat_id="123", text="서면 맛집 추천"))
    bot.enqueue_job(TelegramJob(chat_id="123", text="이태원 맛집 추천"))

    assert bot.jobs.qsize() == 1
    assert bot.calls[0][0] == "sendMessage"
    assert "처리 중" in str(bot.calls[0][1]["text"])


def test_admin_chatid_command_responds_outside_allowed_chats(tmp_path: Path) -> None:
    bot = RecordingTelegramBot(
        make_settings(tmp_path, allowed_chat_ids=("123",), admin_user_ids=("42",))
    )

    bot.handle_update(
        {
            "message": {
                "from": {"id": 42},
                "chat": {"id": -100999, "type": "supergroup", "title": "맛집추천방"},
                "text": "/chatid",
            }
        }
    )

    assert bot.jobs.empty()
    assert bot.calls[0][0] == "sendMessage"
    assert bot.calls[0][1]["chat_id"] == "-100999"
    assert "chat_id: -100999" in str(bot.calls[0][1]["text"])
    assert "맛집추천방" in str(bot.calls[0][1]["text"])


def test_admin_can_register_momuk_room_outside_allowed_chats(tmp_path: Path) -> None:
    bot = RecordingTelegramBot(
        make_settings(tmp_path, allowed_chat_ids=("123",), admin_user_ids=("42",))
    )

    bot.handle_update(
        {
            "message": {
                "from": {"id": 42},
                "chat": {"id": -100999, "type": "supergroup", "title": "맛집추천방"},
                "text": "/set_momuk_room",
            }
        }
    )

    rooms = tmp_path.joinpath("telegram_rooms.json").read_text(encoding="utf-8")
    assert '"momuk_chat_id": "-100999"' in rooms
    assert '"momuk_chat_title": "맛집추천방"' in rooms
    assert '"registered_by_user_id": "42"' in rooms
    assert bot.jobs.empty()
    assert bot.calls[0][0] == "sendMessage"
    assert "momukbot 채팅방으로 등록했습니다" in str(bot.calls[0][1]["text"])


def test_non_admin_cannot_register_momuk_room(tmp_path: Path) -> None:
    bot = RecordingTelegramBot(make_settings(tmp_path, admin_user_ids=("42",)))

    bot.handle_update(
        {
            "message": {
                "from": {"id": 7},
                "chat": {"id": -100999, "type": "supergroup", "title": "맛집추천방"},
                "text": "/set_momuk_room",
            }
        }
    )

    assert not tmp_path.joinpath("telegram_rooms.json").exists()
    assert bot.jobs.empty()
    assert bot.calls == []


def test_old_reminder_room_command_is_not_handled(tmp_path: Path) -> None:
    bot = RecordingTelegramBot(make_settings(tmp_path, admin_user_ids=("42",)))

    bot.handle_update(
        {
            "message": {
                "from": {"id": 42},
                "chat": {"id": -100999, "type": "supergroup", "title": "이전명령테스트방"},
                "text": "/set_reminder_room",
            }
        }
    )

    assert not tmp_path.joinpath("telegram_rooms.json").exists()
    assert bot.jobs.empty()


def test_admin_commands_do_not_work_without_admin_allowlist(tmp_path: Path) -> None:
    bot = RecordingTelegramBot(make_settings(tmp_path, admin_user_ids=()))

    bot.handle_update(
        {
            "message": {
                "from": {"id": 42},
                "chat": {"id": -100999, "type": "supergroup", "title": "맛집추천방"},
                "text": "/chatid",
            }
        }
    )

    assert bot.jobs.empty()
    assert bot.calls == []


def test_unallowed_regular_message_is_ignored(tmp_path: Path) -> None:
    bot = RecordingTelegramBot(
        make_settings(tmp_path, allowed_chat_ids=("123",), admin_user_ids=("42",))
    )

    bot.handle_update(
        {
            "message": {
                "from": {"id": 42},
                "chat": {"id": -100999, "type": "supergroup", "title": "맛집추천방"},
                "text": "서면 맛집 추천",
            }
        }
    )

    assert bot.jobs.empty()
    assert bot.calls == []


def make_settings(
    tmp: Path | None = None,
    allowed_chat_ids: tuple[str, ...] = (),
    admin_user_ids: tuple[str, ...] = (),
    allow_all_chats: bool = False,
) -> Settings:
    tmp = tmp or Path("/tmp/momukbot-test")
    return Settings(
        telegram_bot_token="token",
        telegram_allowed_chat_ids=allowed_chat_ids,
        telegram_admin_user_ids=admin_user_ids,
        telegram_allow_all_chats=allow_all_chats,
        naver_client_id="",
        naver_client_secret="",
        naver_daily_soft_limit=10,
        blog_allowed_domains=("blog.naver.com",),
        agent_provider="codex_cli",
        codex_bin="codex",
        codex_workdir=tmp,
        codex_sandbox="read-only",
        codex_timeout_sec=60,
        default_count=30,
        state_dir=tmp,
        log_dir=tmp,
    )
