"""Output gate: model output is untrusted data. No auto-execution, ever.

Hermes-side policy enforcement point: results returned by the bridge are advisory.
Any later action must pass through Hermes's existing tool authorization layer.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class GatedOutput:
    text: str
    mime_type: str
    sha256: str
    truncated: bool
    full_response_retained: bool
    advisory_only: bool = True


class OutputGate:
    def __init__(self, max_result_chars: int = 100_000):
        self.max_result_chars = max_result_chars

    def gate(self, text: str, mime_type: str = "text/markdown", full_response_retained: bool = True) -> GatedOutput:
        truncated = len(text) > self.max_result_chars
        bounded = text[: self.max_result_chars] if truncated else text
        import hashlib

        return GatedOutput(
            text=bounded,
            mime_type=mime_type,
            sha256=hashlib.sha256(bounded.encode("utf-8")).hexdigest(),
            truncated=truncated,
            full_response_retained=full_response_retained,
            advisory_only=True,
        )
