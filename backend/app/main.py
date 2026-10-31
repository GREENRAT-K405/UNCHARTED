import asyncio
import logging
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import auth, clock, db, embeddings, llm
from . import memory_store as ms
from .agent import brief as brief_mod
from .agent import guard, pipeline
from .auth import Principal
from .config import settings
from .realtime import hub
from .routes_auth import router as auth_router
from .seed import seed_demo
from .stats import router as stats_router

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
    if not await db.users.find_one({"role": "patient", "demo": True}):
        log.info("seeding demo family: %s", await seed_demo())
    hub.refresh_brief = _refresh_brief
    await hub.start()
    log.info("ready: auth=%s llm=%s embeddings=%s capabilities=%s", settings.auth_mode, llm.status["mode"],
             embeddings.MODE, db.capabilities)
    if settings.auth_mode == "dev":
        log.warning("AUTH IS IN DEV MODE (no FIREBASE_PROJECT_ID): unsigned dev tokens are accepted. Local use only.")
    yield
    await hub.stop()


app = FastAPI(title="Pehchaan", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(auth_router)
app.include_router(stats_router)


# ---------------------------------------------------------------- helpers
async def get_or_404(coll, id_: str, what: str) -> dict:
    doc = await coll.find_one({"_id": ms.oid(id_)}) if ms.oid(id_) else None
    if not doc:
        raise HTTPException(404, f"{what} not found")
    return doc


async def optional_principal(request: Request) -> Principal | None:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        return None
    return await auth.principal_from_token(header[7:])


def join_url(call: dict) -> str:
    return f"{settings.public_base_url}/join/{call['_id']}?t={call.get('join_token', '')}"


def call_out(call: dict) -> dict:
    out = ms.serialize({k: v for k, v in call.items() if k != "join_token"})
    if call.get("status") in ("ringing", "active"):
        out["join_url"] = join_url(call)
    return out


def check_join_token(call: dict, token: str | None) -> bool:
    return bool(token) and bool(call.get("join_token")) and secrets.compare_digest(token, call["join_token"])


def brief_payload(b: dict) -> dict:
    return {"brief_id": str(b["_id"]), "card": b["card"], "mode": b.get("mode"), "created_at": b["created_at"].isoformat()}


async def _refresh_brief(call: dict) -> dict:
    contact = await db.contacts.find_one({"_id": call["contact_id"]})
    return brief_payload(await brief_mod.build_brief(call["patient_id"], contact, call["_id"]))


# ---------------------------------------------------------------- system
@app.get("/api/health")
async def health():
    return {"ok": True, "auth": settings.auth_mode, "firebase_project_id": settings.firebase_project_id,
            "llm": llm.status, "embeddings": embeddings.MODE, "capabilities": db.capabilities,
            "clock": {"now": clock.now().isoformat(), "offset_days": clock.offset_days()}}


class ClockIn(BaseModel):
    shift_days: float | None = None
    reset: bool = False


@app.get("/api/clock")
async def get_clock():
    return {"now": clock.now().isoformat(), "offset_days": clock.offset_days(), "timezone": settings.timezone}


@app.post("/api/clock")
async def set_clock(body: ClockIn, p: Principal = Depends(auth.current)):
    auth.require_caregiver(p)
    if body.reset:
        clock.reset()
    elif body.shift_days:
        clock.shift(body.shift_days)
    return await get_clock()


@app.post("/api/demo/reset")
async def demo_reset(p: Principal = Depends(auth.current)):
    demo = await db.users.find_one({"role": "patient", "demo": True})
    if not demo:
        raise HTTPException(404, "no demo family")
    auth.require_access(p, demo["_id"], role="owner")
    _off_record.clear()
    clock.reset()
    return await seed_demo()


class ExpiringIn(BaseModel):
    patient_id: str
    contact_id: str | None = None
    seconds: int = 90
    content: str | None = None


@app.post("/api/demo/expiring")
async def demo_expiring(body: ExpiringIn, p: Principal = Depends(auth.current)):
    """Add a memory that MongoDB's TTL monitor will delete shortly (it runs about every 60 s)."""
    pid = auth.require_access(p, body.patient_id, role="family")
    contact = await db.contacts.find_one({"_id": ms.oid(body.contact_id), "patient_id": pid}) if body.contact_id else None
    real_expiry = datetime.now(timezone.utc) + timedelta(seconds=body.seconds)
    mem = await ms.insert_memory(
        owner_id=pid, contact_id=contact["_id"] if contact else None, type="plan",
        content=body.content or f"{contact['name'] if contact else 'Rahul'} will drop by with groceries in a few minutes",
        source={"kind": "caregiver", "origin": "caregiver"}, trust=1.0, importance=0.6,
        event_date=clock.now(), expires_at=real_expiry, actor=p.display)
    return ms.serialize(mem)


# ---------------------------------------------------------------- patients & contacts
@app.get("/api/patients")
async def patients(p: Principal = Depends(auth.current)):
    return [ms.serialize(x) async for x in db.users.find({"_id": {"$in": p.patient_ids}, "role": "patient"})]


@app.get("/api/patients/{pid}/contacts")
async def contacts(pid: str, status: str = "active", p: Principal = Depends(auth.current)):
    patient_id = auth.require_access(p, pid, allow_device=True)
    q: dict = {"patient_id": patient_id}
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
async def add_contact(body: ContactIn, p: Principal = Depends(auth.current)):
    pid = auth.require_access(p, body.patient_id, role="family")
    patient = await db.users.find_one({"_id": pid})
    contact = {
        "patient_id": pid, "name": body.name.strip(), "nicknames": body.nicknames,
        "relation": body.relation.strip().lower(), "relation_detail": body.relation_detail, "phone": body.phone,
        "photo": body.photo, "color": body.color or "#55606e", "status": "active", "primary_caregiver": False,
        "created_at": clock.now(), "added_by": p.display,
    }
    contact["_id"] = (await db.contacts.insert_one(contact)).inserted_id
    src = {"kind": "caregiver", "origin": "caregiver", "by": p.display}
    rel = f"{contact['name']} is {patient['name']}'s {contact['relation']}" + (
        f" ({body.relation_detail})" if body.relation_detail else "")
    await ms.insert_memory(owner_id=pid, contact_id=contact["_id"], type="relationship", content=rel,
                           source=src, trust=1.0, importance=1.0, actor=p.display)
    for note in [n.strip() for n in body.notes if n.strip()]:
        await ms.insert_memory(owner_id=pid, contact_id=contact["_id"], type="fact", content=note,
                               source=src, trust=1.0, importance=0.6, actor=p.display)
    return ms.serialize(contact)


class ContactRequestIn(BaseModel):
    patient_id: str
    name: str


@app.post("/api/contacts/request")
async def request_contact(body: ContactRequestIn, p: Principal = Depends(auth.current)):
    """Patient-initiated new contact: pending until a caregiver approves (scam protection)."""
    pid = auth.require_access(p, body.patient_id, allow_device=True)
    contact = {"patient_id": pid, "name": body.name.strip().title(), "nicknames": [], "relation": "unknown",
               "status": "pending", "photo": None, "color": "#8a8f98", "primary_caregiver": False,
               "created_at": clock.now(), "requested_by": "patient"}
    contact["_id"] = (await db.contacts.insert_one(contact)).inserted_id
    await hub.to_caregivers(str(pid), {"type": "contact_request", "contact": ms.serialize(contact)})
    return ms.serialize(contact)


class ContactPatch(BaseModel):
    name: str | None = None
    relation: str | None = None
    nicknames: list[str] | None = None
    photo: str | None = None
    status: str | None = None
    phone: str | None = None


@app.patch("/api/contacts/{cid}")
async def patch_contact(cid: str, body: ContactPatch, p: Principal = Depends(auth.current)):
    contact = await get_or_404(db.contacts, cid, "contact")
    pid = auth.require_access(p, contact["patient_id"], role="family")
    fields = body.model_dump(exclude_none=True)
    if fields:
        await db.contacts.update_one({"_id": contact["_id"]}, {"$set": fields})
    if body.relation and contact.get("status") == "pending":
        patient = await db.users.find_one({"_id": pid})
        await ms.insert_memory(owner_id=pid, contact_id=contact["_id"], type="relationship",
                               content=f"{contact['name']} is {patient['name']}'s {body.relation}",
                               source={"kind": "caregiver", "origin": "caregiver", "by": p.display}, trust=1.0,
                               importance=1.0, actor=p.display)
    return ms.serialize(await db.contacts.find_one({"_id": contact["_id"]}))


@app.delete("/api/contacts/{cid}")
async def delete_contact(cid: str, mode: str = "delete", p: Principal = Depends(auth.current)):
    contact = await get_or_404(db.contacts, cid, "contact")
    auth.require_access(p, contact["patient_id"], role="family")
    if mode == "archive":
        await db.contacts.update_one({"_id": contact["_id"]}, {"$set": {"status": "archived"}})
        return {"archived": True}
    # Cascade: forgetting a person forgets every memory about them (event log keeps ids only).
    removed = 0
    async for m in db.memories.find({"contact_id": contact["_id"]}):
        await ms.forget(m, p.display)
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
async def start_call(body: CallIn, p: Principal = Depends(auth.current)):
    pid = auth.require_access(p, body.patient_id, allow_device=True)
    contact = await get_or_404(db.contacts, body.contact_id, "contact")
    if contact["patient_id"] != pid:
        raise HTTPException(404, "contact not found")
    if contact.get("status") != "active":
        return {"allowed": False, "can_override": False,
                "message": f"{contact['name']} is not in your family list yet. Your family will check first."}

    check = await guard.check(pid, contact)
    now = clock.now()
    if not body.emergency and not check["allowed"] and not (body.force and check["can_override"]):
        # Kept as a "blocked" attempt: caregivers see when and how often the guard steps in.
        await db.calls.insert_one({"patient_id": pid, "contact_id": contact["_id"], "status": "blocked",
                                   "started_at": now, "initiated_by": "patient", "guard": check})
        return check

    call = {"patient_id": pid, "contact_id": contact["_id"], "status": "ringing", "started_at": now,
            "initiated_by": "patient" if p.kind == "device" else p.display, "emergency": body.emergency,
            "guard": check if not check["allowed"] else None, "off_record": False,
            "join_token": secrets.token_urlsafe(18)}
    call["_id"] = (await db.calls.insert_one(call)).inserted_id
    await hub.call_changed(call["_id"])
    _brief_tasks[str(call["_id"])] = spawn(brief_mod.build_brief(pid, contact, call["_id"]))
    return {"allowed": True, "call_id": str(call["_id"]), "join_url": join_url(call),
            "overrode": not check["allowed"]}


@app.get("/api/calls")
async def list_calls(patient_id: str, limit: int = 40, include_blocked: bool = True, p: Principal = Depends(auth.current)):
    pid = auth.require_access(p, patient_id)
    names = {c["_id"]: c["name"] async for c in db.contacts.find({"patient_id": pid})}
    q: dict = {"patient_id": pid}
    if not include_blocked:
        q["status"] = {"$ne": "blocked"}
    out = []
    async for c in db.calls.find(q).sort("started_at", -1).limit(min(limit, 300)):
        out.append({**call_out(c), "contact_name": names.get(c["contact_id"], "?")})
    return out


@app.get("/api/calls/{call_id}")
async def get_call(call_id: str, p: Principal = Depends(auth.current)):
    call = await get_or_404(db.calls, call_id, "call")
    auth.require_access(p, call["patient_id"], allow_device=True)
    contact = await db.contacts.find_one({"_id": call["contact_id"]})
    patient = await db.users.find_one({"_id": call["patient_id"]})
    return {**call_out(call), "contact": ms.serialize(contact), "patient": ms.serialize(patient)}


@app.get("/api/calls/{call_id}/brief")
async def get_brief(call_id: str, p: Principal = Depends(auth.current)):
    call = await get_or_404(db.calls, call_id, "call")
    auth.require_access(p, call["patient_id"], allow_device=True)
    task = _brief_tasks.get(call_id)
    if task and not task.done():
        await asyncio.shield(task)
    latest = await db.briefs.find_one({"call_id": call["_id"]}, sort=[("created_at", -1)])
    if not latest:
        contact = await db.contacts.find_one({"_id": call["contact_id"]})
        latest = await brief_mod.build_brief(call["patient_id"], contact, call["_id"])
    return brief_payload(latest)


@app.get("/api/calls/{call_id}/receiver")
async def receiver_info(call_id: str, t: str | None = None):
    """For the family member joining from a link: needs the call's join token, no account."""
    call = await get_or_404(db.calls, call_id, "call")
    if not check_join_token(call, t):
        raise HTTPException(403, "This call link is not valid.")
    contact = await db.contacts.find_one({"_id": call["contact_id"]})
    patient = await db.users.find_one({"_id": call["patient_id"]})
    return {"call": call_out(call), "contact": ms.serialize(contact),
            "patient": {"name": patient["name"]}, "notes": await brief_mod.receiver_note(patient["_id"], contact)}


@app.get("/api/calls/{call_id}/transcript")
async def get_transcript(call_id: str, p: Principal = Depends(auth.current)):
    call = await get_or_404(db.calls, call_id, "call")
    auth.require_access(p, call["patient_id"])
    return [ms.serialize(c) async for c in db.transcript_chunks.find({"call_id": call["_id"]}).sort("ts", 1)]


class ChunkIn(BaseModel):
    text: str
    t: str | None = None  # join token -> the text is from the contact


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
async def post_chunk(call_id: str, body: ChunkIn, request: Request):
    call = await get_or_404(db.calls, call_id, "call")
    if check_join_token(call, body.t):
        speaker = "contact"
    else:
        p = await optional_principal(request)
        if p is None:
            raise HTTPException(401, "sign in required")
        auth.require_access(p, call["patient_id"], allow_device=True)
        speaker = "patient"
    return {"saved": await save_chunk(call_id, speaker, body.text) is not None}


class EndIn(BaseModel):
    t: str | None = None


@app.post("/api/calls/{call_id}/end")
async def end_call(call_id: str, request: Request, body: EndIn | None = None):
    call = await get_or_404(db.calls, call_id, "call")
    if not check_join_token(call, body.t if body else None):
        p = await optional_principal(request)
        if p is None:
            raise HTTPException(401, "sign in required")
        auth.require_access(p, call["patient_id"], allow_device=True)
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
async def reprocess(call_id: str, p: Principal = Depends(auth.current)):
    call = await get_or_404(db.calls, call_id, "call")
    auth.require_access(p, call["patient_id"], role="family")
    return await pipeline.process_call(call["_id"])


# ---------------------------------------------------------------- memories
@app.get("/api/memories")
async def list_memories(patient_id: str, contact_id: str | None = None, status: str | None = None,
                        type: str | None = None, visibility: str | None = None, q: str | None = None,
                        p: Principal = Depends(auth.current)):
    owner = auth.require_access(p, patient_id)
    if q:
        vec = await embeddings.embed_one(q, "query")
        statuses = tuple(status.split(",")) if status else ("active", "pending_review", "superseded", "rejected")
        hits = await ms.search(owner, ms.oid(contact_id) if contact_id and contact_id != "none" else None, vec,
                               limit=20, statuses=statuses)
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
async def memory_detail(mid: str, p: Principal = Depends(auth.current)):
    mem = await get_or_404(db.memories, mid, "memory")
    auth.require_access(p, mem["owner_id"])
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
async def add_memory(body: MemoryIn, p: Principal = Depends(auth.current)):
    pid = auth.require_access(p, body.patient_id, role="family")
    if body.type not in ms.MEMORY_TYPES:
        raise HTTPException(400, "unknown memory type")
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
        owner_id=pid, contact_id=ms.oid(body.contact_id) if body.contact_id else None, type=body.type,
        content=body.content.strip(), source={"kind": "caregiver", "origin": "caregiver", "by": p.display}, trust=1.0,
        importance=body.importance, visibility=visibility, rule=body.rule, event_date=event_date,
        expires_at=expires_at, actor=p.display)
    return ms.serialize(mem)


