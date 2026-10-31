"""Demo clock: lets the demo jump "2 days later" without waiting.

All app logic reads time through now(). MongoDB's TTL monitor still uses the real
wall clock, so retrieval also filters out memories whose expires_at has passed
on the demo clock.
"""
from datetime import datetime, timedelta, timezone

_offset = timedelta(0)


def now() -> datetime:
    return datetime.now(timezone.utc) + _offset


def offset_days() -> float:
    return _offset.total_seconds() / 86400


def shift(days: float) -> None:
    global _offset
    _offset += timedelta(days=days)


def reset() -> None:
    global _offset
    _offset = timedelta(0)
