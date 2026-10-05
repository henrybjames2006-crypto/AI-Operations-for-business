"""Wires settings into concrete services. One place to swap adapters later."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from .adapters.ports import ActionAdapter
from .adapters.simulated import make_simulated_adapters
from .ai.mock import MockExtractor
from .clock import Clock
from .config import Settings
from .dispatch.worker import Dispatcher
from .persistence.db import make_engine, make_read_session_factory, make_session_factory
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
    service = WorkflowService(
        sf, clock, MockExtractor(), max_request_chars=settings.max_request_chars
    )
    adapters: dict[str, ActionAdapter] = dict(make_simulated_adapters(sf, clock))
    return Container(
        settings, engine, sf, make_read_session_factory(engine), clock, service, adapters
    )
