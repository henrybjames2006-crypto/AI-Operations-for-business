"""Authenticator app codes (TOTP, RFC 6238: SHA-1, 6 digits, 30-second steps).

These are the settings every common authenticator app uses. Codes from one step before or
after the current one are accepted to allow for clock drift, and a step that was already
used is refused so a code can't be replayed.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
from datetime import datetime
from urllib.parse import quote, urlencode

STEP_SECONDS = 30
DIGITS = 6
ISSUER = "Ops workflow prototype"


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii")


def _code(secret: str, step: int) -> str:
    key = base64.b32decode(secret, casefold=True)
    mac = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    value = struct.unpack(">I", mac[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**DIGITS).zfill(DIGITS)


def step_at(now: datetime) -> int:
    return int(now.timestamp()) // STEP_SECONDS


def code_at(secret: str, now: datetime) -> str:
    return _code(secret, step_at(now))


def matching_step(secret: str, code: str, now: datetime, last_step: int | None) -> int | None:
    """The step the code belongs to, or None if it is wrong or was already used."""
    code = "".join(ch for ch in code if ch.isdigit())
    if len(code) != DIGITS:
        return None
    current = step_at(now)
    for step in (current - 1, current, current + 1):
        if last_step is not None and step <= last_step:
            continue
        if hmac.compare_digest(_code(secret, step), code):
            return step
    return None


def provisioning_uri(secret: str, account: str) -> str:
    label = quote(f"{ISSUER}:{account}")
    query = urlencode({"secret": secret, "issuer": ISSUER, "digits": DIGITS, "period": 30})
    return f"otpauth://totp/{label}?{query}"


def qr_svg_data_uri(text: str) -> str:
    import segno

    return str(segno.make(text, error="m").svg_data_uri(scale=5, border=2))


def grouped(secret: str) -> str:
    """The setup key in groups of four, for typing in by hand."""
    return " ".join(secret[i : i + 4] for i in range(0, len(secret), 4))
