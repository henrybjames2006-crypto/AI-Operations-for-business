"""Stable, prefixed, time-sortable identifiers (ULID layout, Crockford base32).

Example: ``quo_01JA8Z3V6R2N4K7PQ9XW5TB1CD``. The prefix names the entity so an ID pasted
into a log or a bug report says what it refers to.
"""

from __future__ import annotations

import os
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

PREFIXES = {
    "tenant": "ten",
    "user": "usr",
    "customer": "cus",
    "site": "sit",
    "request": "req",
    "extraction": "ext",
    "question": "clq",
    "catalog_item": "cat",
    "pricing_version": "prv",
    "price_entry": "pre",
    "quote": "quo",
    "quote_version": "qv",
    "workflow": "wf",
    "action": "act",
    "approval": "apv",
    "outbox": "obx",
    "attempt": "att",
    "external_operation": "xop",
    "audit": "aud",
    "exception": "exc",
    "ai_usage": "aiu",
    "sim_operation": "sim",
    "sim_fault": "flt",
    "auth_session": "ses",
    "recovery_code": "rcv",
}


def _encode(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        chars.append(_ALPHABET[value & 31])
        value >>= 5
    return "".join(reversed(chars))


def new_id(entity: str) -> str:
    prefix = PREFIXES[entity]
    ms = int(time.time() * 1000) & ((1 << 48) - 1)
    rand = int.from_bytes(os.urandom(10), "big")
    return f"{prefix}_{_encode(ms, 10)}{_encode(rand, 16)}"
