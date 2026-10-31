"""Repeat-call guard: memory changes the *action*, not just the text.

Caregiver-set constraint memories carry a structured `rule`:
  {"kind": "quiet_hours", "start": "09:00", "end": "15:00"}   hard block
  {"kind": "max_calls_per_day", "value": 3}                    hard block
  {"kind": "min_gap_minutes", "value": 60}                     soft, "Call anyway?"
Without a min_gap rule the default gap from settings applies.
"""
from datetime import datetime, time, timedelta

from bson import ObjectId

from .. import clock, db
from .. import memory_store as ms
from ..config import settings


def _hm(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


def _fmt_time(dt: datetime) -> str:
    return dt.astimezone(settings.tz).strftime("%I:%M %p").lstrip("0")


def _in_window(now_t: time, start: time, end: time) -> bool:
    return start <= now_t < end if start <= end else (now_t >= start or now_t < end)


async def check(owner_id: ObjectId, contact: dict) -> dict:
    now = clock.now()
    local = now.astimezone(settings.tz)
    cid = contact["_id"]
    name = contact["name"]

    rules = [c async for c in db.memories.find({
        "owner_id": owner_id, "type": "constraint", "status": "active", "rule": {"$exists": True},
        "$or": [{"contact_id": cid}, {"contact_id": None}]})]
    rules = [r for r in rules if ms.is_live(r, now)]

    last = await db.calls.find_one({"patient_id": owner_id, "contact_id": cid, "status": {"$ne": "cancelled"}},
                                   sort=[("started_at", -1)])
    day_start = datetime.combine(local.date(), time(0), tzinfo=settings.tz)
    today_count = await db.calls.count_documents({
        "patient_id": owner_id, "contact_id": cid, "status": {"$ne": "cancelled"}, "started_at": {"$gte": day_start}})

    # The most relevant upcoming plan, to reassure rather than just refuse.
    plan = await db.memories.find_one({
        "owner_id": owner_id, "contact_id": cid, "status": "active", "visibility": "patient",
        "type": {"$in": ["plan", "event"]}, "event_date": {"$gte": now - timedelta(hours=12)}},
        sort=[("event_date", 1)])
    patient = await db.users.find_one({"_id": owner_id}, {"name": 1})
    plan_text = ""
    if plan and ms.is_live(plan, now):
        plan_text = " " + plan["content"].replace(f" {patient['name']}", " you") + "."

    base = {"last_call_at": last["started_at"].isoformat() if last else None, "calls_today": today_count}

    for r in rules:
        rule = r["rule"]
        if rule.get("kind") == "quiet_hours" and _in_window(local.time(), _hm(rule["start"]), _hm(rule["end"])):
            return {**base, "allowed": False, "can_override": False, "constraint_id": str(r["_id"]),
                    "message": f"{name} is resting right now. You can call after "
                               f"{_fmt_time(datetime.combine(local.date(), _hm(rule['end']), tzinfo=settings.tz))}.{plan_text}",
                    "reason": r["content"]}
        if rule.get("kind") == "max_calls_per_day" and today_count >= int(rule["value"]):
            return {**base, "allowed": False, "can_override": False, "constraint_id": str(r["_id"]),
                    "message": f"You have already spoken to {name} {today_count} times today. "
                               f"Let's call {name} again tomorrow.{plan_text}",
                    "reason": r["content"]}

    gap_rule = next((r for r in rules if r["rule"].get("kind") == "min_gap_minutes"), None)
    gap = int(gap_rule["rule"]["value"]) if gap_rule else settings.default_min_gap_minutes
    if last and (now - last["started_at"]) < timedelta(minutes=gap):
        summary = f" {last['recap']}" if last.get("recap") and last.get("status") == "processed" else ""
        return {**base, "allowed": False, "can_override": True,
                "constraint_id": str(gap_rule["_id"]) if gap_rule else None,
                "message": f"You spoke to {name} at {_fmt_time(last['started_at'])}.{plan_text or summary} "
                           f"Do you still want to call?",
                "reason": gap_rule["content"] if gap_rule else f"Default: {gap} minutes between calls"}

    return {**base, "allowed": True, "can_override": True, "message": None, "reason": None}
