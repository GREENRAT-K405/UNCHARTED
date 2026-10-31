"""Demo clock: lets the demo jump "2 days later" without waiting.

All app logic reads time through now(). MongoDB's TTL monitor still uses the real
wall clock, so retrieval also filters out memories whose expires_at has passed
on the demo clock.

frozen() pins now() to a fixed instant for the current asyncio task only; the
seeder uses it to write six weeks of history without affecting live requests.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone

_offset = timedelta(0)
_frozen: ContextVar[datetime | None] = ContextVar("frozen_now", default=None)


def now() -> datetime:
    pinned = _frozen.get()
    if pinned is not None:
        return pinned
    return datetime.now(timezone.utc) + _offset


@contextmanager
def frozen(at: datetime):
    token = _frozen.set(at)
    try:
        yield
    finally:
        _frozen.reset(token)


def offset_days() -> float:
    return _offset.total_seconds() / 86400


def shift(days: float) -> None:
    global _offset
    _offset += timedelta(days=days)


def reset() -> None:
    global _offset
    _offset = timedelta(0)
