"""Content policy: reject prohibited secret classes and enforce size caps."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..errors import PolicyRejectionError

_SECRET_PATTERNS = [
    re.compile(r"\b(?:sk|pk|rk|ak|api[_-]?key|secret|password|passwd|token)\b[=:]\s*['\"]?[A-Za-z0-9_\-\.]{16,}", re.IGNORECASE),
    re.compile(r"ghp_[A-Za-z0-9]{36}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"AIza[0-9A-Za-z_\-]{35}"),
]


@dataclass
class PolicyResult:
    allowed: bool
    reason: str = ""


class ContentPolicy:
    def __init__(self, *, max_instruction_chars: int = 100_000, max_context_items: int = 64,
                 max_context_item_chars: int = 100_000, reject_secrets: bool = True):
        self.max_instruction_chars = max_instruction_chars
        self.max_context_items = max_context_items
        self.max_context_item_chars = max_context_item_chars
        self.reject_secrets = reject_secrets

    def check(self, task: dict) -> PolicyResult:
        if not isinstance(task, dict):
            return PolicyResult(False, "task must be an object")
        instruction = task.get("instruction", "")
        if not isinstance(instruction, str) or not instruction.strip():
            return PolicyResult(False, "instruction required")
        if len(instruction) > self.max_instruction_chars:
            return PolicyResult(False, f"instruction exceeds {self.max_instruction_chars} chars")
        context = task.get("context", []) or []
        if len(context) > self.max_context_items:
            return PolicyResult(False, f"context exceeds {self.max_context_items} items")
        for item in context:
            content = (item or {}).get("content", "")
            if len(content) > self.max_context_item_chars:
                return PolicyResult(False, f"context item exceeds {self.max_context_item_chars} chars")
            if self.reject_secrets and _looks_like_secret(content):
                return PolicyResult(False, "context item contains likely secret material")
        if self.reject_secrets and _looks_like_secret(instruction):
            return PolicyResult(False, "instruction contains likely secret material")
        return PolicyResult(True)


def _looks_like_secret(text: str) -> bool:
    return any(p.search(text) for p in _SECRET_PATTERNS)


def apply_content_policy(policy: ContentPolicy, task: dict) -> None:
    result = policy.check(task)
    if not result.allowed:
        raise PolicyRejectionError(result.reason)
