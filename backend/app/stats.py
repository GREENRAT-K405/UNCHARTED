"""Caregiver insights, computed in MongoDB with aggregation pipelines ($facet, $dateTrunc, $lookup)."""
from datetime import timedelta

from fastapi import APIRouter, Depends

from . import auth, clock, db
from . import memory_store as ms
from .auth import Principal
from .config import settings

router = APIRouter(prefix="/api")


@router.get("/patients/{pid}/stats")
async def stats(pid: str, days: int = 14, p: Principal = Depends(auth.current)):
    patient_id = auth.require_access(p, pid)
    days = max(1, min(days, 90))
    now = clock.now()
    since = now - timedelta(days=days)
    prev_since = since - timedelta(days=days)
    tz = settings.timezone

    call_facets = await db.calls.aggregate([
        {"$match": {"patient_id": patient_id, "started_at": {"$gte": prev_since, "$lte": now}}},
        {"$facet": {
            "per_day": [
                {"$match": {"started_at": {"$gte": since}}},
                {"$group": {"_id": {"day": {"$dateTrunc": {"date": "$started_at", "unit": "day", "timezone": tz}},
                                    "blocked": {"$eq": ["$status", "blocked"]}},
                            "count": {"$sum": 1}, "minutes": {"$sum": {"$divide": [{"$ifNull": ["$duration_sec", 0]}, 60]}}}},
                {"$sort": {"_id.day": 1}},
            ],
            "by_contact": [
                {"$match": {"started_at": {"$gte": since}}},
                {"$group": {"_id": "$contact_id",
                            "calls": {"$sum": {"$cond": [{"$eq": ["$status", "blocked"]}, 0, 1]}},
                            "blocked": {"$sum": {"$cond": [{"$eq": ["$status", "blocked"]}, 1, 0]}},
                            "minutes": {"$sum": {"$divide": [{"$ifNull": ["$duration_sec", 0]}, 60]}},
                            "last_call": {"$max": "$started_at"}}},
                {"$lookup": {"from": "contacts", "localField": "_id", "foreignField": "_id", "as": "contact",
                             "pipeline": [{"$project": {"name": 1, "relation": 1, "color": 1, "photo": 1}}]}},
                {"$unwind": "$contact"},
                {"$sort": {"calls": -1}},
            ],
            "by_hour": [
                {"$match": {"started_at": {"$gte": since}}},
                {"$group": {"_id": {"hour": {"$hour": {"date": "$started_at", "timezone": tz}},
                                    "blocked": {"$eq": ["$status", "blocked"]}},
                            "count": {"$sum": 1}}},
            ],
            "totals": [
                {"$group": {"_id": {"$gte": ["$started_at", since]},
                            "calls": {"$sum": {"$cond": [{"$eq": ["$status", "blocked"]}, 0, 1]}},
                            "blocked": {"$sum": {"$cond": [{"$eq": ["$status", "blocked"]}, 1, 0]}},
                            "overrides": {"$sum": {"$cond": [{"$and": [{"$ne": ["$status", "blocked"]},
                                                                       {"$ne": [{"$ifNull": ["$guard", None]}, None]}]}, 1, 0]}},
                            "minutes": {"$sum": {"$divide": [{"$ifNull": ["$duration_sec", 0]}, 60]}},
                            "people": {"$addToSet": {"$cond": [{"$eq": ["$status", "blocked"]}, "$$REMOVE", "$contact_id"]}}}},
                {"$project": {"calls": 1, "blocked": 1, "overrides": 1, "minutes": 1, "people": {"$size": "$people"}}},
            ],
        }},
    ]).to_list(1)
    cf = call_facets[0]

    mem_facets = await db.memories.aggregate([
        {"$match": {"owner_id": patient_id}},
        {"$facet": {
            "by_type": [{"$match": {"status": "active"}}, {"$group": {"_id": "$type", "count": {"$sum": 1}}},
                        {"$sort": {"count": -1}}],
            "by_status": [{"$group": {"_id": "$status", "count": {"$sum": 1}}}],
            "added": [{"$match": {"created_at": {"$gte": since}, "source.kind": "call"}}, {"$count": "n"}],
            "upcoming": [
                {"$match": {"status": "active", "type": {"$in": ["event", "plan"]}, "event_date": {"$gte": now - timedelta(days=1)}}},
                {"$sort": {"event_date": 1}}, {"$limit": 8},
                {"$lookup": {"from": "contacts", "localField": "contact_id", "foreignField": "_id", "as": "contact",
                             "pipeline": [{"$project": {"name": 1, "color": 1}}]}},
                {"$project": {"content": 1, "type": 1, "event_date": 1, "expires_at": 1, "contact": {"$first": "$contact"}}},
            ],
            # The same mistaken belief coming back again and again is worth a caregiver's attention.
            "confusions": [
                {"$match": {"source.origin": "patient"}},
                {"$group": {"_id": "$content", "times": {"$sum": 1}, "last": {"$max": "$created_at"},
                            "contact_id": {"$first": "$contact_id"}, "statuses": {"$addToSet": "$status"}}},
                {"$sort": {"times": -1, "last": -1}}, {"$limit": 6},
            ],
        }},
    ]).to_list(1)
    mf = mem_facets[0]

    activity = await db.memory_events.aggregate([
        {"$match": {"owner_id": patient_id, "actor": {"$nin": ["agent"]}}},
        {"$sort": {"ts": -1}}, {"$limit": 12},
        {"$lookup": {"from": "memories", "localField": "memory_id", "foreignField": "_id", "as": "memory",
                     "pipeline": [{"$project": {"content": 1, "type": 1}}]}},
        {"$project": {"action": 1, "actor": 1, "ts": 1, "memory": {"$first": "$memory"}}},
    ]).to_list(12)

    cur = next((t for t in cf["totals"] if t["_id"] is True), {})
    prev = next((t for t in cf["totals"] if t["_id"] is False), {})
    pending = next((s["count"] for s in mf["by_status"] if s["_id"] == "pending_review"), 0)
    devices = [ms.serialize({k: v for k, v in d.items() if k != "firebase_uid"})
               async for d in db.devices.find({"patient_id": patient_id, "revoked": {"$ne": True}})]

    # Fill every day in the window so charts have a continuous axis.
    per_day: dict[str, dict] = {}
    start_day = since.astimezone(settings.tz).date()
    for i in range(days + 1):
        d = (start_day + timedelta(days=i)).isoformat()
        per_day[d] = {"day": d, "calls": 0, "blocked": 0, "minutes": 0.0}
    for row in cf["per_day"]:
        d = row["_id"]["day"].astimezone(settings.tz).date().isoformat()
        if d in per_day:
            per_day[d]["blocked" if row["_id"]["blocked"] else "calls"] += row["count"]
            per_day[d]["minutes"] += round(row["minutes"], 1)
    by_hour = [{"hour": h, "calls": 0, "blocked": 0} for h in range(24)]
    for row in cf["by_hour"]:
        by_hour[row["_id"]["hour"]]["blocked" if row["_id"]["blocked"] else "calls"] += row["count"]

    return ms.serialize({
        "days": days,
        "totals": {"calls": cur.get("calls", 0), "prev_calls": prev.get("calls", 0),
                   "blocked": cur.get("blocked", 0), "prev_blocked": prev.get("blocked", 0),
                   "overrides": cur.get("overrides", 0), "minutes": round(cur.get("minutes", 0)),
                   "people": cur.get("people", 0), "memories_added": (mf["added"][0]["n"] if mf["added"] else 0),
                   "pending_review": pending,
                   "active_memories": sum(t["count"] for t in mf["by_type"])},
        "per_day": list(per_day.values()),
        "by_contact": [{"contact_id": r["_id"], "name": r["contact"]["name"], "relation": r["contact"]["relation"],
                        "color": r["contact"].get("color"), "calls": r["calls"], "blocked": r["blocked"],
                        "minutes": round(r["minutes"]), "last_call": r["last_call"]} for r in cf["by_contact"]],
        "by_hour": by_hour,
        "memories_by_type": [{"type": r["_id"], "count": r["count"]} for r in mf["by_type"]],
        "upcoming": mf["upcoming"],
        "confusions": [{"content": r["_id"], "times": r["times"], "last": r["last"], "contact_id": r["contact_id"],
                        "statuses": r["statuses"]} for r in mf["confusions"]],
        "activity": activity,
        "devices": devices,
    })