class MemoryPatch(BaseModel):
    content: str | None = None
    visibility: str | None = None
    importance: float | None = None
    action: str | None = None  # approve | reject


@app.patch("/api/memories/{mid}")
async def patch_memory(mid: str, body: MemoryPatch, p: Principal = Depends(auth.current)):
    mem = await get_or_404(db.memories, mid, "memory")
    auth.require_access(p, mem["owner_id"], role="family")
    who = p.display
    if body.content is not None and body.content.strip() != mem["content"]:
        # A caregiver correction becomes ground truth.
        await ms.update_content(mem, body.content.strip(), who, {"trust": 1.0, "source.corrected_by": who})
    if body.visibility and body.visibility != mem.get("visibility"):
        await ms.set_fields(mem, {"visibility": body.visibility}, f"visibility:{body.visibility}", who)
    if body.importance is not None:
        await ms.set_fields(mem, {"importance": max(0.0, min(1.0, body.importance))}, "reweighted", who)
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
                await ms.supersede(target, mem["_id"], actor=who)
        await ms.set_fields(mem, fields, "approved", who)
    elif body.action == "reject":
        await ms.set_fields(mem, {"status": "rejected", "visibility": "caregiver_only"}, "rejected", who)
    return ms.serialize(await db.memories.find_one({"_id": mem["_id"]}, {"embedding": 0}))


