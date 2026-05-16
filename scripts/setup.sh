#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV="$ROOT/.venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"
DRY_RUN="${MOMUK_SETUP_DRY_RUN:-0}"

run() {
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '[dry-run]'
    for arg in "$@"; do
      printf ' %q' "$arg"
    done
    printf '\n'
  else
    "$@"
  fi
}

main() {
  cd "$ROOT"

  echo "==> Creating virtual environment"
  if [[ ! -d "$VENV" ]]; then
    run "$PYTHON_BIN" -m venv "$VENV"
  else
    echo "existing .venv found"
  fi

  echo "==> Installing momukbot"
  run "$VENV/bin/python" -m pip install -e ".[dev]"

  echo "==> Creating local env if needed"
  run "$VENV/bin/momuk" init

  echo "==> Checking Telegram setup"
  if [[ "$DRY_RUN" == "1" ]]; then
    run "$VENV/bin/momuk" setup-telegram
  else
    "$VENV/bin/momuk" setup-telegram || true
  fi

  echo "==> Running doctor"
  if [[ "$DRY_RUN" == "1" ]]; then
    run "$VENV/bin/momuk" doctor
  else
    "$VENV/bin/momuk" doctor || true
  fi

  echo "setup checks complete"
  echo "Use 'momuk discover-chat' and 'momuk send-test --chat-id <id>' to verify Telegram delivery."
}

main "$@"
