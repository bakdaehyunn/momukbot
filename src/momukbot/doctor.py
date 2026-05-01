from __future__ import annotations

import shutil
import subprocess

from momukbot.config import Settings
from momukbot.search.naver import NaverSearchProvider


def run_doctor(settings: Settings) -> tuple[int, str]:
    lines: list[str] = []
    failures = 0

    if settings.telegram_bot_token:
        lines.append("[OK] TELEGRAM_BOT_TOKEN is set")
    else:
        lines.append("[WARN] TELEGRAM_BOT_TOKEN is not set")

    if settings.telegram_allowed_chat_ids:
        lines.append("[OK] TELEGRAM_ALLOWED_CHAT_IDS is set")
    else:
        lines.append("[WARN] TELEGRAM_ALLOWED_CHAT_IDS is empty; every chat can use the bot")

    if settings.naver_client_id and settings.naver_client_secret:
        lines.append("[OK] Naver API credentials are set")
    else:
        lines.append("[WARN] NAVER_CLIENT_ID/NAVER_CLIENT_SECRET are not set")

    provider = NaverSearchProvider(settings)
    quota = provider.quota.status()
    lines.append(
        f"[OK] Naver quota status: date={quota.date} count={quota.count} "
        f"soft_limit={quota.soft_limit} remaining={quota.remaining}"
    )

    codex_path = shutil.which(settings.codex_bin)
    if codex_path:
        lines.append(f"[OK] Codex CLI found: {codex_path}")
        try:
            proc = subprocess.run(
                [settings.codex_bin, "--version"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
            )
            version = (proc.stdout or proc.stderr).strip().splitlines()
            if proc.returncode == 0 and version:
                lines.append(f"[OK] Codex CLI version: {version[0]}")
            else:
                lines.append("[WARN] Codex CLI exists but version check did not return cleanly")
        except Exception as exc:
            lines.append(f"[WARN] Codex CLI version check failed: {exc}")
    else:
        failures += 1
        lines.append(f"[FAIL] Codex CLI not found: {settings.codex_bin}")

    settings.state_dir.mkdir(parents=True, exist_ok=True)
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    lines.append(f"[OK] state_dir={settings.state_dir}")
    lines.append(f"[OK] log_dir={settings.log_dir}")

    return (1 if failures else 0), "\n".join(lines)
