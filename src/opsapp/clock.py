"""Injectable clock so tests and evaluations control time."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


class Clock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock(Clock):
    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("FixedClock needs a timezone-aware datetime")
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)
