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


def _customer_name_in_text(mention: str, text: str, ctx: ExtractionContext) -> str | None:
    """The spelling of ``mention``'s customer that actually appears in the text, if any.

    A reader may give the full listed name ("Summit Bakery Co") when the request uses an
    alias ("Summit Bakery"). That still names one known customer, so the spelling found in
    the text is kept. A name that is not in the text at all is dropped.
    """
    for c in ctx.customers:
        spellings = [c.name, *c.aliases]
        if mention not in spellings:
            continue
        present = [n for n in spellings if _norm(n) in text]
        return max(present, key=len) if present else None
    return None


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

    customers: list[str] = []
    for m in out.customer_mentions:
        found = _customer_name_in_text(m, text, ctx)
        if found is None:
            problems.append(
                f"Dropped customer mention {m!r}: not a known customer name found in the text."
            )
        elif found not in customers:
            customers.append(found)
    # A site label inside a customer's own name ("Bakery" in "Summit Bakery Co") is not a
    # site mention, so names are removed before sites are looked for.
    site_text = text
    for name in sorted((_norm(n) for n in known_names), key=len, reverse=True):
        site_text = site_text.replace(name, " ")
    sites = [s for s in out.site_mentions if s in ctx.site_labels and _norm(s) in site_text]

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
