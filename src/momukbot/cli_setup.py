from __future__ import annotations

import getpass
import os
import shutil
from pathlib import Path

from momukbot.config import DEFAULT_ENV_FILE, ROOT


def init_env() -> int:
    example = ROOT / ".env.example"
    target = current_env_file()
    if target.exists():
        print(f"{target} already exists")
        return 0
    shutil.copyfile(example, target)
    print(f"created {target}")
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
        "MOMUK_LLM_REQUEST_PARSER_ENABLED",
        "MOMUK_STORE_RAW_RESPONSE",
        "NAVER_CLIENT_ID",
        "NAVER_CLIENT_SECRET",
        "NAVER_DAILY_SOFT_LIMIT",
        "KAKAO_REST_API_KEY",
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
