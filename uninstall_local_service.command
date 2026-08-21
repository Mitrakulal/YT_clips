#!/bin/bash
set -euo pipefail

LABEL="com.mitrakulal.ytclipsstudio"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
rm -f "$PLIST"
echo "Local Studio automatic startup has been removed."
