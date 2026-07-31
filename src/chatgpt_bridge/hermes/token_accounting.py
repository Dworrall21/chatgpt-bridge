"""Token accounting per the design formulas.

actual_hermes_tokens   = router + packer + provider + result-ingestion
baseline_hermes_tokens = native subagent + native result-ingestion
net_saved              = baseline - actual
savings_percent        = net_saved / baseline * 100
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TokenLedger:
    router_input: int = 0
    router_output: int = 0
    provider_input: int = 0
    provider_output: int = 0
    result_ingestion_input: int = 0
    result_ingestion_output: int = 0
    native_subagent_input: int = 0
    native_subagent_output: int = 0
    native_result_ingestion: int = 0

    @property
    def actual_hermes_tokens(self) -> int:
        return (
            self.router_input + self.router_output
            + self.provider_input + self.provider_output
            + self.result_ingestion_input + self.result_ingestion_output
        )

    @property
    def baseline_hermes_tokens(self) -> int:
        return self.native_subagent_input + self.native_subagent_output + self.native_result_ingestion


@dataclass
class SavingsResult:
    actual_hermes_tokens: int
    baseline_hermes_tokens: int
    net_saved: int
    savings_percent: float | None
    baseline_method: str
    confidence: str


def compute_savings(ledger: TokenLedger, *, baseline_method: str = "shadow",
                    confidence: str = "medium") -> SavingsResult:
    actual = ledger.actual_hermes_tokens
    baseline = ledger.baseline_hermes_tokens
    net = baseline - actual
    pct = (net / baseline * 100.0) if baseline > 0 else None
    return SavingsResult(
        actual_hermes_tokens=actual,
        baseline_hermes_tokens=baseline,
        net_saved=net,
        savings_percent=pct,
        baseline_method=baseline_method,
        confidence=confidence,
    )
