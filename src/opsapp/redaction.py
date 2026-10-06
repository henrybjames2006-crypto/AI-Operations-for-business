"""Redaction for logs and exports.

Removes the values of configured secret environment variables and common credential
patterns. This is a safety net, not a guarantee: code should not log secrets to begin with.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from .config import SECRET_ENV_NAMES

_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*\S+"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/\-]{16,}=*"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
]
REDACTED = "[REDACTED]"


def _secret_values() -> list[str]:
    return [v for name in SECRET_ENV_NAMES if (v := os.environ.get(name)) and len(v) >= 8]


def redact_text(text: str, extra_secrets: list[str] | None = None) -> str:
    for value in [*_secret_values(), *(extra_secrets or [])]:
        if value:
            text = text.replace(value, REDACTED)
    for pattern in _PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text


def redact(data: Any, extra_secrets: list[str] | None = None) -> Any:
    if isinstance(data, str):
        return redact_text(data, extra_secrets)
    if isinstance(data, dict):
        return {k: redact(v, extra_secrets) for k, v in data.items()}
    if isinstance(data, list):
        return [redact(v, extra_secrets) for v in data]
    return data


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_text(record.getMessage())
        record.args = None
        return True


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.addFilter(RedactingFilter())
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    logging.getLogger("alembic").setLevel(logging.WARNING)
