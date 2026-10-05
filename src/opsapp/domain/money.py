"""Exact money arithmetic.

Rules (documented in docs/architecture.md):
- Amounts are ``decimal.Decimal``. Floats are rejected so binary rounding never enters pricing.
- Currency is an ISO code; Checkpoint 1 supports USD only.
- Rounding: every line total is rounded to cents with ROUND_HALF_UP
  (a half cent rounds away from zero). Totals are sums of already rounded lines.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from .errors import PricingError, ValidationError

SUPPORTED_CURRENCIES = frozenset({"USD"})
CENT = Decimal("0.01")


def to_decimal(value: object, *, field: str = "amount") -> Decimal:
    """Convert a str/int/Decimal to Decimal. Floats and booleans are refused."""
    if isinstance(value, (bool, float)):
        raise ValidationError(f"{field} must not be a float or boolean; use a decimal string")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int):
        result = Decimal(value)
    elif isinstance(value, str):
        try:
            result = Decimal(value.strip())
        except InvalidOperation as exc:
            raise ValidationError(f"{field} is not a number: {value!r}") from exc
    else:
        raise ValidationError(f"{field} has unsupported type {type(value).__name__}")
    if not result.is_finite():
        raise ValidationError(f"{field} must be a finite number")
    return result


def round_cents(amount: Decimal) -> Decimal:
    """Round to cents, half away from zero."""
    if not isinstance(amount, Decimal):
        raise PricingError("round_cents requires a Decimal")
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def check_currency(currency: str) -> str:
    code = currency.strip().upper()
    if code not in SUPPORTED_CURRENCIES:
        raise PricingError(f"Currency {currency!r} is not supported (supported: USD)")
    return code


def format_usd(amount: Decimal) -> str:
    sign = "-" if amount < 0 else ""
    return f"{sign}${abs(amount):,.2f}"
