#!/usr/bin/env python3
"""Auto-routing decision CLI (Phase 5, kill-switch gated).

Hermes consults this before delegating: for planning/review/design tasks it
returns delegate=True ONLY when CHATGPT_BRIDGE_AUTOROUTE=1 (or policy
enabled=true) and the router gate passes. Explicit --force overrides for
manual delegation decisions.

Usage:
  route.py --kind planning --instruction '...' [--context-chars N] [--requires-tools]
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from chatgpt_bridge.hermes.router import DELEGATABLE_KINDS, should_delegate  # noqa: E402


def _policy_enabled(path: str | None) -> bool:
    if not path or not os.path.exists(path):
        return False
    try:
        import tomllib

        with open(path, "rb") as f:
            data = tomllib.load(f)
        return bool(data.get("enabled", False))
    except Exception:
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Narrow auto-routing decision")
    ap.add_argument("--kind", required=True, choices=sorted(DELEGATABLE_KINDS | {"synthesis", "drafting", "reasoning"}))
    ap.add_argument("--instruction", required=True)
    ap.add_argument("--context-chars", type=int, default=0)
    ap.add_argument("--requires-tools", action="store_true")
    ap.add_argument("--force", action="store_true", help="manual delegation override")
    ap.add_argument("--policy", default=os.path.join(os.path.dirname(__file__), "..", "hermes", "routing", "heavy_reasoning.yaml"))
    args = ap.parse_args()

    enabled = _policy_enabled(args.policy) or os.environ.get("CHATGPT_BRIDGE_AUTOROUTE") == "1"
    decision = should_delegate(
        kind=args.kind,
        instruction=args.instruction,
        context_chars=args.context_chars,
        requires_tools=args.requires_tools,
    )
    delegate = decision.delegate and (enabled or args.force)
    print(json.dumps({
        "delegate": delegate,
        "kind": args.kind,
        "provider": "chatgpt_desktop_bridge" if delegate else None,
        "autonomous_routing_enabled": enabled,
        "router": {"delegate": decision.delegate, "reason": decision.reason},
        "note": "delegation result is advisory text; never auto-execute" if delegate else None,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
