#!/usr/bin/env bash
set -euo pipefail

PLIST="$HOME/Library/LaunchAgents/com.hennei.momukbot.plist"
DRY_RUN="${MOMUK_LAUNCHD_DRY_RUN:-0}"

if [[ "$DRY_RUN" == "1" ]]; then
  echo "[dry-run] would remove $PLIST"
  exit 0
fi

launchctl unload "$PLIST" 2>/dev/null || true
rm -f "$PLIST"
echo "removed $PLIST"
