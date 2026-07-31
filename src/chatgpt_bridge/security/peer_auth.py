"""Peer authentication over Unix domain sockets (SO_PEERCRED) and replay guards."""

from __future__ import annotations

import os
import socket
import time

from ..errors import AuthenticationFailedError


def peer_uid(sock: socket.socket) -> int:
    """Return the peer UID via SO_PEERCRED on Linux."""
    try:
        cred = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        pid, uid, gid = struct_unpack(cred)
        return uid
    except OSError as e:  # pragma: no cover - platform dependent
        raise AuthenticationFailedError(f"SO_PEERCRED unavailable: {e}")


def struct_unpack(cred: bytes):
    import struct

    return struct.unpack("3i", cred)


class NonceGuard:
    """Reject replayed nonces within a rolling window."""

    def __init__(self, window_seconds: int = 600, max_entries: int = 10_000):
        self.window = window_seconds
        self._seen: dict[str, float] = {}
        self._max = max_entries

    def check(self, nonce: str, now: float | None = None) -> None:
        now = now if now is not None else time.time()
        cutoff = now - self.window
        expired = [k for k, ts in self._seen.items() if ts < cutoff]
        for k in expired:
            del self._seen[k]
        if len(self._seen) >= self._max:
            self._seen.clear()
        if nonce in self._seen:
            raise AuthenticationFailedError("replayed nonce")
        self._seen[nonce] = now
