"""ContextPacker: build the smallest self-contained task packet.

Never forwards the full Hermes transcript by default.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256


@dataclass
class PackedPacket:
    instruction: str
    context: list[dict] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    output_contract: dict = field(default_factory=dict)

    @property
    def total_chars(self) -> int:
        return len(self.instruction) + sum(len(c.get("content", "")) for c in self.context)


def pack_context(*, instruction: str, constraints: list[str] | None = None,
                 selected_facts: list[tuple[str, str]] | None = None,
                 output_format: str = "markdown", max_characters: int = 6000,
                 required_sections: list[str] | None = None) -> PackedPacket:
    items = []
    for label, content in (selected_facts or []):
        items.append({
            "label": label,
            "content": content,
            "sha256": sha256(content.encode("utf-8")).hexdigest(),
        })
    return PackedPacket(
        instruction=instruction,
        context=items,
        constraints=constraints or [],
        output_contract={
            "format": output_format,
            "max_characters": max_characters,
            "required_sections": required_sections or ["Conclusion", "Key rationale", "Risks / uncertainties"],
        },
    )
