"""Canonical hashing for approvals and the audit chain."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any


def _default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"Cannot hash value of type {type(value).__name__}")


def canonical_json(data: Any) -> str:
    """Stable JSON: sorted keys, no whitespace, Decimals as strings."""
    return json.dumps(
        data, sort_keys=True, separators=(",", ":"), default=_default, ensure_ascii=False
    )


def sha256_hex(data: Any) -> str:
    text = data if isinstance(data, str) else canonical_json(data)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
