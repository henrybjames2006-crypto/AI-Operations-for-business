"""The extraction port. Adapters are proposals only and never touch the database."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .schema import ExtractionContext, ExtractionOutput


@dataclass(frozen=True)
class UsageRecord:
    """One model call, successful or not, for cost tracking."""

    provider: str
    model_id: str
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int
    est_cost_usd: str
    outcome: str  # "ok" or a short failure reason


@dataclass(frozen=True)
class ExtractionRun:
    output: ExtractionOutput
    provider: str
    model_id: str
    latency_ms: int
    input_chars: int
    output_chars: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    est_cost_usd: str = "0"
    # Set when the AI reader was tried and the rule-based reader was used instead.
    fallback_reason: str | None = None
    # Failed AI calls made before this result (each one may have cost money).
    failed_attempts: tuple[UsageRecord, ...] = ()


class Extractor(Protocol):
    provider: str
    model_id: str

    def extract(self, text: str, sender: str, context: ExtractionContext) -> ExtractionRun: ...
