from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from momukbot.config import Settings


class CodexCliAgent:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def command(self, output_path: Path, prompt: str) -> list[str]:
        return [
            self.settings.codex_bin,
            "exec",
            "--sandbox",
            self.settings.codex_sandbox,
            "--skip-git-repo-check",
            "--output-last-message",
            str(output_path),
            prompt,
        ]

    def generate(self, prompt: str) -> str:
        with tempfile.NamedTemporaryFile(delete=False) as fp:
            output_path = Path(fp.name)
        try:
            proc = subprocess.run(
                self.command(output_path, prompt),
                cwd=self.settings.codex_workdir,
                stdin=subprocess.DEVNULL,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.settings.codex_timeout_sec,
            )
            last = output_path.read_text(encoding="utf-8", errors="ignore").strip()
            if proc.returncode == 0 and last:
                return last
            output = "\n".join(part for part in [proc.stdout.strip(), proc.stderr.strip()] if part)
            if proc.returncode == 0:
                return last or output
            raise RuntimeError(f"codex exited with {proc.returncode}: {output[:1200]}")
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"codex timed out after {self.settings.codex_timeout_sec}s") from exc
        finally:
            output_path.unlink(missing_ok=True)
