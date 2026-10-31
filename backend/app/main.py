import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import clock, db, embeddings, llm
from . import memory_store as ms
from .agent import brief as brief_mod
from .agent import guard, pipeline
from .config import settings
from .realtime import hub
from .seed import seed

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("pehchaan")

_tasks: set[asyncio.Task] = set()
_brief_tasks: dict[str, asyncio.Task] = {}
_off_record: dict[str, bool] = {}


def spawn(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _tasks.add(task)

    def done(t: asyncio.Task):
        _tasks.discard(t)
        if not t.cancelled() and t.exception():
            log.error("background task failed", exc_info=t.exception())

    task.add_done_callback(done)
    return task


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.ensure_indexes()
    if await db.users.count_documents({"role": "patient"}) == 0:
        log.info("seeding demo data: %s", await seed())
    hub.refresh_brief = _refresh_brief
    await hub.start()
    log.info("ready: llm=%s embeddings=%s capabilities=%s", llm.status["mode"], embeddings.MODE, db.capabilities)
    yield
    await hub.stop()


app = FastAPI(title="Pehchaan", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ---------------------------------------------------------------- helpers
async def get_or_404(coll, id_: str, what: str) -> dict:
    doc = await coll.find_one({"_id": ms.oid(id_)}) if ms.oid(id_) else None
    if not doc:
        raise HTTPException(404, f"{what} not found")
    return doc


def brief_payload(b: dict) -> dict:
    return {"brief_id": str(b["_id"]), "card": b["card"], "mode": b.get("mode"), "created_at": b["created_at"].isoformat()}


async def _refresh_brief(call: dict) -> dict:
    contact = await db.contacts.find_one({"_id": call["contact_id"]})
    return brief_payload(await brief_mod.build_brief(call["patient_id"], contact, call["_id"]))


# ---------------------------------------------------------------- system
@app.get("/api/health")
async def health():
    return {"ok": True, "llm": llm.status, "embeddings": embeddings.MODE, "capabilities": db.capabilities,
            "clock": {"now": clock.now().isoformat(), "offset_days": clock.offset_days()}}


class ClockIn(BaseModel):
    shift_days: float | None = None
    reset: bool = False


@app.get("/api/clock")
async def get_clock():
    return {"now": clock.now().isoformat(), "offset_days": clock.offset_days(), "timezone": settings.timezone}


@app.post("/api/clock")
async def set_clock(body: ClockIn):
    if body.reset:
        clock.reset()
    elif body.shift_days:
        clock.shift(body.shift_days)
    return await get_clock()


@app.post("/api/demo/reset")
async def demo_reset():
    _off_record.clear()
    return await seed(reset=True)


class ExpiringIn(BaseModel):
    patient_id: str
    contact_id: str | None = None
    seconds: int = 90
    content: str | None = None


@app.post("/api/demo/expiring")
async def demo_expiring(body: ExpiringIn):
    """Seed a memory that MongoDB's TTL monitor will delete shortly (it runs about every 60 s)."""
    patient = await get_or_404(db.users, body.patient_id, "patient")
    contact = await db.contacts.find_one({"_id": ms.oid(body.contact_id)}) if body.contact_id else None
    real_expiry = datetime.now(timezone.utc) + timedelta(seconds=body.seconds)
    mem = await ms.insert_memory(
        owner_id=patient["_id"], contact_id=contact["_id"] if contact else None, type="plan",
        content=body.content or f"{contact['name'] if contact else 'Rahul'} will drop by with groceries in a few minutes",
        source={"kind": "caregiver", "origin": "caregiver"}, trust=1.0, importance=0.6,
        event_date=clock.now(), expires_at=real_expiry, actor="caregiver")
    return ms.serialize(mem)


# ---------------------------------------------------------------- patients & contacts
@app.get("/api/patients")
async def patients():
    return [ms.serialize(p) async for p in db.users.find({"role": "patient"})]


@app.get("/api/patients/{pid}/contacts")
async def contacts(pid: str, status: str = "active"):
    q = {"patient_id": ms.oid(pid)}
    if status != "all":
        q["status"] = {"$in": status.split(",")}
    return [ms.serialize(c) async for c in db.contacts.find(q).sort([("primary_caregiver", -1), ("created_at", 1)])]


class ContactIn(BaseModel):
    patient_id: str
    name: str
    relation: str
    relation_detail: str | None = None
    nicknames: list[str] = []
    phone: str | None = None
    photo: str | None = None  # URL or small data: URL
    color: str | None = None
    notes: list[str] = []


@app.post("/api/contacts")
async def add_contact(body: ContactIn):
    patient = await get_or_404(db.users, body.patient_id, "patient")
    contact = {
        "patient_id": patient["_id"], "name": body.name.strip(), "nicknames": body.nicknames,
        "relation": body.relation.strip().lower(), "relation_detail": body.relation_detail, "phone": body.phone,
        "photo": body.photo, "color": body.color or "#55606e", "status": "active", "primary_caregiver": False,
        "created_at": clock.now(),
    }
    contact["_id"] = (await db.contacts.insert_one(contact)).inserted_id
    src = {"kind": "caregiver", "origin": "caregiver"}
    rel = f"{contact['name']} is {patient['name']}'s {contact['relation']}" + (
        f" ({body.relation_detail})" if body.relation_detail else "")
    await ms.insert_memory(owner_id=patient["_id"], contact_id=contact["_id"], type="relationship", content=rel,
                           source=src, trust=1.0, importance=1.0, actor="caregiver")
    for note in [n.strip() for n in body.notes if n.strip()]:
        await ms.insert_memory(owner_id=patient["_id"], contact_id=contact["_id"], type="fact", content=note,
                               source=src, trust=1.0, importance=0.6, actor="caregiver")
    return {**ms.serialize(contact), "invite_url": f"{settings.public_base_url}/caregiver"}


class ContactRequestIn(BaseModel):
    patient_id: str
    name: str


@app.post("/api/contacts/request")
async def request_contact(body: ContactRequestIn):
    """Patient-initiated new contact: pending until the caregiver approves (scam protection)."""
    patient = await get_or_404(db.users, body.patient_id, "patient")
    contact = {"patient_id": patient["_id"], "name": body.name.strip().title(), "nicknames": [], "relation": "unknown",
               "status": "pending", "photo": None, "color": "#8a8f98", "primary_caregiver": False,
               "created_at": clock.now(), "requested_by": "patient"}
    contact["_id"] = (await db.contacts.insert_one(contact)).inserted_id
    await hub.to_caregivers(str(patient["_id"]), {"type": "contact_request", "contact": ms.serialize(contact)})
    return ms.serialize(contact)


class ContactPatch(BaseModel):
    name: str | None = None
    relation: str | None = None
    nicknames: list[str] | None = None
    photo: str | None = None
    status: str | None = None
    phone: str | None = None


@app.patch("/api/contacts/{cid}")
async def patch_contact(cid: str, body: ContactPatch):
    contact = await get_or_404(db.contacts, cid, "contact")
    fields = body.model_dump(exclude_none=True)
    if fields:
        await db.contacts.update_one({"_id": contact["_id"]}, {"$set": fields})
    if body.relation and contact.get("status") == "pending":
        patient = await db.users.find_one({"_id": contact["patient_id"]})
        await ms.insert_memory(owner_id=patient["_id"], contact_id=contact["_id"], type="relationship",
                               content=f"{contact['name']} is {patient['name']}'s {body.relation}",
                               source={"kind": "caregiver", "origin": "caregiver"}, trust=1.0, importance=1.0,
                               actor="caregiver")
    return ms.serialize(await db.contacts.find_one({"_id": contact["_id"]}))


@app.delete("/api/contacts/{cid}")
async def delete_contact(cid: str, mode: str = "delete"):
    contact = await get_or_404(db.contacts, cid, "contact")
    if mode == "archive":
        await db.contacts.update_one({"_id": contact["_id"]}, {"$set": {"status": "archived"}})
        return {"archived": True}
    # Cascade: forgetting a person forgets every memory about them (event log keeps ids only).
    removed = 0
    async for m in db.memories.find({"contact_id": contact["_id"]}):
        await ms.forget(m, "caregiver")
        removed += 1
    await db.contacts.delete_one({"_id": contact["_id"]})
    return {"deleted": True, "memories_removed": removed}


# ---------------------------------------------------------------- calls
class CallIn(BaseModel):
    patient_id: str
    contact_id: str
    force: bool = False
    emergency: bool = False


@app.post("/api/calls")
async def start_call(body: CallIn):
    patient = await get_or_404(db.users, body.patient_id, "patient")
    contact = await get_or_404(db.contacts, body.contact_id, "contact")
    if contact.get("status") != "active":
        return {"allowed": False, "can_override": False,
                "message": f"{contact['name']} is not in your family list yet. Ask Rahul first."}

    check = await guard.check(patient["_id"], contact)
    if not body.emergency and not check["allowed"] and not (body.force and check["can_override"]):
        return check

    now = clock.now()
    call = {"patient_id": patient["_id"], "contact_id": contact["_id"], "status": "ringing", "started_at": now,
            "initiated_by": "patient", "emergency": body.emergency, "guard": check if not check["allowed"] else None,
            "off_record": False}
    call["_id"] = (await db.calls.insert_one(call)).inserted_id
    await hub.call_changed(call["_id"])
    _brief_tasks[str(call["_id"])] = spawn(brief_mod.build_brief(patient["_id"], contact, call["_id"]))
    return {"allowed": True, "call_id": str(call["_id"]),
            "join_url": f"{settings.public_base_url}/join/{call['_id']}", "overrode": not check["allowed"]}


@app.get("/api/calls")
async def list_calls(patient_id: str, limit: int = 30):
    names = {c["_id"]: c["name"] async for c in db.contacts.find({"patient_id": ms.oid(patient_id)})}
    out = []
    async for c in db.calls.find({"patient_id": ms.oid(patient_id)}).sort("started_at", -1).limit(limit):
        out.append({**ms.serialize(c), "contact_name": names.get(c["contact_id"], "?")})
    return out


@app.get("/api/calls/{call_id}")
async def get_call(call_id: str):
    call = await get_or_404(db.calls, call_id, "call")
    contact = await db.contacts.find_one({"_id": call["contact_id"]})
    patient = await db.users.find_one({"_id": call["patient_id"]})
    return {**ms.serialize(call), "contact": ms.serialize(contact), "patient": ms.serialize(patient),
            "join_url": f"{settings.public_base_url}/join/{call['_id']}"}


@app.get("/api/calls/{call_id}/brief")
async def get_brief(call_id: str):
    call = await get_or_404(db.calls, call_id, "call")
    task = _brief_tasks.get(call_id)
    if task and not task.done():
        await asyncio.shield(task)
    latest = await db.briefs.find_one({"call_id": call["_id"]}, sort=[("created_at", -1)])
    if not latest:
        contact = await db.contacts.find_one({"_id": call["contact_id"]})
        latest = await brief_mod.build_brief(call["patient_id"], contact, call["_id"])
    return brief_payload(latest)


@app.get("/api/calls/{call_id}/receiver")
async def receiver_info(call_id: str):
    call = await get_or_404(db.calls, call_id, "call")
    contact = await db.contacts.find_one({"_id": call["contact_id"]})
    patient = await db.users.find_one({"_id": call["patient_id"]})
    return {"call": ms.serialize(call), "contact": ms.serialize(contact), "patient": ms.serialize(patient),
            "notes": await brief_mod.receiver_note(patient["_id"], contact)}


@app.get("/api/calls/{call_id}/transcript")
async def get_transcript(call_id: str):
    call = await get_or_404(db.calls, call_id, "call")
    return [ms.serialize(c) async for c in db.transcript_chunks.find({"call_id": call["_id"]}).sort("ts", 1)]


class ChunkIn(BaseModel):
    speaker: str
    text: str


async def save_chunk(call_id: str, speaker: str, text: str) -> dict | None:
    text = text.strip()
    if not text or speaker not in ("patient", "contact"):
        return None
    msg = {"type": "transcript", "speaker": speaker, "text": text, "ts": clock.now().isoformat()}
    if _off_record.get(call_id):
        await hub.to_room(call_id, {**msg, "off_record": True})
        return None  # never stored, never remembered
    call_oid = ms.oid(call_id)
    await db.transcript_chunks.insert_one({"call_id": call_oid, "speaker": speaker, "text": text,
                                           "ts": clock.now(), "created_at": datetime.now(timezone.utc)})
    await hub.to_room(call_id, msg)
    call = await db.calls.find_one({"_id": call_oid}, {"patient_id": 1})
    if call:
        await hub.to_caregivers(str(call["patient_id"]), {**msg, "call_id": call_id})
    return msg


@app.post("/api/calls/{call_id}/transcript")
async def post_chunk(call_id: str, body: ChunkIn):
    await get_or_404(db.calls, call_id, "call")
    return {"saved": await save_chunk(call_id, body.speaker, body.text) is not None}


@app.post("/api/calls/{call_id}/end")
async def end_call(call_id: str):
    call = await get_or_404(db.calls, call_id, "call")
    if call["status"] in ("ringing", "active"):
        now = clock.now()
        await db.calls.update_one({"_id": call["_id"]}, {"$set": {
            "status": "ended", "ended_at": now,
            "duration_sec": int((now - call["started_at"]).total_seconds())}})
        await hub.to_room(call_id, {"type": "hangup"})
        await hub.call_changed(call["_id"])
        spawn(_process(call["_id"]))
    return {"status": "ended"}


async def _process(call_oid):
    try:
        await pipeline.process_call(call_oid)
    except Exception:
        log.exception("memory agent failed")
        await db.calls.update_one({"_id": call_oid}, {"$set": {"status": "processed", "agent": {"error": True}}})
    await hub.call_changed(call_oid)
    call = await db.calls.find_one({"_id": call_oid})
    await hub.to_room(str(call_oid), {"type": "processed", "recap": call.get("recap")})


@app.post("/api/calls/{call_id}/reprocess")
async def reprocess(call_id: str):
    call = await get_or_404(db.calls, call_id, "call")
    return await pipeline.process_call(call["_id"])


# ---------------------------------------------------------------- memories
@app.get("/api/memories")
async def list_memories(patient_id: str, contact_id: str | None = None, status: str | None = None,
                        type: str | None = None, visibility: str | None = None, q: str | None = None):
    owner = ms.oid(patient_id)
    if q:
        vec = await embeddings.embed_one(q, "query")
        statuses = tuple(status.split(",")) if status else ("active", "pending_review", "superseded", "rejected")
        hits = await ms.search(owner, ms.oid(contact_id) if contact_id else None, vec, limit=20, statuses=statuses)
        return [{**ms.serialize(d), "score": round(s, 3)} for d, s in hits]
    query: dict = {"owner_id": owner}
    if contact_id:
        query["contact_id"] = None if contact_id == "none" else ms.oid(contact_id)
    if status:
        query["status"] = {"$in": status.split(",")}
    if type:
        query["type"] = {"$in": type.split(",")}
    if visibility:
        query["visibility"] = {"$in": visibility.split(",")}
    now = clock.now()
    return [{**ms.serialize(d), "expired_on_demo_clock": not ms.is_live(d, now)}
            async for d in db.memories.find(query, {"embedding": 0}).sort("updated_at", -1)]


@app.get("/api/memories/{mid}")
async def memory_detail(mid: str):
    mem = await get_or_404(db.memories, mid, "memory")
    chain_back, cur = [], mem
    while cur.get("supersedes"):
        cur = await db.memories.find_one({"_id": cur["supersedes"]}, {"embedding": 0})
        if not cur:
            break
        chain_back.append(cur)
    chain_fwd, cur = [], mem
    while cur.get("superseded_by"):
        cur = await db.memories.find_one({"_id": cur["superseded_by"]}, {"embedding": 0})
        if not cur:
            break
        chain_fwd.append(cur)
    conflict = await db.memories.find_one({"_id": mem["conflicts_with"]}, {"embedding": 0}) if mem.get("conflicts_with") else None
    events = [e async for e in db.memory_events.find({"memory_id": mem["_id"]}).sort("ts", 1)]
    used_in = []
    async for b in db.briefs.find({"items.memory_id": mem["_id"]}).sort("created_at", -1).limit(5):
        item = next(i for i in b["items"] if i["memory_id"] == mem["_id"])
        used_in.append({"brief_id": str(b["_id"]), "created_at": b["created_at"].isoformat(), "item": ms.serialize(item)})
    source_call = None
    if mem.get("source", {}).get("call_id"):
        c = await db.calls.find_one({"_id": mem["source"]["call_id"]})
        if c:
            source_call = {"id": str(c["_id"]), "started_at": c["started_at"].isoformat(), "summary": c.get("summary")}
    return {"memory": ms.serialize(mem), "supersedes_chain": ms.serialize(chain_back),
            "superseded_by_chain": ms.serialize(chain_fwd), "conflicts_with": ms.serialize(conflict),
            "events": ms.serialize(events), "used_in_briefs": used_in, "source_call": source_call,
            "expired_on_demo_clock": not ms.is_live(mem)}


class MemoryIn(BaseModel):
    patient_id: str
    contact_id: str | None = None
    type: str
    content: str
    visibility: str = "patient"
    importance: float = 0.6
    rule: dict | None = None
    event_date: str | None = None
    expires_in_days: float | None = None


@app.post("/api/memories")
async def add_memory(body: MemoryIn):
    if body.type not in ms.MEMORY_TYPES:
        raise HTTPException(400, "unknown memory type")
    patient = await get_or_404(db.users, body.patient_id, "patient")
    event_date = pipeline._parse_date(body.event_date)
    expires_at = None
    if body.expires_in_days:
        expires_at = clock.now() + timedelta(days=body.expires_in_days)
    elif event_date and body.type in ("event", "plan"):
        expires_at = ms.expiry_for(event_date, 2)
    visibility = body.visibility
    if body.type == "sensitive":
        visibility = "never_surface"
    elif body.type == "constraint":
        visibility = "caregiver_only"
    mem = await ms.insert_memory(
        owner_id=patient["_id"], contact_id=ms.oid(body.contact_id) if body.contact_id else None, type=body.type,
        content=body.content.strip(), source={"kind": "caregiver", "origin": "caregiver"}, trust=1.0,
        importance=body.importance, visibility=visibility, rule=body.rule, event_date=event_date,
        expires_at=expires_at, actor="caregiver")
    return ms.serialize(mem)


class MemoryPatch(BaseModel):
    content: str | None = None
    visibility: str | None = None
    importance: float | None = None
    action: str | None = None  # approve | reject


@app.patch("/api/memories/{mid}")
async def patch_memory(mid: str, body: MemoryPatch):
    mem = await get_or_404(db.memories, mid, "memory")
    if body.content is not None and body.content.strip() != mem["content"]:
        # A caregiver correction becomes ground truth.
        await ms.update_content(mem, body.content.strip(), "caregiver",
                                {"trust": 1.0, "source.corrected_by": "caregiver"})
    if body.visibility and body.visibility != mem.get("visibility"):
        await ms.set_fields(mem, {"visibility": body.visibility}, f"visibility:{body.visibility}", "caregiver")
    if body.importance is not None:
        await ms.set_fields(mem, {"importance": max(0.0, min(1.0, body.importance))}, "reweighted", "caregiver")
    if body.action == "approve":
        fields = {"status": "active", "trust": 1.0}
        # Once a caregiver vouches for it, it may be shown, unless they chose a visibility explicitly.
        if body.visibility:
            fields["visibility"] = body.visibility
        elif mem.get("visibility") == "caregiver_only" and mem["type"] not in ("sensitive", "constraint"):
            fields["visibility"] = "patient"
        if mem.get("conflicts_with"):
            # Caregiver confirms the new claim: it replaces the memory it conflicted with.
            target = await db.memories.find_one({"_id": mem["conflicts_with"]})
            fields["supersedes"] = mem["conflicts_with"]
            if target and target["status"] == "active":
                await ms.supersede(target, mem["_id"], actor="caregiver")
        await ms.set_fields(mem, fields, "approved", "caregiver")
    elif body.action == "reject":
        await ms.set_fields(mem, {"status": "rejected", "visibility": "caregiver_only"}, "rejected", "caregiver")
    return ms.serialize(await db.memories.find_one({"_id": mem["_id"]}, {"embedding": 0}))


@app.delete("/api/memories/{mid}")
async def delete_memory(mid: str):
    mem = await get_or_404(db.memories, mid, "memory")
    await ms.forget(mem, "caregiver")
    return {"deleted": True}


class FeedbackIn(BaseModel):
    helpful: bool
    by: str = "patient"


@app.post("/api/memories/{mid}/feedback")
async def memory_feedback(mid: str, body: FeedbackIn):
    """Learned strategy: feedback re-weights importance so the item ranks differently next brief."""
    mem = await get_or_404(db.memories, mid, "memory")
    key = "feedback.helpful" if body.helpful else "feedback.not_helpful"
    delta = 0.05 if body.helpful else -0.1
    new_imp = max(0.0, min(1.0, mem.get("importance", 0.5) + delta))
    await db.memories.update_one({"_id": mem["_id"]}, {"$inc": {key: 1}, "$set": {"importance": new_imp}})
    await ms.log_event(mem["_id"], "feedback:helpful" if body.helpful else "feedback:not_helpful", mem["owner_id"], body.by)
    return {"importance": new_imp}


# ---------------------------------------------------------------- briefs
@app.get("/api/briefs")
async def list_briefs(patient_id: str, contact_id: str | None = None, limit: int = 20):
    q = {"owner_id": ms.oid(patient_id)}
    if contact_id:
        q["contact_id"] = ms.oid(contact_id)
    return [ms.serialize(b) async for b in db.briefs.find(q).sort("created_at", -1).limit(limit)]


@app.get("/api/briefs/{bid}")
async def get_brief_doc(bid: str):
    return ms.serialize(await get_or_404(db.briefs, bid, "brief"))


@app.post("/api/contacts/{cid}/preview-brief")
async def preview_brief(cid: str):
    """Caregiver preview: what would the patient see if they called this person now?"""
    contact = await get_or_404(db.contacts, cid, "contact")
    return ms.serialize(await brief_mod.build_brief(contact["patient_id"], contact, None))


# ---------------------------------------------------------------- websockets
@app.websocket("/ws/call/{call_id}")
async def ws_call(ws: WebSocket, call_id: str, role: str = "patient"):
    await ws.accept()
    call = await db.calls.find_one({"_id": ms.oid(call_id)}) if ms.oid(call_id) else None
    if not call or role not in ("patient", "contact"):
        await ws.close(code=4404)
        return
    room = hub.rooms[call_id]
    previous = room.get(role)
    if previous is not None:
        try:
            await previous.close(code=4000)
        except Exception:
            pass
    room[role] = ws
    _off_record.setdefault(call_id, bool(call.get("off_record")))
    await ws.send_json({"type": "welcome", "role": role, "peers": [r for r in room if r != role],
                        "off_record": _off_record[call_id], "status": call["status"]})
    await hub.to_room(call_id, {"type": "peer_joined", "role": role}, exclude=role)
    if role == "contact" and call["status"] == "ringing":
        await db.calls.update_one({"_id": call["_id"]}, {"$set": {"status": "active", "answered_at": clock.now()}})
        await hub.call_changed(call["_id"])

    other = "contact" if role == "patient" else "patient"
    try:
        while True:
            msg = await ws.receive_json()
            kind = msg.get("type")
            if kind == "signal":
                await hub.to_room(call_id, {"type": "signal", "from": role, "data": msg.get("data")}, only=other)
            elif kind == "transcript":
                await save_chunk(call_id, role, msg.get("text", ""))
            elif kind == "interim":
                await hub.to_room(call_id, {"type": "interim", "speaker": role, "text": msg.get("text", "")}, exclude=role)
            elif kind == "off_record":
                _off_record[call_id] = bool(msg.get("value"))
                await db.calls.update_one({"_id": call["_id"]}, {"$set": {"off_record": _off_record[call_id]}})
                await hub.to_room(call_id, {"type": "off_record", "value": _off_record[call_id], "by": role})
            elif kind == "ping":
                await ws.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        if hub.rooms.get(call_id, {}).get(role) is ws:
            del hub.rooms[call_id][role]
            await hub.to_room(call_id, {"type": "peer_left", "role": role})
            if not hub.rooms[call_id]:
                hub.rooms.pop(call_id, None)


@app.websocket("/ws/caregiver/{patient_id}")
async def ws_caregiver(ws: WebSocket, patient_id: str):
    await ws.accept()
    hub.caregivers[patient_id].add(ws)
    await ws.send_json({"type": "welcome", "change_streams": db.capabilities["change_streams"]})
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        hub.caregivers[patient_id].discard(ws)


# ---------------------------------------------------------------- static frontend (production build)
_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _dist.exists():
    app.mount("/assets", StaticFiles(directory=_dist / "assets"), name="assets")

    @app.get("/{path:path}")
    async def spa(path: str):
        file = _dist / path
        if path and file.is_file():
            return FileResponse(file)
        return FileResponse(_dist / "index.html")
