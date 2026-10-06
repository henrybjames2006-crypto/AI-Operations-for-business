"""Adapter port for external actions (email delivery, calendar proposals, ...).

Contract every adapter must follow:
- ``send`` is called with a stable idempotency key. It returns ``SendResult`` when the
  provider answered, or raises ``AdapterTimeout`` when no answer arrived in time. A timeout
  means the outcome is unknown: the action may or may not have happened.
- ``lookup`` asks the provider whether an operation with that key exists. It is used to
  reconcile after a timeout before anything is retried.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol


class AdapterTimeout(Exception):
    """No response within the timeout. The operation may or may not have happened."""


@dataclass(frozen=True)
class SendResult:
    status: Literal["succeeded", "failed"]
    external_ref: str | None = None
    error: str | None = None
    retryable: bool = True


@dataclass(frozen=True)
class LookupResult:
    status: Literal["found", "not_found", "unknown"]
    external_ref: str | None = None


class ActionAdapter(Protocol):
    name: str
    simulated: bool
    honors_idempotency: bool

    def send(
        self, tenant_id: str, idempotency_key: str, payload: dict[str, Any], timeout_seconds: float
    ) -> SendResult: ...

    def lookup(self, tenant_id: str, idempotency_key: str) -> LookupResult: ...
