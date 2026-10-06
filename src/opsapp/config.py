"""Configuration from environment variables (and an optional untracked .env file).

Secrets never live in source code. For local development they come from the environment
or ``.env``. A hosted deployment would replace ``load_settings`` with a managed secret
store; nothing else reads secrets directly.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path

SECRET_ENV_NAMES = ("OPSAPP_SESSION_SECRET", "OPSAPP_AI_API_KEY")


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
    session_secret: str
    host: str = "127.0.0.1"
    port: int = 8000
    ai_provider: str = "mock"
    dispatch_poll_seconds: float = 2.0
    action_timeout_seconds: float = 10.0
    max_attempts: int = 3
    max_request_chars: int = 20_000

    def __post_init__(self) -> None:
        if self.host not in {"127.0.0.1", "localhost"}:
            raise ValueError("This prototype only binds to 127.0.0.1; it must not be exposed.")
        if self.ai_provider != "mock":
            raise ValueError("Checkpoint 1 supports only OPSAPP_AI_PROVIDER=mock.")
        if self.max_attempts < 1 or self.max_attempts > 5:
            raise ValueError("OPSAPP_MAX_ATTEMPTS must be between 1 and 5.")


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
        dispatch_poll_seconds=float(get("OPSAPP_DISPATCH_POLL_SECONDS", "2")),
        action_timeout_seconds=float(get("OPSAPP_ACTION_TIMEOUT_SECONDS", "10")),
        max_attempts=int(get("OPSAPP_MAX_ATTEMPTS", "3")),
    )
