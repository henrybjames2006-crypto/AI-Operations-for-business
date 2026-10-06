"""Simulated email and calendar providers.

They store what they "did" in their own tables (``sim_operations``), in a separate database
transaction from the workflow, exactly like a third-party service would. Nothing leaves the
machine: no email is sent and no calendar event is created.

Fault injection (``sim_faults``) lets the demo and tests force each failure path:

- ``fail``: the provider answers "failed"; nothing happened.
- ``timeout_before_send``: no answer; nothing happened.
- ``drop_response``: the operation happened, then the answer was lost.
- ``status_unknown``: the operation happened, the answer was lost, and status lookups
  cannot tell (lookup returns ``unknown``).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..clock import Clock
from ..ids import new_id
from ..persistence.models import SimFault, SimOperation
from .ports import AdapterTimeout, LookupResult, SendResult

FAULT_MODES = ("fail", "timeout_before_send", "drop_response", "status_unknown")


class SimulatedAdapter:
    simulated = True

    def __init__(
        self,
        name: str,
        session_factory: sessionmaker[Session],
        clock: Clock,
        honors_idempotency: bool,
    ) -> None:
        self.name = name
        self._sf = session_factory
        self._clock = clock
        self.honors_idempotency = honors_idempotency

    def _next_fault(self, s: Session, tenant_id: str) -> str | None:
        fault = s.scalars(
            select(SimFault)
            .where(
                SimFault.tenant_id == tenant_id,
                SimFault.adapter == self.name,
                SimFault.consumed_at.is_(None),
            )
            .order_by(SimFault.created_at, SimFault.id)
            .limit(1)
        ).first()
        if fault is None:
            return None
        fault.consumed_at = self._clock.now()
        return fault.mode

    def _record(
        self,
        s: Session,
        tenant_id: str,
        key: str,
        payload: dict[str, Any],
        lookup_unknown: bool = False,
    ) -> SimOperation:
        # A provider without idempotency stores every call separately (duplicates possible).
        stored_key = key if self.honors_idempotency else f"{key}#{new_id('sim_operation')}"
        op = SimOperation(
            id=new_id("sim_operation"),
            tenant_id=tenant_id,
            adapter=self.name,
            idempotency_key=stored_key,
            payload={"key": key, "lookup_unknown": lookup_unknown, **payload},
            created_at=self._clock.now(),
        )
        s.add(op)
        return op

    def _existing(self, s: Session, tenant_id: str, key: str) -> list[SimOperation]:
        ops = s.scalars(
            select(SimOperation).where(
                SimOperation.tenant_id == tenant_id, SimOperation.adapter == self.name
            )
        ).all()
        return [op for op in ops if op.payload.get("key") == key]

    def send(
        self, tenant_id: str, idempotency_key: str, payload: dict[str, Any], timeout_seconds: float
    ) -> SendResult:
        with self._sf.begin() as s:
            fault = self._next_fault(s, tenant_id)
            if self.honors_idempotency:
                existing = self._existing(s, tenant_id, idempotency_key)
                if existing:
                    return SendResult("succeeded", external_ref=existing[0].id)
            if fault == "fail":
                return SendResult("failed", error="Simulated provider error (503).", retryable=True)
            if fault == "timeout_before_send":
                pass  # nothing recorded; fall through to raise after commit
            else:
                op = self._record(
                    s,
                    tenant_id,
                    idempotency_key,
                    payload,
                    lookup_unknown=(fault == "status_unknown"),
                )
                ref = op.id
        if fault in ("timeout_before_send", "drop_response", "status_unknown"):
            raise AdapterTimeout(
                f"No response from simulated {self.name} within {timeout_seconds:g}s."
            )
        return SendResult("succeeded", external_ref=ref)

    def lookup(self, tenant_id: str, idempotency_key: str) -> LookupResult:
        with self._sf.begin() as s:
            existing = self._existing(s, tenant_id, idempotency_key)
            if any(op.payload.get("lookup_unknown") for op in existing):
                return LookupResult("unknown")
            if existing:
                return LookupResult("found", external_ref=existing[0].id)
            return LookupResult("not_found")


def make_simulated_adapters(
    session_factory: sessionmaker[Session], clock: Clock
) -> dict[str, SimulatedAdapter]:
    """Email honors idempotency keys; the calendar does not, so reconciliation matters."""
    return {
        "sim_email": SimulatedAdapter("sim_email", session_factory, clock, honors_idempotency=True),
        "sim_calendar": SimulatedAdapter(
            "sim_calendar", session_factory, clock, honors_idempotency=False
        ),
    }
