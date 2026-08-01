#!/usr/bin/env python3
"""Hermes delegate tool: submit a planning/review/design packet to the
ChatGPT desktop bridge (project-confined, GPT-5.6 Sol @ High).

Explicit invocation only (Phase 3). No automatic routing.

Usage:
  delegate_chatgpt_desktop.py --kind planning --instruction '...' \
      --context 'label=path.txt' --constraint 'read-only' \
      --session-id <hermes session id> [--timeout 600]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from chatgpt_bridge.config import Config  # noqa: E402
from chatgpt_bridge.hermes.context_packer import pack_context  # noqa: E402
from chatgpt_bridge.hermes.provider import BridgeClient  # noqa: E402
from chatgpt_bridge.hermes.router import should_delegate  # noqa: E402


def _read_context(spec: str) -> tuple[str, str]:
    """'label=path' reads file; bare 'path' uses basename as label."""
    if "=" in spec:
        label, path = spec.split("=", 1)
    else:
        label, path = Path(spec).name, spec
    content = Path(path).read_text(encoding="utf-8", errors="replace")
    return label, content


def main() -> int:
    ap = argparse.ArgumentParser(description="Delegate a reasoning task to the ChatGPT desktop bridge")
    ap.add_argument("--kind", required=True, choices=["planning", "review", "design"])
    ap.add_argument("--instruction", required=True)
    ap.add_argument("--context", action="append", default=[], help="label=/path/file (repeatable)")
    ap.add_argument("--constraint", action="append", default=[])
    ap.add_argument("--output-format", default="markdown", choices=["text", "markdown", "json"])
    ap.add_argument("--detail", default="standard", choices=["brief", "standard", "detailed"],
                    help="response depth: brief (~1.2k chars), standard (~4k), detailed (~12k)")
    ap.add_argument("--max-characters", type=int, default=None,
                    help="explicit output cap; overrides the --detail default")
    ap.add_argument("--session-id", default=os.environ.get("HERMES_SESSION_ID", "cli"))
    ap.add_argument("--new-shard", action="store_true", help="force a new project conversation (default: reuse session conversation)")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--config", default=None)
    ap.add_argument("--dry-run", action="store_true", help="build + submit only, no poll")
    args = ap.parse_args()

    # Narrow router gate.
    context_chars = sum(len(content) for _label, content in [_read_context(spec) for spec in args.context])
    decision = should_delegate(
        kind=args.kind,
        instruction=args.instruction,
        context_chars=context_chars,
        requires_tools=False,
    )
    if not decision.delegate:
        print(json.dumps({"ok": False, "reason": decision.reason}), file=sys.stderr)
        return 2

    # Compact packet.
    selected_facts = [_read_context(spec) for spec in args.context]
    detail_defaults = {"brief": 1200, "standard": 4000, "detailed": 12000}
    max_chars = args.max_characters or detail_defaults.get(args.detail, 4000)
    packed = pack_context(
        instruction=args.instruction,
        constraints=args.constraint or None,
        selected_facts=selected_facts,
        output_format=args.output_format,
        max_characters=max_chars,
    )
    packed.output_contract["detail"] = args.detail

    request = {
        "protocol_version": "1.0",
        "request_id": str(uuid.uuid4()),
        "idempotency_key": f"idem-{uuid.uuid4().hex[:20]}",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "deadline_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + args.timeout)),
        "hermes": {"session_id": args.session_id, "task_id": f"t-{int(time.time())}", "route": "delegate_task"},
        "target_assertions": {
            "project_name": "chatgpt-bridge",
            "mode": "Work",
            "model_label": "GPT-5.6 Sol",
            "effort_label": "High",
        },
        "conversation": {"policy": "new_shard" if args.new_shard else "reuse_session"},
        "task": {
            "kind": args.kind,
            "instruction": packed.instruction,
            "context": packed.context,
            "constraints": packed.constraints,
            "output_contract": packed.output_contract,
        },
    }

    cfg = Config.load(args.config)
    secret = cfg.transport.secret()
    sock = cfg.transport.resolve_socket_path()
    client = BridgeClient(sock, secret, connect_timeout=5.0)

    code, env = client.submit(request)
    if code not in (200, 202):
        print(json.dumps({"ok": False, "http": code, "error": env.get("error")}), file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "accepted": True, "request_id": request["request_id"],
                      "status": env.get("status"), "idempotency_key": request["idempotency_key"]}))

    if args.dry_run:
        return 0

    deadline = time.time() + args.timeout
    while time.time() < deadline:
        time.sleep(2)
        code, env = client.poll(request["request_id"])
        status = env.get("status")
        if status == "completed":
            result = env.get("result") or {}
            print(json.dumps({
                "ok": True,
                "request_id": request["request_id"],
                "status": "completed",
                "binding": env.get("binding"),
                "execution": env.get("execution"),
                "result": result.get("text", ""),
                "result_sha256": result.get("sha256"),
                "usage": env.get("usage"),
            }))
            return 0
        if status == "failed":
            print(json.dumps({"ok": False, "status": "failed", "error": env.get("error")}), file=sys.stderr)
            return 1
        if status in ("needs_reconciliation", "cancelled"):
            print(json.dumps({"ok": False, "status": status, "error": env.get("error")}), file=sys.stderr)
            return 1
    print(json.dumps({"ok": False, "status": "timeout"}), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
