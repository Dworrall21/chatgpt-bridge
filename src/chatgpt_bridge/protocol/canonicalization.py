"""Body canonicalization and hashing."""

from __future__ import annotations

import hashlib
import json


def canonical_bytes(body: dict) -> bytes:
    """Canonical JSON bytes: compact separators, stable key order from input dict.

    The HMAC covers the exact request bytes; both sides must hash the identical
    serialized body, so the sender pins Content-SHA256 of the exact bytes sent.
    """
    return json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
