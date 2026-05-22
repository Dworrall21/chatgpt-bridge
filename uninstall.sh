#!/usr/bin/env bash
# chatgpt-bridge-uninstall.sh — Remove ChatGPT Bridge Hermes integration
#
# Removes the chatgpt-bridge provider from ~/.hermes/config.yaml.
# Keeps Python packages and the Hermes profile.
#
# bash uninstall.sh

set -euo pipefail

HERMES_CONFIG="${HOME}/.hermes/config.yaml"

err() { echo "Error: $*" >&2; exit 1; }
info() { echo "$*"; }

[[ -f "$HERMES_CONFIG" ]] || {
    info "No Hermes config at $HERMES_CONFIG — nothing to do."
    exit 0
}

info "Unregistering chatgpt-bridge provider..."

python3 - "$HERMES_CONFIG" <<'PYBLOCK'

import sys, re
path = sys.argv[1]

with open(path) as f:
    lines = f.readlines()

cleaned      = []
in_cbg_block = False
key_depth    = None

for line in lines:
    m = re.match(r'^(\s*)chatgpt-bridge:', line)
    if not in_cbg_block and m:
        in_cbg_block = True
        key_depth    = len(m.group(1))
        continue            # skip the chatgpt-bridge: key line itself

    if in_cbg_block:
        stripped = line.strip()
        if stripped == '':
            continue        # blank line inside block → drop
        indent = len(line) - len(stripped)
        # Stop at the next sibling key at same/lesser indent
        if indent <= key_depth and not line.startswith('    '):
            in_cbg_block = False
            cleaned.append(line)   # next section — keep it
        else:
            continue        # still inside block → drop
    else:
        cleaned.append(line)

removed = len(cleaned) < len(lines)

if removed:
    with open(path, 'w') as f:
        f.writelines(cleaned)
    sys.exit(0)     # success — removed
else:
    sys.exit(1)     # nothing found
PYBLOCK

if [[ $? -eq 0 ]]; then
    info "  Provider block removed."
else
    info "  No chatgpt-bridge provider found — nothing changed."
fi

info ""
info "=========================================================="
info "  ChatGPT Bridge Hermes integration is now uninstalled."
info "=========================================================="
info ""
info "The profile at ~/.hermes/profiles/chatgpt-bridge/ was kept."
info "Remove it manually if you no longer need it:"
info "  rm -rf ~/.hermes/profiles/chatgpt-bridge/"
