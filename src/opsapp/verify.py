"""Independent re-calculation of a stored quote.

``independent_recalculation`` deliberately does not import the pricing module: it is a
second, separately written implementation of the documented rules, so a bug in one is
likely to show up as a mismatch. It reads only the inputs stored with the quote version.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy.orm import Session


def _cents(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def independent_recalculation(inputs: dict[str, Any]) -> tuple[list[tuple[str, Decimal]], Decimal]:
    pricing = inputs["pricing"]
    entries = pricing["entries"]
    amounts: list[tuple[str, Decimal]] = []
    items_total = Decimal("0")
    item_amount: dict[str, Decimal] = {}
    qty_of: dict[str, Decimal] = {}
    for req in inputs["requested"]:
        e = entries[req["sku"]]
        qty = Decimal(req["quantity"])
        amt = _cents(qty * Decimal(e["unit_price"]))
        amounts.append((req["sku"], amt))
        item_amount[req["sku"]] = amt
        qty_of[req["sku"]] = qty
        items_total += amt
    rules = pricing["rules"]
    for r in (r for r in rules if r["kind"] == "volume_tier"):
        if r["sku"] in qty_of and qty_of[r["sku"]] >= Decimal(r["min_qty"]):
            d = _cents(item_amount[r["sku"]] * Decimal(r["percent_off"]) / 100)
            amounts.append((r["sku"], -d))
            items_total -= d
    total = items_total
    for r in (r for r in rules if r["kind"] == "minimum_charge"):
        m = _cents(Decimal(r["amount"]))
        if total < m:
            amounts.append(("MIN-CHARGE", m - total))
            total = m
    for r in (r for r in rules if r["kind"] == "trip_fee"):
        if any(entries[q["sku"]]["onsite"] for q in inputs["requested"]):
            fee = _cents(Decimal(r["amount"]))
            amounts.append((r.get("sku", "SITE-TRIP"), fee))
            total += fee
    return amounts, total


def recompute_quote_version(s: Session, quote_version_id: str) -> tuple[bool, str]:
    from .persistence.models import QuoteVersion

    qv = s.get(QuoteVersion, quote_version_id)
    if qv is None:
        return False, "Quote version not found."
    lines, total = independent_recalculation(qv.inputs)
    stored = [(ln["sku"], Decimal(ln["amount"])) for ln in qv.calculation["lines"]]
    ok = lines == stored and total == qv.total
    out = [
        f"Quote version {qv.version_no} ({qv.id}), pricing version "
        f"{qv.inputs['pricing']['version_no']}:"
    ]
    for (sku, amt), (_s2, amt2) in zip(lines, stored, strict=False):
        out.append(
            f"  {sku:<14} recomputed {amt:>10}  stored {amt2:>10}  "
            f"{'ok' if amt == amt2 else 'MISMATCH'}"
        )
    out.append(
        f"  TOTAL          recomputed {total:>10}  stored {qv.total:>10}  "
        f"{'ok' if total == qv.total else 'MISMATCH'}"
    )
    out.append("Result: " + ("MATCH" if ok else "MISMATCH"))
    return ok, "\n".join(out)
