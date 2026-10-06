"""Quote arithmetic: exact decimals, documented rounding, fixed rule order."""

from decimal import Decimal as D

import pytest
from hypothesis import given
from hypothesis import strategies as st

from opsapp.domain.errors import PricingError, ValidationError
from opsapp.domain.money import round_cents, to_decimal
from opsapp.domain.pricing import (
    PriceEntry,
    PricingSnapshot,
    RequestedLine,
    calculate_quote,
    quantity_problem,
)
from opsapp.verify import independent_recalculation
from opsapp.workflow.catalog import snapshot_to_json

RULES = [
    {"kind": "volume_tier", "sku": "WS", "min_qty": "10", "percent_off": "5"},
    {"kind": "minimum_charge", "amount": "250.00"},
    {"kind": "trip_fee", "sku": "SITE-TRIP", "label": "Trip", "amount": "75.00"},
]


def snap(rules=RULES, **over):  # type: ignore[no-untyped-def]
    entries = {
        "WS": PriceEntry("WS", "Workstation", "workstation", D("185.00"), "USD", D(1), D(50)),
        "MIG": PriceEntry("MIG", "Migration", "PC", D("95.00"), "USD", D(1), D(50)),
        "HR": PriceEntry(
            "HR", "Hour", "hour", D("125.00"), "USD", D("0.5"), D(40), quantity_step=D("0.5")
        ),
        "ODD": PriceEntry("ODD", "Odd", "unit", D("0.125"), "USD", D(1), D(1000)),
        "REMOTE": PriceEntry(
            "REMOTE", "Remote", "user", D("40.00"), "USD", D(1), D(100), onsite=False
        ),
    }
    entries.update(over)
    return PricingSnapshot("prv_test", 3, "USD", entries, rules)


def test_demo_quote_matches_hand_calculation() -> None:
    # 5 x 185.00 = 925.00; 5 x 95.00 = 475.00; trip fee 75.00 => 1,475.00
    calc = calculate_quote([RequestedLine("WS", D(5)), RequestedLine("MIG", D(5))], snap())
    assert [ln.amount for ln in calc.lines] == [D("925.00"), D("475.00"), D("75.00")]
    assert calc.total == D("1475.00")
    applied = {r.kind: r.applied for r in calc.rules}
    assert applied == {"volume_tier": False, "minimum_charge": False, "trip_fee": True}


def test_volume_tier_discount_rounds_half_up() -> None:
    # 11 x 185.00 = 2035.00; 5% = 101.75 => 1933.25 + 75 = 2008.25
    calc = calculate_quote([RequestedLine("WS", D(11))], snap())
    assert [ln.amount for ln in calc.lines] == [D("2035.00"), D("-101.75"), D("75.00")]
    assert calc.total == D("2008.25")


def test_minimum_charge_then_trip_fee() -> None:
    calc = calculate_quote([RequestedLine("MIG", D(1))], snap())
    assert [(ln.kind, ln.amount) for ln in calc.lines] == [
        ("item", D("95.00")),
        ("minimum", D("155.00")),
        ("fee", D("75.00")),
    ]
    assert calc.total == D("325.00")


def test_no_trip_fee_for_remote_only_work() -> None:
    calc = calculate_quote([RequestedLine("REMOTE", D(10))], snap())
    assert calc.total == D("400.00")
    assert not any(ln.kind == "fee" for ln in calc.lines)


def test_line_rounding_is_half_up_per_line() -> None:
    # 3 x 0.125 = 0.375 -> 0.38 (half up), not banker's 0.38/0.37 ambiguity
    calc = calculate_quote([RequestedLine("ODD", D(3))], snap(rules=[]))
    assert calc.lines[0].amount == D("0.38")
    assert round_cents(D("0.005")) == D("0.01")
    assert round_cents(D("-0.005")) == D("-0.01")


def test_mixed_currency_rejected() -> None:
    eur = PriceEntry("EUR1", "Euro item", "unit", D("10.00"), "EUR", D(1), D(5))
    with pytest.raises(PricingError, match="Currency|currenc"):
        calculate_quote([RequestedLine("WS", D(1)), RequestedLine("EUR1", D(1))], snap(EUR1=eur))


def test_non_usd_quote_rejected() -> None:
    s = PricingSnapshot("p", 1, "EUR", snap().entries, [])
    with pytest.raises(PricingError):
        calculate_quote([RequestedLine("WS", D(1))], s)


def test_unknown_sku_and_duplicates_rejected() -> None:
    with pytest.raises(PricingError, match="not in approved pricing"):
        calculate_quote([RequestedLine("NOPE", D(1))], snap())
    with pytest.raises(PricingError, match="twice"):
        calculate_quote([RequestedLine("WS", D(1)), RequestedLine("WS", D(2))], snap())
    with pytest.raises(PricingError, match="at least one"):
        calculate_quote([], snap())


@pytest.mark.parametrize(
    "qty, fragment",
    [
        (D(0), "greater than zero"),
        (D(-3), "greater than zero"),
        (D("2.5"), "multiple of"),
        (D(51), "above the maximum"),
        (None, "not stated"),
    ],
)
def test_invalid_quantities(qty, fragment) -> None:  # type: ignore[no-untyped-def]
    entry = snap().entries["WS"]
    problem = quantity_problem(entry, qty)
    assert problem and fragment in problem
    if qty is not None:
        with pytest.raises(PricingError):
            calculate_quote([RequestedLine("WS", qty)], snap())


def test_half_hour_steps_allowed_for_hourly_items() -> None:
    assert quantity_problem(snap().entries["HR"], D("1.5")) is None
    assert quantity_problem(snap().entries["HR"], D("1.25")) is not None


def test_floats_never_enter_pricing() -> None:
    with pytest.raises(ValidationError):
        to_decimal(1.1)
    with pytest.raises(ValidationError):
        to_decimal(True)
    with pytest.raises(ValidationError):
        calculate_quote([RequestedLine("WS", 2.0)], snap())  # type: ignore[arg-type]


@given(ws=st.integers(1, 50), mig=st.integers(0, 50), hours=st.integers(0, 80))
def test_property_total_is_sum_of_lines_and_matches_independent_check(ws, mig, hours) -> None:  # type: ignore[no-untyped-def]
    lines = [RequestedLine("WS", D(ws))]
    if mig:
        lines.append(RequestedLine("MIG", D(mig)))
    if hours:
        lines.append(RequestedLine("HR", D(hours) / 2))
    s = snap()
    calc = calculate_quote(lines, s)
    assert calc.total == sum(ln.amount for ln in calc.lines)
    assert all(ln.amount == round_cents(ln.amount) for ln in calc.lines)
    assert calc.total >= D("250.00")
    inputs = {
        "requested": [{"sku": r.sku, "quantity": str(r.quantity)} for r in lines],
        "pricing": snapshot_to_json(s, [r.sku for r in lines]),
    }
    recomputed, total = independent_recalculation(inputs)
    assert total == calc.total
    assert recomputed == [(ln.sku, ln.amount) for ln in calc.lines]
