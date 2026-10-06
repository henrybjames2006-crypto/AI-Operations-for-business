"""Password hashing with scrypt from the standard library, and the password rules.

Stored format: ``scrypt$<n>$<r>$<p>$<salt b64>$<hash b64>``. The cost settings are stored
with each hash, so they can be raised later without breaking existing passwords.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets

N, R, P = 2**15, 8, 1
_MAXMEM = 64 * 1024 * 1024
MIN_LENGTH = 12
MAX_LENGTH = 128

# A short list of the most common passwords and patterns of 12+ characters. Not exhaustive;
# length does most of the work.
_COMMON = {
    "123456789012",
    "1234567890123",
    "12345678901234",
    "qwertyuiopasdfgh",
    "qwertyuiop123",
    "qwerty123456",
    "password1234",
    "password12345",
    "password123456",
    "passwordpassword",
    "iloveyou1234",
    "letmein12345",
    "welcome12345",
    "welcome123456",
    "administrator",
    "admin1234567",
    "changeme1234",
    "abcdefghijkl",
    "abc123456789",
    "aaaaaaaaaaaa",
    "111111111111",
    "000000000000",
    "trustno11234",
    "football1234",
    "baseball1234",
    "monkey123456",
    "sunshine1234",
    "princess1234",
    "superman1234",
    "starwars1234",
    "dragon123456",
    "master123456",
    "computer1234",
    "internet1234",
    "summer202620",
    "winter202620",
}


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=N, r=R, p=P, maxmem=_MAXMEM)
    return f"scrypt${N}${R}${P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str | None) -> bool:
    """Constant-time check. A missing hash still costs one scrypt run (no timing hint)."""
    if not stored:
        hashlib.scrypt(b"x", salt=b"0" * 16, n=N, r=R, p=P, maxmem=_MAXMEM)
        return False
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=base64.b64decode(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            maxmem=_MAXMEM,
            dklen=len(expected),
        )
    except ValueError:
        return False
    return hmac.compare_digest(actual, expected)


def password_problems(password: str, email: str = "", name: str = "") -> list[str]:
    """Reasons a new password is not allowed. Empty when it is fine."""
    problems: list[str] = []
    if len(password) < MIN_LENGTH:
        problems.append(f"Use at least {MIN_LENGTH} characters.")
    if len(password) > MAX_LENGTH:
        problems.append(f"Use at most {MAX_LENGTH} characters.")
    lowered = password.lower()
    if lowered in _COMMON or len(set(lowered)) <= 3:
        problems.append("This password is too common or too simple.")
    local = email.split("@")[0].lower()
    if (len(local) >= 4 and local in lowered) or (
        name and len(name) >= 4 and name.lower().replace(" ", "") in lowered.replace(" ", "")
    ):
        problems.append("Don't use your name or email address in your password.")
    return problems


_WORDS_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"


def temporary_password() -> str:
    """16 random characters, without look-alike letters, for the owner to hand over."""
    return "".join(secrets.choice(_WORDS_ALPHABET) for _ in range(16))
