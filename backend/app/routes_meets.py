"""Scheduled meets, plus the home screens for the patient and for relatives."""
from datetime import datetime, timedelta

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from . import auth, clock, db, faces
from . import memory_store as ms
from .auth import Principal
from .config import settings

router = APIRouter(prefix="/api")

JOIN_EARLY = timedelta(minutes=30)  # the patient's Join button turns on this long before the start


def parse_when(value: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(400, "scheduled_at must be an ISO date and time") from None
    return dt if dt.tzinfo else dt.replace(tzinfo=settings.tz)


async def _people(patient_id: ObjectId, ids: list[ObjectId]) -> list[dict]:
    q = {"patient_id": patient_id, "status": "active"}
    if ids:
        q["_id"] = {"$in": ids}
    return [{"id": str(c["_id"]), "name": c["name"], "relation": c.get("relation"), "photo": c.get("photo"),
             "color": c.get("color")} async for c in db.contacts.find(q, {"name": 1, "relation": 1, "photo": 1, "color": 1})]


async def meet_out(m: dict) -> dict:
    out = ms.serialize(m)
    out["everyone"] = not m.get("contact_ids")
    out["people"] = await _people(m["patient_id"], m.get("contact_ids") or [])
    return out


# ---------------------------------------------------------------- caretaker: schedule
class MeetIn(BaseModel):
    patient_id: str
    title: str
    scheduled_at: str
    duration_min: int = 30
    contact_ids: list[str] = []  # empty = every relative
    agenda: str | None = None


def _contact_ids(raw: list[str]) -> list[ObjectId]:
    ids = [auth.as_object_id(x) for x in raw]
    if any(i is None for i in ids):
        raise HTTPException(400, "unknown person in the invite list")
    return ids


@router.post("/meets")
async def create_meet(body: MeetIn, p: Principal = Depends(auth.current)):
    pid = auth.require_access(p, body.patient_id, role="family")
    if not body.title.strip():
        raise HTTPException(400, "give the meet a title")
    ids = _contact_ids(body.contact_ids)
    if ids and await db.contacts.count_documents({"_id": {"$in": ids}, "patient_id": pid, "status": "active"}) != len(ids):
        raise HTTPException(400, "someone on the invite list isn't in the family")
    meet = {"patient_id": pid, "title": body.title.strip()[:120], "scheduled_at": parse_when(body.scheduled_at),
            "duration_min": max(5, min(body.duration_min, 240)), "contact_ids": ids,
            "agenda": (body.agenda or "").strip()[:600] or None, "status": "scheduled",
            "created_by": p.display, "created_at": clock.now()}
    meet["_id"] = (await db.meets.insert_one(meet)).inserted_id
    return await meet_out(meet)


@router.get("/meets")
async def list_meets(patient_id: str, scope: str = "all", p: Principal = Depends(auth.current)):
    pid = auth.require_access(p, patient_id)
    now = clock.now()
    q: dict = {"patient_id": pid}
    if scope == "upcoming":
        q.update(status="scheduled", scheduled_at={"$gte": now - timedelta(hours=1)})
    elif scope == "past":
        q["$or"] = [{"status": {"$in": ["done", "cancelled", "started"]}}, {"scheduled_at": {"$lt": now - timedelta(hours=1)}}]
    sort = [("scheduled_at", 1 if scope == "upcoming" else -1)]
    out = []
    async for m in db.meets.find(q).sort(sort).limit(120):
        item = await meet_out(m)
        if m.get("call_id"):
            call = await db.calls.find_one({"_id": m["call_id"]}, {"summary": 1, "caretaker_note": 1, "important": 1,
                                                                   "duration_sec": 1, "contact_id": 1})
            if call:
                item["call"] = ms.serialize(call)
        out.append(item)
    return out


class MeetPatch(BaseModel):
    title: str | None = None
    scheduled_at: str | None = None
    duration_min: int | None = None
    contact_ids: list[str] | None = None
    agenda: str | None = None
    status: str | None = None


@router.patch("/meets/{mid}")
async def patch_meet(mid: str, body: MeetPatch, p: Principal = Depends(auth.current)):
    m = await db.meets.find_one({"_id": ms.oid(mid)})
    if not m:
        raise HTTPException(404, "meet not found")
    auth.require_access(p, m["patient_id"], role="family")
    sets: dict = {}
    if body.title is not None:
        sets["title"] = body.title.strip()[:120]
    if body.scheduled_at is not None:
        sets["scheduled_at"] = parse_when(body.scheduled_at)
    if body.duration_min is not None:
        sets["duration_min"] = max(5, min(body.duration_min, 240))
    if body.contact_ids is not None:
        sets["contact_ids"] = _contact_ids(body.contact_ids)
    if body.agenda is not None:
        sets["agenda"] = body.agenda.strip()[:600] or None
    if body.status in ("scheduled", "cancelled"):
        sets["status"] = body.status
    if sets:
        await db.meets.update_one({"_id": m["_id"]}, {"$set": sets})
    return await meet_out(await db.meets.find_one({"_id": m["_id"]}))


@router.delete("/meets/{mid}")
async def delete_meet(mid: str, p: Principal = Depends(auth.current)):
    m = await db.meets.find_one({"_id": ms.oid(mid)})
    if not m:
        raise HTTPException(404, "meet not found")
    auth.require_access(p, m["patient_id"], role="family")
    await db.meets.delete_one({"_id": m["_id"]})
    return {"deleted": True}


# ---------------------------------------------------------------- patient home
@router.get("/patient/home")
async def patient_home(p: Principal = Depends(auth.current)):
    if not p.is_patient_side:
        raise HTTPException(403, "this is the patient's screen")
    pid = p.patient_ids[0]
    patient = await db.users.find_one({"_id": pid})
    now = clock.now()
    upcoming = []
    async for m in db.meets.find({"patient_id": pid, "status": "scheduled",
                                  "scheduled_at": {"$gte": now - timedelta(minutes=60), "$lte": now + timedelta(days=14)}}
                                 ).sort("scheduled_at", 1).limit(12):
        item = await meet_out(m)
        starts_in = (m["scheduled_at"] - now).total_seconds()
        item["can_join"] = starts_in <= JOIN_EARLY.total_seconds()
        item["starts_in_sec"] = int(starts_in)
        upcoming.append(item)
    live = await db.calls.find_one({"patient_id": pid, "status": {"$in": ["ringing", "active"]}},
                                   {"code": 1, "status": 1, "title": 1}, sort=[("started_at", -1)])
    return {"patient": {"id": str(pid), "name": patient["name"], "full_name": patient.get("full_name")},
            "upcoming": upcoming, "live_call": ms.serialize(live) if live else None,
            "now": now.isoformat()}


# ---------------------------------------------------------------- relative home
@router.get("/relative/home")
async def relative_home(patient_id: str | None = None, p: Principal = Depends(auth.current)):
    auth.require_relative(p)
    pid = auth.as_object_id(patient_id) if patient_id else p.patient_ids[0]
    if pid not in p.contacts:
        raise HTTPException(404, "patient not found")
    me = p.contacts[pid]
    patient = await db.users.find_one({"_id": pid})
    now = clock.now()

    live = []
    async for c in db.calls.find({"patient_id": pid, "status": {"$in": ["ringing", "active"]}, "invited": me["_id"]}
                                 ).sort("started_at", -1).limit(3):
        taken = c.get("contact_id") not in (None, me["_id"])
        live.append({"call_id": str(c["_id"]), "title": c.get("title"), "status": c["status"],
                     "started_at": c["started_at"].isoformat(), "taken": taken, "mine": c.get("contact_id") == me["_id"]})

    upcoming = []
    q = {"patient_id": pid, "status": {"$in": ["scheduled", "started"]}, "scheduled_at": {"$gte": now - timedelta(hours=1)},
         "$or": [{"contact_ids": []}, {"contact_ids": me["_id"]}]}
    async for m in db.meets.find(q).sort("scheduled_at", 1).limit(10):
        upcoming.append(await meet_out(m))

    past = []
    async for c in db.calls.find({"patient_id": pid, "contact_id": me["_id"], "status": "processed"}
                                 ).sort("started_at", -1).limit(6):
        past.append({"at": c["started_at"].isoformat(), "title": c.get("title"),
                     "duration_sec": c.get("duration_sec"), "summary": faces.short(c.get("summary"), 200)})
    return {"contact": {"id": str(me["_id"]), "name": me["name"], "relation": me.get("relation"), "photo": me.get("photo")},
            "patient": {"id": str(pid), "name": patient["name"], "full_name": patient.get("full_name")},
            "live": live, "upcoming": upcoming, "past": past, "now": now.isoformat()}
