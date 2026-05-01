#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

fail=0

if git ls-files --error-unmatch .env >/dev/null 2>&1; then
  echo "[FAIL] .env is tracked"
  fail=1
fi

tracked_bad_files="$(git ls-files '*.sqlite3' 'state/*' 'logs/*' '.local/*' 2>/dev/null || true)"
if [[ -n "$tracked_bad_files" ]]; then
  echo "[FAIL] state/log/sqlite files are tracked:"
  echo "$tracked_bad_files"
  fail=1
fi

if rg -n '/home/dh|telegram-bridge|codex-resolve' . \
  --glob '!scripts/preflight_public.sh' \
  --glob '!docs/architecture.md' >/tmp/momukbot-preflight-paths.txt; then
  echo "[FAIL] private paths or bridge coupling found:"
  cat /tmp/momukbot-preflight-paths.txt
  fail=1
fi

if rg -n 'TELEGRAM_BOT_TOKEN=\\S+|NAVER_CLIENT_SECRET=\\S+|NAVER_CLIENT_ID=\\S+' . \
  --glob '!.env.example' \
  --glob '!scripts/preflight_public.sh' >/tmp/momukbot-preflight-secrets.txt; then
  echo "[FAIL] possible committed secrets found:"
  cat /tmp/momukbot-preflight-secrets.txt
  fail=1
fi

if [[ "$fail" -ne 0 ]]; then
  exit "$fail"
fi

echo "[OK] public preflight passed"
