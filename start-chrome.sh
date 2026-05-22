#!/usr/bin/env bash
# start-chrome.sh — Start Chrome + ChatGPT Bridge (full stack).
#
# Launches Chrome with remote-debugging then starts the bridge daemon.
# Chrome is backgrounded; bridge daemon is backgrounded via chatgpt-bridge CLI.
#
# Usage:
#   ./start-chrome.sh   [--headless]  [--no-bridge]
set -euo pipefail

CHROME_DIR="${CHROME_DIR:-${HOME}/.chrome-chatgpt-debug}"
CDP_PORT="${CDP_PORT:-9222}"
HEADLESS="${HEADLESS:-}"
START_BRIDGE=1

# ── Clean __pycache__ before anything else (Chrome rejects dirs starting with _)
rm -rf "${HOME}/chatgpt-extension/__pycache__" 2>/dev/null || true

for arg in "$@"; do
    case "$arg" in
        --headless) HEADLESS="--headless=new" ;;
        --no-bridge) START_BRIDGE=0 ;;
    esac
done

# ── Kill any prior Chrome on the same port
lsof -ti :"$CDP_PORT" 2>/dev/null | xargs kill -TERM 2>/dev/null || true
sleep 1

# ── Launch Chrome
echo "Starting Chrome (CDP port $CDP_PORT, profile $CHROME_DIR)…"
google-chrome-stable \
  --remote-debugging-port="$CDP_PORT" \
  --user-data-dir="$CHROME_DIR" \
  --new-window \
  $HEADLESS \
  "https://chatgpt.com" \
  2>&1 &
disown $!

echo "  CDP:     ws://127.0.0.1:$CDP_PORT"
echo "  URL:     https://chatgpt.com"
echo "  Profile: $CHROME_DIR"
echo "  Headless: ${HEADLESS:-off}"
echo ""

# ── Give Chrome a moment to initialise, then start bridge
if [[ $START_BRIDGE -eq 1 ]]; then
    sleep 2
    if [[ -x "${HOME}/chatgpt-extension/chatgpt-bridge" ]]; then
        echo "Starting ChatGPT Bridge daemon…"
        "${HOME}/chatgpt-extension/chatgpt-bridge" start
        echo ""
        echo "NOTE: Extension persists in the Chrome profile once loaded."
        echo "      If not loaded: chrome://extensions → Load unpacked → ~/chatgpt-extension/"
        echo "NOTE: Verify with:  chatgpt-bridge status"
    else
        echo "WARNING: chatgpt-bridge CLI not found at ${HOME}/chatgpt-extension/chatgpt-bridge"
        echo "         Install it to get lifecycle management of the bridge daemon."
    fi
fi