@app.delete("/api/memories/{mid}")
async def delete_memory(mid: str, p: Principal = Depends(auth.current)):
    mem = await get_or_404(db.memories, mid, "memory")
    auth.require_access(p, mem["owner_id"], role="family")
    await ms.forget(mem, p.display)
    return {"deleted": True}


class FeedbackIn(BaseModel):
    helpful: bool


@app.post("/api/memories/{mid}/feedback")
async def memory_feedback(mid: str, body: FeedbackIn, p: Principal = Depends(auth.current)):
    """Learned strategy: feedback re-weights importance so the item ranks differently next brief."""
    mem = await get_or_404(db.memories, mid, "memory")
    auth.require_access(p, mem["owner_id"], allow_device=True)
    key = "feedback.helpful" if body.helpful else "feedback.not_helpful"
    delta = 0.05 if body.helpful else -0.1
    new_imp = max(0.0, min(1.0, mem.get("importance", 0.5) + delta))
    await db.memories.update_one({"_id": mem["_id"]}, {"$inc": {key: 1}, "$set": {"importance": new_imp}})
    by = "patient" if p.kind == "device" else p.display
    await ms.log_event(mem["_id"], "feedback:helpful" if body.helpful else "feedback:not_helpful", mem["owner_id"], by)
    return {"importance": new_imp}


