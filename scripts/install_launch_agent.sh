#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/com.hennei.momukbot.plist"
DRY_RUN="${MOMUK_LAUNCHD_DRY_RUN:-0}"

plist_body() {
  cat <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.hennei.momukbot</string>
  <key>ProgramArguments</key>
  <array>
    <string>$ROOT/.venv/bin/momuk</string>
    <string>telegram</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$ROOT</string>
  <key>KeepAlive</key>
  <true/>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$ROOT/.local/logs/telegram.stdout.log</string>
  <key>StandardErrorPath</key>
  <string>$ROOT/.local/logs/telegram.stderr.log</string>
</dict>
</plist>
PLIST
}

if [[ "$DRY_RUN" == "1" ]]; then
  echo "[dry-run] would install $PLIST"
  plist_body
  exit 0
fi

mkdir -p "$HOME/Library/LaunchAgents" "$ROOT/.local/logs"
plist_body > "$PLIST"

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "installed $PLIST"
