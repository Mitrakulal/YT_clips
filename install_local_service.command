#!/bin/bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
LABEL="com.mitrakulal.ytclipsstudio"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$APP_DIR/studio_data/logs"

mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR"

if [ ! -x "$APP_DIR/.venv/bin/python" ]; then
  echo "The local environment is missing. Run start_studio.command once first."
  exit 1
fi

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$APP_DIR/.venv/bin/python</string>
    <string>$APP_DIR/local_studio.py</string>
    <string>--no-browser</string>
  </array>
  <key>WorkingDirectory</key><string>$APP_DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$LOG_DIR/local-studio.out.log</string>
  <key>StandardErrorPath</key><string>$LOG_DIR/local-studio.error.log</string>
</dict>
</plist>
PLIST

launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl kickstart -k "gui/$(id -u)/$LABEL"

echo "Local Studio now starts automatically after login."
echo "Open http://127.0.0.1:8765 in Safari or Chrome."
