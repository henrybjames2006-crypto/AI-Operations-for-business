"""Validate extractor output against the schema and the tenant's records.

Anything that fails is dropped and noted, never "fixed up": a dropped item becomes a
clarification question later, so missing facts are asked for rather than invented.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from .schema import ExtractionContext, ExtractionOutput

_TIMEFRAMES = {"next_week", "this_week", "tomorrow", "asap"}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def validate_output(
    raw: Any, request_text: str, ctx: ExtractionContext
) -> tuple[ExtractionOutput, list[str]]:
    """Return a cleaned output and a list of problems found."""
    problems: list[str] = []
    try:
        out = raw if isinstance(raw, ExtractionOutput) else ExtractionOutput.model_validate(raw)
    except PydanticValidationError as exc:
        problems.append(f"Extractor output failed schema validation: {exc.error_count()} errors.")
        return ExtractionOutput(request_type="unclear"), problems

    text = _norm(request_text)
    known_skus = {c.sku for c in ctx.catalog}
    known_names = {n for c in ctx.customers for n in [c.name, *c.aliases]}

    items = []
    for item in out.items:
        if item.sku not in known_skus:
            problems.append(f"Dropped suggested SKU {item.sku!r}: not in the catalog.")
            continue
        if _norm(item.evidence) not in text:
            problems.append(f"Dropped {item.sku}: its evidence is not in the request text.")
            continue
        if item.quantity is not None:
            try:
                q = Decimal(item.quantity)
                if not q.is_finite():
                    raise InvalidOperation
            except InvalidOperation:
                problems.append(
                    f"Quantity {item.quantity!r} for {item.sku} is not a number; "
                    f"it will be asked for."
                )
                item = item.model_copy(update={"quantity": None})
        items.append(item)

    customers = [m for m in out.customer_mentions if m in known_names and _norm(m) in text]
    if len(customers) != len(out.customer_mentions):
        problems.append(
            "Dropped customer mentions that are not known customer names found in the text."
        )
    sites = [s for s in out.site_mentions if s in ctx.site_labels and _norm(s) in text]

    timeframe = out.timeframe
    if (
        timeframe
        and timeframe not in _TIMEFRAMES
        and not re.fullmatch(r"date:\d{4}-\d\d-\d\d", timeframe)
    ):
        problems.append(f"Ignored unrecognized timeframe {timeframe!r}.")
        timeframe = None

    cleaned = out.model_copy(
        update={
            "items": items,
            "customer_mentions": customers,
            "site_mentions": sites,
            "timeframe": timeframe,
        }
    )
    return cleaned, problems
