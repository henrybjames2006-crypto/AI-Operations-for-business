"""The extraction port. Adapters are proposals only and never touch the database."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .schema import ExtractionContext, ExtractionOutput


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


class Extractor(Protocol):
    provider: str
    model_id: str

    def extract(self, text: str, sender: str, context: ExtractionContext) -> ExtractionRun: ...
