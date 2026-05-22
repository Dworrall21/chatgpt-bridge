#!/usr/bin/env bash
# chatgpt-bridge-install.sh — Install ChatGPT Bridge deps & register Hermes profile
#
# Usage (local):
#   bash install.sh
# Usage (curl | bash from GitHub):
#   curl -fsSL https://raw.githubusercontent.com/dworrall21/chatgpt-extension/main/install.sh | bash

set -euo pipefail

BRIDGE_HOME="$(cd "$(dirname "$0")" && pwd)"
REQUIREMENTS="${BRIDGE_HOME}/requirements.txt"
HERMES_CONFIG="${HOME}/.hermes/config.yaml"
PROFILE_DIR="${HOME}/.hermes/profiles/chatgpt-bridge"

err() { echo "Error: $*" >&2; exit 1; }
info() { echo "$*"; }

# ── Prerequisites ────────────────────────────────────────────────────────────

command -v python3 >/dev/null 2>&1 || err "Python 3 is required but not found in PATH"

CHROME_CMD=""
if command -v google-chrome-stable  >/dev/null 2>&1; then
    CHROME_CMD="google-chrome-stable"
elif command -v chromium-browser     >/dev/null 2>&1; then
    CHROME_CMD="chromium-browser"
elif command -v chromium             >/dev/null 2>&1; then
    CHROME_CMD="chromium"
fi

if [[ -n "$CHROME_CMD" ]]; then
    info "Chrome: $CHROME_CMD"
else
    info "Warning: Chrome/Chromium not found — install it to use the extension."
    info "  Ubuntu/Debian:  sudo apt install google-chrome-stable"
    info "  Fedora/RHEL:    sudo dnf install chromium"
fi

# ── Python dependencies ──────────────────────────────────────────────────────

info "Installing Python dependencies..."

pip_install() {
    python3 -m pip install --quiet --user "$@" && return 0
    python3 -m pip install --quiet         "$@" && return 0
    return 1
}

if [[ -f "$REQUIREMENTS" ]]; then
    info "  From requirements.txt (pinned versions)..."
    pip_install -r "$REQUIREMENTS"
else
    info "  No requirements.txt found, installing defaults..."
    pip_install aiohttp websockets
fi

# Quick verification
python3 -c "import aiohttp, websockets" 2>/dev/null && \
    info "  Python deps OK: $(python3 -c 'import aiohttp,websockets;print(f"aiohttp={aiohttp.__version__}, websockets={websockets.__version__}")')" \
    || err "Dependency check failed — run: pip install aiohttp websockets"

# ── Hermes chatgpt-bridge profile ────────────────────────────────────────────

info "Setting up Hermes chatgpt-bridge profile..."

mkdir -p "${PROFILE_DIR}"
mkdir -p "${PROFILE_DIR}/audio_cache"
mkdir -p "${PROFILE_DIR}/image_cache"
mkdir -p "${PROFILE_DIR}/logs"
mkdir -p "${PROFILE_DIR}/memories"
mkdir -p "${PROFILE_DIR}/skills"
mkdir -p "${PROFILE_DIR}/sandboxes"
mkdir -p "${PROFILE_DIR}/sessions"
mkdir -p "${PROFILE_DIR}/workspace"

PROFILE_CONFIG="${PROFILE_DIR}/config.yaml"

if [[ ! -f "$PROFILE_CONFIG" ]]; then
    cat > "$PROFILE_CONFIG" <<'PROFILECFG'
model:
  base_url: ''
  context_length: 65536
  default: chatgpt-5.5
  provider: chatgpt-bridge
display:
  streaming: false
providers:
  chatgpt-bridge:
    name: ChatGPT Bridge
    base_url: http://127.0.0.1:11557/v1
    api_key: none
    api_mode: chat_completions
    default_model: chatgpt-5.5
    models:
      chatgpt-5.5:
        context_length: 65536
      chatgpt-5.5-thinking:
        context_length: 65536
PROFILECFG
    info "  Profile config created at $PROFILE_CONFIG"
else
    info "  Profile config already present at $PROFILE_CONFIG (skipping)"
fi

# ── Hermes root config — add/replace provider block ───────────────────────────

info "Registering chatgpt-bridge provider in Hermes config..."

if [[ ! -f "$HERMES_CONFIG" ]]; then
    err "Hermes config not found at $HERMES_CONFIG. Is Hermes installed?"
fi

# Check whether a 'chatgpt-bridge:' provider entry already exists under providers:.
if grep -qE '^\s{2}chatgpt-bridge:' "$HERMES_CONFIG"; then
    info "  Provider already registered in Hermes config (skipping)"
elif grep -qE '^providers:' "$HERMES_CONFIG"; then
    python3 - "$HERMES_CONFIG" <<'PYBLOCK'
import sys
path = sys.argv[1]

with open(path) as f:
    lines = f.readlines()

changed = False
for i, line in enumerate(lines):
    if line.rstrip() == 'providers:':
        indent = '  '
        block = (
            f'{indent}chatgpt-bridge:\n'
            f'{indent}  name: ChatGPT Bridge\n'
            f'{indent}  base_url: http://127.0.0.1:11557/v1\n'
            f'{indent}  api_key: none\n'
            f'{indent}  api_mode: chat_completions\n'
            f'{indent}  default_model: chatgpt-5.5\n'
            f'{indent}  models:\n'
            f'{indent}    chatgpt-5.5:\n'
            f'{indent}      context_length: 65536\n'
            f'{indent}    chatgpt-5.5-thinking:\n'
            f'{indent}      context_length: 65536\n'
        )
        lines.insert(i + 1, block)
        changed = True
        break

if changed:
    with open(path, 'w') as f:
        f.writelines(lines)
    print("  Provider block added to Hermes config.")
else:
    print("  Warning: 'providers:' section not found — config not modified.")
PYBLOCK
else
    info "  No 'providers:' section found — config not modified (edit manually)"
fi

# ── Permissions ──────────────────────────────────────────────────────────────

chmod +x "${BRIDGE_HOME}/bridge-host.py"  2>/dev/null || true
chmod +x "${BRIDGE_HOME}/chatgpt-cdp.py"   2>/dev/null || true
chmod +x "${BRIDGE_HOME}/chatgpt-chat"     2>/dev/null || true
[[ -x "${BRIDGE_HOME}/chatgpt-bridge" ]] && chmod +x "${BRIDGE_HOME}/chatgpt-bridge" 2>/dev/null || true
[[ -x "${BRIDGE_HOME}/start-chrome.sh" ]] && chmod +x "${BRIDGE_HOME}/start-chrome.sh" 2>/dev/null || true

# ── Summary ──────────────────────────────────────────────────────────────────

info ""
info "══════════════════════════════════════════════════════════"
info "  ChatGPT Bridge installed successfully."
info "══════════════════════════════════════════════════════════"
info ""
info "Next steps:"
info "  1. Start Chrome:          bash ${BRIDGE_HOME}/start-chrome.sh"
info "  2. Load unpacked ext:     chrome://extensions → Load unpacked → ${BRIDGE_HOME}/"
info "  3. Start bridge host:     python3 ${BRIDGE_HOME}/bridge-host.py"
info "  4. Test:                  ${BRIDGE_HOME}/chatgpt-chat 'Hello'"
info "  5. Hermes profile:        hermes -p chatgpt-bridge chat 'Hello'"
info ""
if [[ -z "$CHROME_CMD" ]]; then
    info "NOTE: Chrome / Chromium not detected. Install it before running the bridge."
fi
