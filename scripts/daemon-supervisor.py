#!/usr/bin/env python3
"""Daemon supervisor: run the bridge only while >=1 Hermes instance is alive.

A Hermes instance is a process whose environ carries HERMES_SESSION_ID
(support daemons like hindsight-api/postgres do not qualify).

Loop every POLL_S: if hermes instances exist -> ensure daemon up; if none for
GRACE_S -> SIGTERM the daemon. Adopts an already-listening daemon instead of
spawning a duplicate.

Run directly for testing:  --once (single pass)  --dry-run (no spawn/kill)
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DAEMON = os.path.join(HERE, "chatgpt-bridge.py")
SOCKET = "/run/user/%s/chatgpt-bridge/bridge.sock" % os.getuid()
DB = os.path.expanduser("~/.local/state/chatgpt-bridge/registry.sqlite3")
METRICS = os.path.expanduser("~/.local/state/chatgpt-bridge/metrics.jsonl")
SECRET_FILE = os.path.expanduser("~/.local/state/chatgpt-bridge/hmac.secret")


def hermes_instances() -> int:
    """Count processes carrying HERMES_SESSION_ID in their environ."""
    count = 0
    for pid in _pids():
        try:
            with open(f"/proc/{pid}/environ", "rb") as f:
                env = f.read()
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        if b"HERMES_SESSION_ID=" in env:
            count += 1
    return count


def _pids() -> list[int]:
    try:
        return [int(p) for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        return []


def daemon_healthy() -> bool:
    try:
        import socket

        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(1)
        s.connect(SOCKET)
        s.sendall(b"GET /v1/health HTTP/1.1\r\nHost: bridge\r\nConnection: close\r\n\r\n")
        data = b""
        while True:
            part = s.recv(4096)
            if not part:
                break
            data += part
        s.close()
        return b"200" in data.split(b"\r\n")[0]
    except Exception:
        return False


def _secret() -> str:
    if os.path.exists(SECRET_FILE):
        return open(SECRET_FILE).read().strip()
    return "test-bridge-secret-0123456789abcdef"  # dev default; supervisor sets it


class Supervisor:
    def __init__(self, poll_s: float = 15.0, grace_s: float = 120.0, dry_run: bool = False):
        self.poll_s = poll_s
        self.grace_s = grace_s
        self.dry_run = dry_run
        self.daemon_proc: subprocess.Popen | None = None
        self.no_hermes_since: float | None = None

    def ensure_daemon(self) -> bool:
        if daemon_healthy():
            self.daemon_proc = None  # adopt external daemon
            return True
        if self.dry_run:
            return False
        if self.daemon_proc is not None and self.daemon_proc.poll() is None:
            return True  # our child still starting
        env = dict(os.environ)
        env.update({
            "CHATGPT_BRIDGE_ENABLED": "1",
            "CHATGPT_BRIDGE_ALLOW_CREATE": "1",
            "CHATGPT_BRIDGE_ALLOW_SEND": "1",
            "CHATGPT_BRIDGE_HMAC_SECRET": _secret(),
        })
        self.daemon_proc = subprocess.Popen(
            [sys.executable, DAEMON, "daemon", "--config",
             os.path.join(HERE, "..", "config", "bridge.example.toml"),
             "--db", DB, "--metrics-jsonl", METRICS],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True

    def stop_daemon(self) -> None:
        if self.dry_run:
            return
        if self.daemon_proc is not None and self.daemon_proc.poll() is None:
            os.killpg(os.getpgid(self.daemon_proc.pid), signal.SIGTERM)
            try:
                self.daemon_proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(self.daemon_proc.pid), signal.SIGKILL)
            self.daemon_proc = None
        elif daemon_healthy():
            # adopted external daemon: find its pid via socket? fall back to pkill
            subprocess.run(["pkill", "-TERM", "-f", "chatgpt-bridge.py daemon"], check=False)

    def tick(self) -> dict:
        n = hermes_instances()
        if n > 0:
            self.no_hermes_since = None
            started = not daemon_healthy()
            self.ensure_daemon()
            return {"hermes": n, "daemon": "up" if daemon_healthy() else "starting", "started": started}
        if self.no_hermes_since is None:
            self.no_hermes_since = time.time()
        idle = time.time() - self.no_hermes_since
        if idle >= self.grace_s and daemon_healthy():
            self.stop_daemon()
            return {"hermes": 0, "daemon": "stopped", "idle_s": round(idle)}
        return {"hermes": 0, "daemon": "up" if daemon_healthy() else "down", "idle_s": round(idle)}

    def run(self) -> None:
        while True:
            print(self.tick(), flush=True)
            time.sleep(self.poll_s)


def main() -> int:
    ap = argparse.ArgumentParser(description="Hermes-aware bridge supervisor")
    ap.add_argument("--once", action="store_true", help="single tick then exit")
    ap.add_argument("--dry-run", action="store_true", help="no spawn/kill")
    ap.add_argument("--poll", type=float, default=15.0)
    ap.add_argument("--grace", type=float, default=120.0)
    args = ap.parse_args()
    sup = Supervisor(poll_s=args.poll, grace_s=args.grace, dry_run=args.dry_run)
    if args.once:
        print(sup.tick(), flush=True)
        return 0
    sup.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
