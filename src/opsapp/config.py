"""Configuration from environment variables (and an optional untracked .env file).

Secrets never live in source code. For local development they come from the environment
or ``.env``. A hosted deployment would replace ``load_settings`` with a managed secret
store; nothing else reads secrets directly.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

SECRET_ENV_NAMES = ("OPSAPP_SESSION_SECRET", "OPSAPP_AI_API_KEY")
AI_PROVIDERS = ("mock", "anthropic")
AI_MODELS = ("claude-haiku-4-5", "claude-sonnet-5-5", "claude-opus-5-5")


def _read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@dataclass(frozen=True)
class Settings:
    database_path: Path
    session_secret: str = field(repr=False)
    host: str = "127.0.0.1"
    port: int = 8000
    ai_provider: str = "mock"
    ai_model: str = "claude-haiku-4-5"
    ai_api_key: str = field(default="", repr=False)
    ai_budget_usd: Decimal = Decimal("5.00")
    ai_timeout_seconds: float = 30.0
    dispatch_poll_seconds: float = 2.0
    action_timeout_seconds: float = 10.0
    max_attempts: int = 3
    max_request_chars: int = 20_000

    def __post_init__(self) -> None:
        if self.host not in {"127.0.0.1", "localhost"}:
            raise ValueError("This prototype only binds to 127.0.0.1; it must not be exposed.")
        if self.ai_provider not in AI_PROVIDERS:
            raise ValueError(f"OPSAPP_AI_PROVIDER must be one of: {', '.join(AI_PROVIDERS)}.")
        if self.ai_model not in AI_MODELS:
            raise ValueError(f"OPSAPP_AI_MODEL must be one of: {', '.join(AI_MODELS)}.")
        if self.ai_provider == "anthropic" and not self.ai_api_key:
            raise ValueError("OPSAPP_AI_PROVIDER=anthropic needs OPSAPP_AI_API_KEY to be set.")
        if not (Decimal("0") <= self.ai_budget_usd <= Decimal("100")):
            raise ValueError("OPSAPP_AI_BUDGET_USD must be between 0 and 100.")
        if not (1 <= self.ai_timeout_seconds <= 120):
            raise ValueError("OPSAPP_AI_TIMEOUT_SECONDS must be between 1 and 120.")
        if self.max_attempts < 1 or self.max_attempts > 5:
            raise ValueError("OPSAPP_MAX_ATTEMPTS must be between 1 and 5.")


def _decimal(value: str) -> Decimal:
    try:
        return Decimal(value)
    except InvalidOperation:
        raise ValueError(f"Not a number: {value!r}") from None


def load_settings(env_file: Path | None = None) -> Settings:
    file_values = _read_dotenv(env_file or Path(".env"))

    def get(name: str, default: str) -> str:
        return os.environ.get(name, file_values.get(name, default))

    secret = get("OPSAPP_SESSION_SECRET", "")
    if not secret or secret == "change-me-to-a-random-string":  # noqa: S105 - placeholder
        # Ephemeral secret: sessions reset when the server restarts. Fine for local demos.
        secret = secrets.token_urlsafe(32)
    return Settings(
        database_path=Path(get("OPSAPP_DATABASE_PATH", "data/opsapp.sqlite")),
        session_secret=secret,
        port=int(get("OPSAPP_PORT", "8000")),
        ai_provider=get("OPSAPP_AI_PROVIDER", "mock"),
        ai_model=get("OPSAPP_AI_MODEL", "claude-haiku-4-5"),
        ai_api_key=get("OPSAPP_AI_API_KEY", ""),
        ai_budget_usd=_decimal(get("OPSAPP_AI_BUDGET_USD", "5.00")),
        ai_timeout_seconds=float(get("OPSAPP_AI_TIMEOUT_SECONDS", "30")),
        dispatch_poll_seconds=float(get("OPSAPP_DISPATCH_POLL_SECONDS", "2")),
        action_timeout_seconds=float(get("OPSAPP_ACTION_TIMEOUT_SECONDS", "10")),
        max_attempts=int(get("OPSAPP_MAX_ATTEMPTS", "3")),
    )
