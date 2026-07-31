"""chatgpt-bridge CLI: daemon + discovery subcommands."""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def cmd_daemon(args: argparse.Namespace) -> int:
    from chatgpt_bridge.config import Config
    from chatgpt_bridge.metrics import Metrics
    from chatgpt_bridge.queue import JobQueue
    from chatgpt_bridge.registry import Registry
    from chatgpt_bridge.server import serve

    cfg = Config.load(args.config)
    registry = Registry(args.db)
    metrics = Metrics(args.metrics_jsonl)
    queue = JobQueue(registry, cfg.flags)
    server = serve(cfg, registry, queue, metrics, sock_path=args.socket)
    print(f"chatgpt-bridge listening on {args.socket or cfg.transport.resolve_socket_path()}", flush=True)
    print(f"flags: enabled={cfg.flags.bridge_enabled} create={cfg.flags.allow_conversation_creation} send={cfg.flags.allow_send} autoroute={cfg.flags.automatic_routing}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        registry.close()
    return 0


def cmd_discover(args: argparse.Namespace) -> int:
    from chatgpt_bridge.app.discovery import discover

    result = discover(cdp_url=args.cdp_url, renderer_origin=args.renderer_origin)
    import json

    print(json.dumps(result, indent=2, default=str))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="chatgpt-bridge")
    sub = parser.add_subparsers(dest="command", required=True)

    d = sub.add_parser("daemon", help="run the UDS bridge daemon")
    d.add_argument("--config", default=None, help="TOML config path")
    d.add_argument("--db", default=os.path.expanduser("~/.local/state/chatgpt-bridge/registry.sqlite3"))
    d.add_argument("--socket", default=None, help="override socket path")
    d.add_argument("--metrics-jsonl", default=None)
    d.set_defaults(func=cmd_daemon)

    disc = sub.add_parser("discover", help="Phase 1: read-only app + project discovery")
    disc.add_argument("--cdp-url", default="http://127.0.0.1:9222")
    disc.add_argument("--renderer-origin", default="http://127.0.0.1:5175")
    disc.set_defaults(func=cmd_discover)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
