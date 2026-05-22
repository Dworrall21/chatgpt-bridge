#!/usr/bin/env bash
# Start Chrome with remote debugging for ChatGPT extension development.
# Uses a persistent user-data-dir so Google login survives restarts.
#
# CDP watchdog is handled by chatgpt-bridge-host.py, which monitors
# http://127.0.0.1:9222/json/version and calls this script to restart
# Chrome when CDP is down for 30s+.
set -euo pipefail

CHROME_DIR="${CHROME_DIR:-$HOME/.chrome-chatgpt-debug}"
CDP_PORT="${CDP_PORT:-9222}"

mkdir -p "$CHROME_DIR"

# Kill any previous instance on the same port
lsof -ti :$CDP_PORT 2>/dev/null | xargs kill 2>/dev/null || true
sleep 1

google-chrome-stable \
  --remote-debugging-port="$CDP_PORT" \
  --user-data-dir="$CHROME_DIR" \
  --new-window \
  "https://chatgpt.com" \
  2>&1 &

echo "Chrome debug instance started:"
echo "  CDP:   ws://127.0.0.1:$CDP_PORT"
echo "  URL:   https://chatgpt.com"
echo "  Profile: $CHROME_DIR"
echo ""
echo "NOTE: Extension persists in the profile once loaded."
echo "      If not loaded: chrome://extensions → Load unpacked → ~/chatgpt-extension/"
echo "NOTE: CDP watchdog runs inside chatgpt-bridge-host.py (monitors every 30s)."
