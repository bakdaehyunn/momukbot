from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = ROOT / ".env"


def load_env(path: Path | None = None) -> None:
    env_path = path or Path(os.environ.get("MOMUK_ENV_FILE", DEFAULT_ENV_FILE))
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def env_str(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def env_int(name: str, default: int) -> int:
    raw = env_str(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def env_bool(name: str, default: bool = False) -> bool:
    raw = env_str(name)
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "y", "on"}


def resolve_path(raw: str, default: str) -> Path:
    value = raw or default
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    return path


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_allowed_chat_ids: tuple[str, ...]
    telegram_admin_user_ids: tuple[str, ...]
    naver_client_id: str
    naver_client_secret: str
    naver_daily_soft_limit: int
    blog_allowed_domains: tuple[str, ...]
    agent_provider: str
    codex_bin: str
    codex_workdir: Path
    codex_sandbox: str
    codex_timeout_sec: int
    default_count: int
    state_dir: Path
    log_dir: Path
    telegram_allow_all_chats: bool = False


def get_settings() -> Settings:
    allowed = tuple(
        item.strip()
        for item in env_str("TELEGRAM_ALLOWED_CHAT_IDS").split(",")
        if item.strip()
    )
    admins = tuple(
        item.strip()
        for item in env_str("TELEGRAM_ADMIN_USER_IDS").split(",")
        if item.strip()
    )
    domains = tuple(
        item.strip().lower()
        for item in env_str("BLOG_ALLOWED_DOMAINS", "blog.naver.com").split(",")
        if item.strip() and item.strip().lower() != "tistory.com" and not item.strip().lower().endswith(".tistory.com")
    )
    return Settings(
        telegram_bot_token=env_str("TELEGRAM_BOT_TOKEN"),
        telegram_allowed_chat_ids=allowed,
        telegram_admin_user_ids=admins,
        telegram_allow_all_chats=env_bool("MOMUK_ALLOW_ALL_CHATS", False),
        naver_client_id=env_str("NAVER_CLIENT_ID"),
        naver_client_secret=env_str("NAVER_CLIENT_SECRET"),
        naver_daily_soft_limit=env_int("NAVER_DAILY_SOFT_LIMIT", 24000),
        blog_allowed_domains=domains,
        agent_provider=env_str("AGENT_PROVIDER", "codex_cli"),
        codex_bin=env_str("CODEX_BIN", "codex"),
        codex_workdir=resolve_path(env_str("CODEX_WORKDIR"), "."),
        codex_sandbox=env_str("CODEX_SANDBOX", "read-only"),
        codex_timeout_sec=env_int("CODEX_TIMEOUT_SEC", 600),
        default_count=max(1, min(30, env_int("MOMUK_DEFAULT_COUNT", 30))),
        state_dir=resolve_path(env_str("MOMUK_STATE_DIR"), ".local/state"),
        log_dir=resolve_path(env_str("MOMUK_LOG_DIR"), ".local/logs"),
    )
