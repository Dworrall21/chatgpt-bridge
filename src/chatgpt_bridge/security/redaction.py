"""Log redaction: default logs carry ids/hashes/states, never full content."""

from __future__ import annotations

import re

_SESSION_RE = re.compile(r"(?i)(session[_-]?id)[=:]\s*([A-Za-z0-9_\-]{8,})")
_BEARER_RE = re.compile(r"(?i)(bearer|authorization)[=:]\s*\S+")


def redact_log(text: str) -> str:
    out = _SESSION_RE.sub(r"\1=<redacted>", text)
    out = _BEARER_RE.sub(r"\1=<redacted>", out)
    return out
