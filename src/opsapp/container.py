"""Wires settings into concrete services. One place to swap adapters later."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from .adapters.ports import ActionAdapter
from .adapters.simulated import make_simulated_adapters
from .ai.fallback import FallbackExtractor
from .ai.mock import MockExtractor
from .ai.ports import Extractor
from .auth.service import AuthService
from .clock import Clock
from .config import Settings
from .dispatch.worker import Dispatcher
from .persistence.db import make_engine, make_read_session_factory, make_session_factory
from .persistence.models import AIUsage
from .workflow.firm import FirmService
from .workflow.service import WorkflowService


@dataclass
class Container:
    settings: Settings
    engine: Engine
    sf: sessionmaker[Session]
    read_sf: sessionmaker[Session]
    clock: Clock
    service: WorkflowService
    adapters: dict[str, ActionAdapter]
    auth: AuthService
    firm: FirmService

    def dispatcher(
        self, worker_id: str = "dispatcher-1", backoff_base_seconds: float = 2.0
    ) -> Dispatcher:
        return Dispatcher(
            self.sf,
            self.clock,
            self.adapters,
            max_attempts=self.settings.max_attempts,
            timeout_seconds=self.settings.action_timeout_seconds,
            worker_id=worker_id,
            backoff_base_seconds=backoff_base_seconds,
        )


def build(
    settings: Settings,
    clock: Clock | None = None,
    *,
    memory: bool = False,
    engine: Engine | None = None,
) -> Container:
    clock = clock or Clock()
    engine = engine or make_engine(settings.database_path, memory=memory)
    sf = make_session_factory(engine)
    read_sf = make_read_session_factory(engine)
    service = WorkflowService(
        sf, clock, make_extractor(settings, read_sf), max_request_chars=settings.max_request_chars
    )
    adapters: dict[str, ActionAdapter] = dict(make_simulated_adapters(sf, clock))
    return Container(
        settings,
        engine,
        sf,
        read_sf,
        clock,
        service,
        adapters,
        AuthService(sf, clock),
        FirmService(service),
    )


def ai_spent_usd(read_sf: sessionmaker[Session]) -> Decimal:
    """Estimated spend on paid AI calls so far, from this database's usage records."""
    with read_sf() as s:
        costs = s.scalars(select(AIUsage.est_cost_usd).where(AIUsage.provider != "mock"))
        return sum(costs, Decimal("0"))


def make_extractor(settings: Settings, read_sf: sessionmaker[Session]) -> Extractor:
    if settings.ai_provider == "mock":
        return MockExtractor()
    from .ai.claude import ClaudeExtractor, make_client

    client = make_client(settings.ai_api_key, settings.ai_timeout_seconds)
    return FallbackExtractor(
        ClaudeExtractor(client, settings.ai_model, settings.ai_timeout_seconds),
        MockExtractor(),
        lambda: ai_spent_usd(read_sf),
        settings.ai_budget_usd,
    )
