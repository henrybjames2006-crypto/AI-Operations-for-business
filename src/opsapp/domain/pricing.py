"""Deterministic quote calculation.

Inputs are catalog SKUs with quantities plus a snapshot of one approved pricing version.
Nothing here accepts a price, discount or tax from the caller: every amount comes from
the snapshot. Rules are applied in a fixed order:

1. Item lines: quantity x unit price, each rounded to cents (ROUND_HALF_UP).
2. Volume tiers: a percentage off one SKU's line when its quantity reaches a threshold.
3. Minimum charge: if items after tiers total less than the minimum, an adjustment line
   brings them up to it.
4. Trip fee: one fee per quote when any line is performed on site.

Tax is not calculated in Checkpoint 1 and is reported as such.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any

from .errors import PricingError
from .money import check_currency, format_usd, round_cents, to_decimal

RULE_ORDER = ("volume_tier", "minimum_charge", "trip_fee")


@dataclass(frozen=True)
class PriceEntry:
    sku: str
    name: str
    unit: str
    unit_price: Decimal
    currency: str
    min_qty: Decimal = Decimal(1)
    max_qty: Decimal = Decimal(1000)
    quantity_step: Decimal = Decimal(1)
    onsite: bool = True


@dataclass(frozen=True)
class PricingSnapshot:
    pricing_version_id: str
    version_no: int
    currency: str
    entries: Mapping[str, PriceEntry]
    rules: Sequence[Mapping[str, Any]] = ()


@dataclass(frozen=True)
class RequestedLine:
    sku: str
    quantity: Decimal


@dataclass(frozen=True)
class QuoteLine:
    kind: str  # item | discount | minimum | fee
    sku: str
    description: str
    quantity: Decimal
    unit: str
    unit_price: Decimal
    amount: Decimal
    calculation: str


@dataclass(frozen=True)
class RuleResult:
    kind: str
    applied: bool
    explanation: str


@dataclass(frozen=True)
class QuoteCalculation:
    currency: str
    pricing_version_id: str
    version_no: int
    lines: tuple[QuoteLine, ...]
    rules: tuple[RuleResult, ...]
    total: Decimal
    tax_note: str = "Tax not calculated in this prototype."
    notes: tuple[str, ...] = field(default_factory=tuple)

    def to_json(self) -> dict[str, Any]:
        """Serializable form; all amounts as strings so no float ever appears."""

        def conv(v: Any) -> Any:
            if isinstance(v, Decimal):
                return str(v)
            if isinstance(v, dict):
                return {k: conv(x) for k, x in v.items()}
            if isinstance(v, (list, tuple)):
                return [conv(x) for x in v]
            return v

        return conv(asdict(self))


def quantity_problem(entry: PriceEntry, quantity: Decimal | None) -> str | None:
    """Return a plain-language problem with a quantity, or None if it is acceptable."""
    if quantity is None:
        return f"Quantity for {entry.name} was not stated."
    if not isinstance(quantity, Decimal):
        return "Quantity must be an exact number."
    if quantity <= 0:
        return f"Quantity {quantity} for {entry.name} must be greater than zero."
    if quantity % entry.quantity_step != 0:
        return (
            f"Quantity {quantity} for {entry.name} must be a multiple of "
            f"{entry.quantity_step} {entry.unit}."
        )
    if quantity < entry.min_qty:
        return f"Quantity {quantity} for {entry.name} is below the minimum of {entry.min_qty}."
    if quantity > entry.max_qty:
        return (
            f"Quantity {quantity} for {entry.name} is above the maximum of "
            f"{entry.max_qty} per quote."
        )
    return None


def _qty_text(q: Decimal) -> str:
    return format(q.normalize(), "f") if q == q.to_integral_value() else str(q)


def calculate_quote(
    requested: Iterable[RequestedLine], snapshot: PricingSnapshot
) -> QuoteCalculation:
    currency = check_currency(snapshot.currency)
    req = list(requested)
    if not req:
        raise PricingError("A quote needs at least one catalog item.")
    seen: set[str] = set()
    lines: list[QuoteLine] = []
    item_amounts: dict[str, Decimal] = {}
    for r in req:
        if r.sku in seen:
            raise PricingError(f"SKU {r.sku} appears twice; combine the quantities.")
        seen.add(r.sku)
        entry = snapshot.entries.get(r.sku)
        if entry is None:
            raise PricingError(
                f"SKU {r.sku} is not in approved pricing version {snapshot.version_no}."
            )
        if check_currency(entry.currency) != currency:
            raise PricingError(
                f"Mixed currencies: {entry.sku} is priced in {entry.currency}, "
                f"quote is in {currency}."
            )
        qty = to_decimal(r.quantity, field=f"quantity for {r.sku}")
        problem = quantity_problem(entry, qty)
        if problem:
            raise PricingError(problem)
        unit_price = to_decimal(entry.unit_price, field=f"unit price for {r.sku}")
        if unit_price < 0:
            raise PricingError(f"Unit price for {r.sku} is negative.")
        amount = round_cents(qty * unit_price)
        item_amounts[r.sku] = amount
        lines.append(
            QuoteLine(
                kind="item",
                sku=entry.sku,
                description=entry.name,
                quantity=qty,
                unit=entry.unit,
                unit_price=unit_price,
                amount=amount,
                calculation=f"{_qty_text(qty)} x {format_usd(unit_price)} = {format_usd(amount)}",
            )
        )

    rules_by_kind: dict[str, list[Mapping[str, Any]]] = {}
    for rule in snapshot.rules:
        kind = str(rule.get("kind"))
        if kind not in RULE_ORDER:
            raise PricingError(f"Unknown pricing rule kind {kind!r}.")
        rules_by_kind.setdefault(kind, []).append(rule)

    results: list[RuleResult] = []

    # 2. Volume tiers
    for rule in rules_by_kind.get("volume_tier", []):
        sku = str(rule["sku"])
        threshold = to_decimal(rule["min_qty"], field="volume tier threshold")
        pct = to_decimal(rule["percent_off"], field="volume tier percent")
        line = next((ln for ln in lines if ln.kind == "item" and ln.sku == sku), None)
        if line is None:
            continue
        if line.quantity >= threshold:
            discount = round_cents(line.amount * pct / Decimal(100))
            lines.append(
                QuoteLine(
                    kind="discount",
                    sku=sku,
                    description=f"Volume tier: {pct}% off {line.description}",
                    quantity=Decimal(1),
                    unit="adjustment",
                    unit_price=-discount,
                    amount=-discount,
                    calculation=(
                        f"{pct}% of {format_usd(line.amount)} = {format_usd(discount)}, "
                        f"rounded half up to cents"
                    ),
                )
            )
            results.append(
                RuleResult(
                    "volume_tier",
                    True,
                    f"{_qty_text(line.quantity)} {line.unit} reaches the "
                    f"{_qty_text(threshold)} threshold for {sku}.",
                )
            )
        else:
            results.append(
                RuleResult(
                    "volume_tier",
                    False,
                    f"{sku} tier needs {_qty_text(threshold)} or more; quote has "
                    f"{_qty_text(line.quantity)}.",
                )
            )

    items_after_tiers = sum((ln.amount for ln in lines), Decimal("0.00"))

    # 3. Minimum charge
    for rule in rules_by_kind.get("minimum_charge", []):
        minimum = round_cents(to_decimal(rule["amount"], field="minimum charge"))
        if items_after_tiers < minimum:
            adj = minimum - items_after_tiers
            lines.append(
                QuoteLine(
                    kind="minimum",
                    sku="MIN-CHARGE",
                    description="Minimum charge adjustment",
                    quantity=Decimal(1),
                    unit="adjustment",
                    unit_price=adj,
                    amount=adj,
                    calculation=(
                        f"{format_usd(minimum)} minimum - {format_usd(items_after_tiers)} "
                        f"items = {format_usd(adj)}"
                    ),
                )
            )
            results.append(
                RuleResult(
                    "minimum_charge",
                    True,
                    f"Items total {format_usd(items_after_tiers)}, below the "
                    f"{format_usd(minimum)} minimum.",
                )
            )
        else:
            results.append(
                RuleResult(
                    "minimum_charge",
                    False,
                    f"Items total {format_usd(items_after_tiers)} meets the "
                    f"{format_usd(minimum)} minimum.",
                )
            )

    # 4. Trip fee
    for rule in rules_by_kind.get("trip_fee", []):
        fee = round_cents(to_decimal(rule["amount"], field="trip fee"))
        onsite = [r.sku for r in req if snapshot.entries[r.sku].onsite]
        if onsite:
            lines.append(
                QuoteLine(
                    kind="fee",
                    sku=str(rule.get("sku", "SITE-TRIP")),
                    description=str(rule.get("label", "Site visit trip fee")),
                    quantity=Decimal(1),
                    unit="visit",
                    unit_price=fee,
                    amount=fee,
                    calculation=f"1 x {format_usd(fee)} = {format_usd(fee)}",
                )
            )
            results.append(RuleResult("trip_fee", True, "One site visit assumed for on-site work."))
        else:
            results.append(RuleResult("trip_fee", False, "No on-site work, so no trip fee."))

    total = sum((ln.amount for ln in lines), Decimal("0.00"))
    return QuoteCalculation(
        currency=currency,
        pricing_version_id=snapshot.pricing_version_id,
        version_no=snapshot.version_no,
        lines=tuple(lines),
        rules=tuple(results),
        total=total,
    )