# ---------------------------------------------------------------- briefs
@app.get("/api/briefs")
async def list_briefs(patient_id: str, contact_id: str | None = None, limit: int = 30,
                      p: Principal = Depends(auth.current)):
    q: dict = {"owner_id": auth.require_access(p, patient_id)}
    if contact_id:
        q["contact_id"] = ms.oid(contact_id)
    return [ms.serialize(b) async for b in db.briefs.find(q).sort("created_at", -1).limit(min(limit, 100))]


@app.get("/api/briefs/{bid}")
async def get_brief_doc(bid: str, p: Principal = Depends(auth.current)):
    b = await get_or_404(db.briefs, bid, "brief")
    auth.require_access(p, b["owner_id"])
    return ms.serialize(b)


@app.post("/api/contacts/{cid}/preview-brief")
async def preview_brief(cid: str, p: Principal = Depends(auth.current)):
    """Caregiver preview: what would the patient see if they called this person now?"""
    contact = await get_or_404(db.contacts, cid, "contact")
    auth.require_access(p, contact["patient_id"])
    return ms.serialize(await brief_mod.build_brief(contact["patient_id"], contact, None))


# ---------------------------------------------------------------- websockets
async def _ws_auth(ws: WebSocket) -> dict | None:
    """First frame must be {"type": "auth", "token": ...} or {"type": "auth", "join_token": ...}.
    Keeps tokens out of URLs (and therefore out of access logs)."""
    try:
        msg = await asyncio.wait_for(ws.receive_json(), timeout=10)
    except Exception:
        return None
    return msg if msg.get("type") == "auth" else None


@app.websocket("/ws/call/{call_id}")
async def ws_call(ws: WebSocket, call_id: str, role: str = "patient"):
    await ws.accept()
    call = await db.calls.find_one({"_id": ms.oid(call_id)}) if ms.oid(call_id) else None
    hello = await _ws_auth(ws)
    if not call or role not in ("patient", "contact") or not hello:
        await ws.close(code=4404)
        return
    try:
        if role == "contact":
            if not check_join_token(call, hello.get("join_token")):
                raise HTTPException(403)
        else:
            p = await auth.principal_from_token(hello.get("token"))
            auth.require_access(p, call["patient_id"], allow_device=True)
    except HTTPException:
        await ws.close(code=4403)
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
            elif kind == "off_record" and role == "contact":
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
    hello = await _ws_auth(ws)
    try:
        if not hello:
            raise HTTPException(401)
        p = await auth.principal_from_token(hello.get("token"))
        auth.require_access(p, patient_id)
    except HTTPException:
        await ws.close(code=4403)
        return
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
