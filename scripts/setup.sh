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
    local mask_next=0
    for arg in "$@"; do
      if [[ "$mask_next" == "1" ]]; then
        printf ' %q' "[configured]"
        mask_next=0
      elif [[ "$arg" == --telegram-bot-token=* ]]; then
        printf ' %q' "--telegram-bot-token=[configured]"
      elif [[ "$arg" == "--telegram-bot-token" ]]; then
        printf ' %q' "$arg"
        mask_next=1
      elif [[ "$arg" == --naver-client-secret=* ]]; then
        printf ' %q' "--naver-client-secret=[configured]"
      elif [[ "$arg" == "--naver-client-secret" ]]; then
        printf ' %q' "$arg"
        mask_next=1
      else
        printf ' %q' "$arg"
      fi
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

  echo "==> Running momuk setup"
  if [[ "$DRY_RUN" == "1" ]]; then
    run "$VENV/bin/momuk" setup --dry-run "$@"
  else
    run "$VENV/bin/momuk" setup "$@"
  fi
}

main "$@"
