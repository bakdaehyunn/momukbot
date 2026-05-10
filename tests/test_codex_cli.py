from pathlib import Path

from momukbot.agent.codex_cli import CodexCliAgent
from momukbot.config import Settings


def test_codex_command_uses_user_configured_binary(tmp_path: Path) -> None:
    settings = Settings(
        telegram_bot_token="",
        telegram_allowed_chat_ids=(),
        naver_client_id="",
        naver_client_secret="",
        naver_daily_soft_limit=10,
        blog_allowed_domains=("blog.naver.com",),
        agent_provider="codex_cli",
        codex_bin="my-codex",
        codex_workdir=tmp_path,
        codex_sandbox="read-only",
        codex_timeout_sec=60,
        default_count=30,
        state_dir=tmp_path,
        log_dir=tmp_path,
    )

    cmd = CodexCliAgent(settings).command(tmp_path / "out.txt", "prompt")

    assert cmd[0] == "my-codex"
    assert ("/home/" + "dh") not in " ".join(cmd)
    assert ("codex" + "-resolve") not in " ".join(cmd)
