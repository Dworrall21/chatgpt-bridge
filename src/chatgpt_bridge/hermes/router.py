"""Narrow delegation router.

Delegate ONLY planning / review / design tasks where Sol-level intelligence
clearly pays. General reasoning, synthesis, drafting, short answers, and any
task needing live Hermes tools stay local.
"""

from __future__ import annotations

from dataclasses import dataclass

DELEGATABLE_KINDS = {"planning", "review", "design"}


@dataclass
class RouterDecision:
    delegate: bool
    kind: str | None = None
    reason: str = ""


def _estimate_native_tokens(context_chars: int, instruction_chars: int) -> int:
    # ~4 chars/token heuristic; used only for the benefit-overhead gate.
    return (context_chars + instruction_chars) // 4


def should_delegate(
    *,
    kind: str,
    instruction: str,
    context_chars: int = 0,
    requires_tools: bool = False,
    expected_native_tokens: int | None = None,
    min_tokens: int = 1500,
) -> RouterDecision:
    if kind not in DELEGATABLE_KINDS:
        return RouterDecision(False, kind, f"kind '{kind}' not in delegatable classes (planning/review/design)")
    if requires_tools:
        return RouterDecision(False, kind, "task requires live Hermes tools")
    estimated = expected_native_tokens if expected_native_tokens is not None else _estimate_native_tokens(
        context_chars, len(instruction)
    )
    if estimated < min_tokens:
        return RouterDecision(False, kind, "expected native cost below delegation overhead")
    return RouterDecision(True, kind, "planning/review/design with sufficient expected cost")
