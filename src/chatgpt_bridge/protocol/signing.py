"""HMAC request signing over a canonical header string.

Canonical input:
    <METHOD>\n\n<PATH>\n\n<X-Bridge-Timestamp>\n\n<X-Bridge-Nonce>\n\n<X-Content-SHA256>\n\n<Idempotency-Key>
"""

from __future__ import annotations

import hmac
import hashlib
import secrets
import time


class SigningError(ValueError):
    pass


def canonical_input(method: str, path: str, timestamp_ms: str, nonce: str, content_sha256: str, idempotency_key: str) -> str:
    return "\n".join([method, path, timestamp_ms, nonce, content_sha256, idempotency_key])


def build_hmac(secret: bytes, method: str, path: str, timestamp_ms: str, nonce: str, content_sha256: str, idempotency_key: str) -> str:
    data = canonical_input(method, path, timestamp_ms, nonce, content_sha256, idempotency_key)
    return hmac.new(secret, data.encode("utf-8"), hashlib.sha256).hexdigest()


def new_nonce() -> str:
    return secrets.token_hex(16)


def verify_hmac(
    secret: bytes,
    method: str,
    path: str,
    timestamp_ms: str,
    nonce: str,
    content_sha256: str,
    idempotency_key: str,
    provided: str,
    *,
    tolerance_seconds: int = 30,
    now_ms: int | None = None,
) -> None:
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    try:
        ts = int(timestamp_ms)
    except (TypeError, ValueError):
        raise SigningError("invalid timestamp")
    if abs(now_ms - ts) > tolerance_seconds * 1000:
        raise SigningError("timestamp outside tolerance window")
    if not nonce or len(nonce) < 16:
        raise SigningError("invalid nonce")
    expected = build_hmac(secret, method, path, timestamp_ms, nonce, content_sha256, idempotency_key)
    if not hmac.compare_digest(expected, provided):
        raise SigningError("HMAC mismatch")
