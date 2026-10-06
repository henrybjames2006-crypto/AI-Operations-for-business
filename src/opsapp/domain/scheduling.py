"""Deterministic schedule proposals.

These are proposals for the customer to choose from, computed from the requested timeframe.
They are NOT checked against any real calendar or technician availability in Checkpoint 1.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

SLOT_START = time(9, 0)
SLOTS = 3


def _business_days_from(start: date, count: int) -> list[date]:
    days: list[date] = []
    d = start
    while len(days) < count:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def window_start(now: datetime, timeframe: str | None) -> tuple[date, str]:
    """Return the first candidate day and a plain explanation of how it was chosen."""
    today = now.date()
    if timeframe == "next_week":
        monday = today + timedelta(days=7 - today.weekday())
        return monday, "Customer asked for next week: first three business days of next week."
    if timeframe == "this_week":
        start = today + timedelta(days=1)
        return start, "Customer asked for this week: next business days from tomorrow."
    if timeframe == "tomorrow":
        return today + timedelta(days=1), "Customer asked for tomorrow: tomorrow onward."
    if timeframe == "asap":
        return today + timedelta(days=1), "Customer asked for as soon as possible."
    if timeframe and timeframe.startswith("date:"):
        requested = date.fromisoformat(timeframe[5:])
        if requested > today:
            return requested, f"Customer asked for {requested.isoformat()}: that day onward."
    return today + timedelta(days=2), (
        "No timeframe stated: default proposal starts two days from today."
    )


def propose_slots(now: datetime, timeframe: str | None, tz_name: str) -> tuple[list[str], str]:
    tz = ZoneInfo(tz_name)
    local_now = now.astimezone(tz)
    start, why = window_start(local_now, timeframe)
    days = _business_days_from(start, SLOTS)
    slots = [datetime.combine(d, SLOT_START, tzinfo=tz).isoformat() for d in days]
    return slots, why
