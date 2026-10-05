"""Wrap the AI reader with a spending budget, one retry, and a rule-based fallback.

If the AI is over budget, fails, or returns something unusable, the request is read by the
rule-based reader instead and the workflow page says so. A request is never left unread
because the AI was unavailable.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal

from .claude import ReaderError
from .ports import ExtractionRun, Extractor, UsageRecord
from .schema import ExtractionContext

MAX_ATTEMPTS = 2  # the first call plus at most one retry


class FallbackExtractor:
    def __init__(
        self,
        primary: Extractor,
        fallback: Extractor,
        spent_usd: Callable[[], Decimal],
        budget_usd: Decimal,
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.spent_usd = spent_usd
        self.budget_usd = budget_usd
        self.provider = primary.provider
        self.model_id = primary.model_id

    def extract(self, text: str, sender: str, context: ExtractionContext) -> ExtractionRun:
        spent = self.spent_usd()
        if spent >= self.budget_usd:
            return self._fall_back(
                text,
                sender,
                context,
                f"AI budget of ${self.budget_usd} reached (${spent:.4f} used)",
                (),
            )
        failed: list[UsageRecord] = []
        reason = "unknown error"
        for _ in range(MAX_ATTEMPTS):
            try:
                run = self.primary.extract(text, sender, context)
            except ReaderError as exc:
                reason = exc.reason
                if exc.usage is not None:
                    failed.append(exc.usage)
                if not exc.retryable:
                    break
                continue
            return replace(run, failed_attempts=tuple(failed))
        return self._fall_back(text, sender, context, reason, tuple(failed))

    def _fall_back(
        self,
        text: str,
        sender: str,
        context: ExtractionContext,
        reason: str,
        failed: tuple[UsageRecord, ...],
    ) -> ExtractionRun:
        run = self.fallback.extract(text, sender, context)
        return replace(run, fallback_reason=reason, failed_attempts=failed)
