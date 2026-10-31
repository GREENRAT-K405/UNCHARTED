import asyncio
import json
import logging
import re
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pymongo.errors import DuplicateKeyError

from . import auth, clock, db, embeddings, llm
from . import memory_store as ms
from .agent import brief as brief_mod
from .agent import guard, pipeline
from .auth import Principal
from .config import settings
from .realtime import hub
from .routes_auth import router as auth_router
from .routes_faces import MAX_FACES_PER_PERSON, clean_face_input, contact_out, lookalike_of, person_card
from .routes_faces import router as faces_router
from .routes_meets import JOIN_EARLY
from .routes_meets import router as meets_router
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
    spawn(_reembed_loop())
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
app.include_router(faces_router)
app.include_router(meets_router)


async def _reembed_loop():
    """Upgrade fallback vectors to the primary model, one batch per minute (fits free-tier rate limits)."""
    while True:
        try:
            while await embeddings.reembed_stale():
                await asyncio.sleep(25)
        except Exception:
            log.exception("re-embed pass failed")
        await asyncio.sleep(60)


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
    return bool(token) and bool(call.get("join_token")) and secrets.compare_digest(
        str(token).encode(), call["join_token"].encode())


def brief_payload(b: dict) -> dict:
    return {"brief_id": str(b["_id"]), "card": b["card"], "mode": b.get("mode"), "created_at": b["created_at"].isoformat()}


async def _refresh_brief(call: dict) -> dict:
    contact = await db.contacts.find_one({"_id": call["contact_id"]})
    return brief_payload(await brief_mod.build_brief(call["patient_id"], contact, call["_id"]))


# ---------------------------------------------------------------- system
@app.get("/api/health")
async def health():
    return {"ok": True, "auth": settings.auth_mode, "firebase_project_id": settings.firebase_project_id,
            "llm": llm.status, "embeddings": embeddings.MODE, "embedding_model": embeddings.PRIMARY_MODEL,
            "embedding_status": embeddings.status, "capabilities": db.capabilities,
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


# ---------------------------------------------------------------- patients & relatives
@app.get("/api/patients")
async def patients(p: Principal = Depends(auth.current)):
    return [ms.serialize({k: v for k, v in x.items() if k != "firebase_uid"})
            async for x in db.users.find({"_id": {"$in": p.patient_ids}, "role": "patient"})]


@app.get("/api/patients/{pid}/contacts")
async def contacts(pid: str, status: str = "active", p: Principal = Depends(auth.current)):
    """The relatives (and unverified guests) of one patient. Caretakers only: it holds emails and phones."""
    patient_id = auth.require_access(p, pid)
    q: dict = {"patient_id": patient_id}
    if status != "all":
        q["status"] = {"$in": status.split(",")}
    cursor = db.contacts.find(q, {"faces.descriptor": 0, "faces.photo": 0}).sort([("status", 1), ("created_at", 1)])
    return [contact_out(c) async for c in cursor]


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class FaceBlob(BaseModel):
    descriptor: list[float]
    photo: str | None = None


class ContactIn(BaseModel):
    patient_id: str
    name: str
    email: str
    phone: str
    relation: str
    relation_detail: str | None = None
    nicknames: list[str] = []
    notes: list[str] = []
    faces: list[FaceBlob] = []  # computed in the browser from the uploaded photos
    ignore_lookalike: bool = False  # the caretaker confirmed two people really do look alike


def _check_contact_fields(name: str, email: str | None, phone: str | None) -> tuple[str, str | None, str | None]:
    name = name.strip()
    if not name:
        raise HTTPException(400, "enter the person's name")
    email = (email or "").strip().lower() or None
    if email and not EMAIL_RE.match(email):
        raise HTTPException(400, "that email address doesn't look right")
    phone = (phone or "").strip() or None
    if phone and sum(ch.isdigit() for ch in phone) < 7:
        raise HTTPException(400, "enter a phone number with at least 7 digits")
    return name, email, phone


async def _email_taken(pid, email: str | None, except_id=None) -> bool:
    if not email:
        return False
    q = {"patient_id": pid, "email": email, "status": {"$in": ["active", "guest"]}}
    if except_id:
        q["_id"] = {"$ne": except_id}
    return await db.contacts.find_one(q) is not None


@app.post("/api/contacts")
async def add_contact(body: ContactIn, p: Principal = Depends(auth.current)):
    """The caretaker adds a relative: name, email, phone and face photos (so they can be recognised on a call)."""
    pid = auth.require_access(p, body.patient_id, role="family")
    patient = await db.users.find_one({"_id": pid})
    name, email, phone = _check_contact_fields(body.name, body.email, body.phone)
    if not email or not phone:
        raise HTTPException(400, "email and phone are required")
    if not body.relation.strip():
        raise HTTPException(400, "say how they are related to the patient")
    if not body.faces:
        raise HTTPException(400, "add at least one clear photo of their face so they can be recognised on calls")
    if await _email_taken(pid, email):
        raise HTTPException(400, "someone with that email is already in the family")
    face_docs = [clean_face_input(f.descriptor, f.photo) for f in body.faces[:MAX_FACES_PER_PERSON]]
    if not body.ignore_lookalike:
        twin = await lookalike_of([f["descriptor"] for f in face_docs], pid)
        if twin:
            raise HTTPException(409, f"This face looks very similar to {twin}, who is already in the family, so the app "
                                     f"could confuse them. Use clearer photos, or confirm they really look alike.")
    contact = {
        "patient_id": pid, "name": name, "email": email, "phone": phone, "nicknames": body.nicknames,
        "relation": body.relation.strip().lower(), "relation_detail": body.relation_detail,
        "photo": next((f["photo"] for f in face_docs if f.get("photo")), None), "faces": face_docs,
        "color": "#55606e", "status": "active", "primary_caregiver": False, "created_at": clock.now(),
        "added_by": p.display,
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
    auth.invalidate()
    return contact_out(contact)


class ContactPatch(BaseModel):
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    relation: str | None = None
    relation_detail: str | None = None
    nicknames: list[str] | None = None
    status: str | None = None


@app.patch("/api/contacts/{cid}")
async def patch_contact(cid: str, body: ContactPatch, p: Principal = Depends(auth.current)):
    contact = await get_or_404(db.contacts, cid, "contact")
    pid = auth.require_access(p, contact["patient_id"], role="family")
    fields = body.model_dump(exclude_none=True)
    if "name" in fields or "email" in fields or "phone" in fields:
        name, email, phone = _check_contact_fields(fields.get("name", contact["name"]), fields.get("email", contact.get("email")),
                                                   fields.get("phone", contact.get("phone")))
        if await _email_taken(pid, email, contact["_id"]):
            raise HTTPException(400, "someone with that email is already in the family")
        fields.update(name=name, email=email, phone=phone)
    if "relation" in fields:
        fields["relation"] = fields["relation"].strip().lower()
    if fields.get("status") not in (None, "active", "archived"):
        fields.pop("status")
    if fields:
        await db.contacts.update_one({"_id": contact["_id"]}, {"$set": fields})
        auth.invalidate()
    return contact_out(await db.contacts.find_one({"_id": contact["_id"]}))


@app.delete("/api/contacts/{cid}")
async def delete_contact(cid: str, mode: str = "delete", p: Principal = Depends(auth.current)):
    """Remove a relative or reject a guest. Their face data and every memory about them are deleted."""
    contact = await get_or_404(db.contacts, cid, "contact")
    pid = auth.require_access(p, contact["patient_id"], role="family")
    if mode == "archive":
        await db.contacts.update_one({"_id": contact["_id"]}, {"$set": {"status": "archived"}})
        auth.invalidate()
        return {"archived": True}
    removed = 0
    async for m in db.memories.find({"contact_id": contact["_id"]}):
        await ms.forget(m, p.display)
        removed += 1
    await db.contacts.delete_one({"_id": contact["_id"]})
    # Meets that were only for this person are cancelled; in group meets they are just removed from the list.
    await db.meets.update_many({"patient_id": pid, "contact_ids": [contact["_id"]], "status": "scheduled"},
                               {"$set": {"status": "cancelled"}})
    await db.meets.update_many({"patient_id": pid, "contact_ids": contact["_id"]}, {"$pull": {"contact_ids": contact["_id"]}})
    await db.calls.update_many({"patient_id": pid, "invited": contact["_id"]}, {"$pull": {"invited": contact["_id"]}})
    auth.invalidate()
    return {"deleted": True, "memories_removed": removed}


# ---------------------------------------------------------------- calls
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O/1/I/L: easy to read aloud
_code_attempts: dict[str, list[float]] = {}


def _rate_limited(request: Request, limit: int = 12, per: float = 60.0) -> bool:
    ip = (request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (request.client.host if request.client else "?"))
    now = time.time()
    hits = [t for t in _code_attempts.get(ip, []) if now - t < per]
    hits.append(now)
    _code_attempts[ip] = hits
    return len(hits) > limit


class JoinCodeIn(BaseModel):
    code: str


class CallIn(BaseModel):
    meet_id: str | None = None  # join a scheduled meet
    invite_all: bool = False  # notify every relative on their dashboard
    force: bool = False
    patient_id: str | None = None  # only needed when a caretaker starts it


async def _all_active_ids(pid) -> list:
    return [c["_id"] async for c in db.contacts.find({"patient_id": pid, "status": "active"}, {"_id": 1})]


@app.post("/api/calls")
async def start_call(body: CallIn, p: Principal = Depends(auth.current)):
    """The patient starts a meet: a scheduled one, a new one, or a new one that notifies every relative."""
    if p.is_patient_side:
        pid = p.patient_ids[0]
    else:
        pid = auth.require_access(p, body.patient_id, role="family")
    now = clock.now()
    meet = None
    if body.meet_id:
        meet = await db.meets.find_one({"_id": ms.oid(body.meet_id), "patient_id": pid})
        if not meet or meet["status"] != "scheduled":
            raise HTTPException(404, "that meet isn't available any more")
        if (meet["scheduled_at"] - now).total_seconds() > JOIN_EARLY.total_seconds():
            raise HTTPException(400, "it's not time for this meet yet")
    else:
        check = await guard.check(pid, None)
        if not check["allowed"] and not (body.force and check["can_override"]):
            await db.calls.insert_one({"patient_id": pid, "contact_id": None, "status": "blocked", "started_at": now,
                                       "initiated_by": "patient", "guard": check})
            return check

    invited = []
    if meet:
        invited = list(meet.get("contact_ids") or []) or await _all_active_ids(pid)
    elif body.invite_all:
        invited = await _all_active_ids(pid)

    call = {"patient_id": pid, "contact_id": None, "status": "ringing", "started_at": now,
            "initiated_by": "patient" if p.is_patient_side else p.display, "off_record": False,
            "join_token": secrets.token_urlsafe(18), "title": meet["title"] if meet else "New meet",
            "meet_id": meet["_id"] if meet else None, "invited": invited, "invited_at": now if invited else None}
    for _ in range(6):
        call["code"] = "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))
        try:
            call["_id"] = (await db.calls.insert_one(call)).inserted_id
            break
        except DuplicateKeyError:
            call.pop("_id", None)
    else:
        raise HTTPException(500, "could not create a meeting code")
    if meet:
        await db.meets.update_one({"_id": meet["_id"]}, {"$set": {"status": "started", "call_id": call["_id"]}})
    await hub.call_changed(call["_id"])
    return {"allowed": True, "call_id": str(call["_id"]), "code": call["code"], "join_url": join_url(call),
            "invited": len(invited)}


@app.post("/api/calls/{call_id}/invite-all")
async def invite_all(call_id: str, p: Principal = Depends(auth.current)):
    """Notify every relative: the call shows up on each of their dashboards. First to join takes the call."""
    call = await get_or_404(db.calls, call_id, "call")
    pid = auth.require_access(p, call["patient_id"], allow_device=True)
    if call["status"] not in ("ringing", "active"):
        raise HTTPException(400, "this meet has ended")
    ids = await _all_active_ids(pid)
    await db.calls.update_one({"_id": call["_id"]}, {"$set": {"invited": ids, "invited_at": clock.now()}})
    return {"invited": len(ids)}


@app.post("/api/join/code")
async def join_by_code(body: JoinCodeIn, request: Request):
    """A guest types the 6-character code they were given; we find the live meet."""
    if _rate_limited(request):
        raise HTTPException(429, "too many tries, wait a minute")
    code = re.sub(r"[^A-Za-z0-9]", "", body.code).upper()
    call = await db.calls.find_one({"code": code, "status": {"$in": ["ringing", "active"]}}) if len(code) == 6 else None
    if not call:
        raise HTTPException(404, "That code isn't right, or the meet has ended.")
    return {"call_id": str(call["_id"]), "t": call["join_token"]}


@app.get("/api/calls")
async def list_calls(patient_id: str, limit: int = 40, include_blocked: bool = True, p: Principal = Depends(auth.current)):
    pid = auth.require_access(p, patient_id)
    names = {c["_id"]: c["name"] async for c in db.contacts.find({"patient_id": pid}, {"name": 1})}
    q: dict = {"patient_id": pid}
    if not include_blocked:
        q["status"] = {"$ne": "blocked"}
    out = []
    async for c in db.calls.find(q).sort("started_at", -1).limit(min(limit, 300)):
        out.append({**call_out(c), "contact_name": names.get(c.get("contact_id")) or "Nobody joined"})
    return out


@app.get("/api/calls/{call_id}")
async def get_call(call_id: str, p: Principal = Depends(auth.current)):
    call = await get_or_404(db.calls, call_id, "call")
    auth.require_access(p, call["patient_id"], allow_device=True)
    contact = await db.contacts.find_one({"_id": call["contact_id"]}, {"faces": 0}) if call.get("contact_id") else None
    patient = await db.users.find_one({"_id": call["patient_id"]})
    return {**call_out(call), "contact": contact_out(contact) if contact else None,
            "patient": ms.serialize({"id": patient["_id"], "name": patient["name"], "full_name": patient.get("full_name")})}


@app.get("/api/calls/{call_id}/participant")
async def get_participant(call_id: str, p: Principal = Depends(auth.current)):
    """Who is on the other end: name, photo, relation, last meet and what was discussed."""
    call = await get_or_404(db.calls, call_id, "call")
    auth.require_access(p, call["patient_id"], allow_device=True)
    contact = await db.contacts.find_one({"_id": call["contact_id"]}) if call.get("contact_id") else None
    return {"person": await person_card(contact, call["_id"]) if contact else None, "status": call["status"]}


class NoteIn(BaseModel):
    note: str | None = None
    important: bool | None = None


@app.patch("/api/calls/{call_id}/note")
async def note_call(call_id: str, body: NoteIn, p: Principal = Depends(auth.current)):
    """The caretaker's own note on a meet, and a star for important discussions."""
    call = await get_or_404(db.calls, call_id, "call")
    auth.require_access(p, call["patient_id"], role="family")
    sets: dict = {}
    if body.note is not None:
        sets["caretaker_note"] = body.note.strip()[:1500] or None
        sets["note_by"] = p.display
    if body.important is not None:
        sets["important"] = body.important
    if sets:
        await db.calls.update_one({"_id": call["_id"]}, {"$set": sets})
    return {"ok": True}


@app.get("/api/calls/{call_id}/brief")
async def get_brief(call_id: str, p: Principal = Depends(auth.current)):
    """Conversation suggestions for the person on the call (built once they are identified)."""
    call = await get_or_404(db.calls, call_id, "call")
    auth.require_access(p, call["patient_id"], allow_device=True)
    if not call.get("contact_id"):
        return {"brief_id": None, "card": None}
    task = _brief_tasks.get(call_id)
    if task and not task.done():
        await asyncio.shield(task)
    latest = await db.briefs.find_one({"call_id": call["_id"], "contact_id": call["contact_id"]}, sort=[("created_at", -1)])
    if not latest:
        contact = await db.contacts.find_one({"_id": call["contact_id"]})
        _brief_tasks[call_id] = asyncio.ensure_future(brief_mod.build_brief(call["patient_id"], contact, call["_id"]))
        latest = await asyncio.shield(_brief_tasks[call_id])
    return brief_payload(latest)


@app.get("/api/calls/{call_id}/receiver")
async def receiver_info(call_id: str, t: str | None = None):
    """For a guest opening a call link: needs the call's join token, no account."""
    call = await get_or_404(db.calls, call_id, "call")
    if not check_join_token(call, t):
        raise HTTPException(403, "This call link is not valid.")
    patient = await db.users.find_one({"_id": call["patient_id"]})
    return {"call": {"status": call["status"], "title": call.get("title")}, "patient": {"name": patient["name"]}}


@app.get("/api/calls/{call_id}/transcript")
async def get_transcript(call_id: str, p: Principal = Depends(auth.current)):
    call = await get_or_404(db.calls, call_id, "call")
    auth.require_access(p, call["patient_id"])
    return [ms.serialize(c) async for c in db.transcript_chunks.find({"call_id": call["_id"]}).sort("ts", 1)]


class ChunkIn(BaseModel):
    text: str
    t: str | None = None  # join token -> the text is from the guest


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
    p = await optional_principal(request)
    if p is not None and p.kind == "relative" and call["patient_id"] in p.contacts:
        speaker = "contact"
    elif check_join_token(call, body.t):
        speaker = "contact"
    else:
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
        if p.kind == "relative" and call["patient_id"] in p.contacts:
            pass
        else:
            auth.require_access(p, call["patient_id"], allow_device=True)
    if call["status"] in ("ringing", "active"):
        now = clock.now()
        await db.calls.update_one({"_id": call["_id"]}, {"$set": {
            "status": "ended", "ended_at": now,
            "duration_sec": int((now - call["started_at"]).total_seconds())}})
        if call.get("meet_id"):
            await db.meets.update_one({"_id": call["meet_id"]}, {"$set": {"status": "done"}})
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


@app.get("/api/ice")
async def ice_servers():
    """WebRTC servers: STUN to find a path, TURN to relay when a strict network blocks direct video."""
    servers = [{"urls": ["stun:stun.l.google.com:19302", "stun:stun1.l.google.com:19302"]}]
    if settings.turn_urls:
        servers.append({"urls": [u.strip() for u in settings.turn_urls.split(",") if u.strip()],
                        "username": settings.turn_username, "credential": settings.turn_credential})
    else:  # best-effort public relay; video stays end-to-end encrypted
        servers.append({"urls": ["turn:openrelay.metered.ca:80", "turn:openrelay.metered.ca:443",
                                 "turns:openrelay.metered.ca:443?transport=tcp"],
                        "username": "openrelayproject", "credential": "openrelayproject"})
    return {"iceServers": servers}


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
    by = "patient" if p.is_patient_side else p.display
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
    """First frame must be {"type": "auth", ...} with a token, or a join token for guests.
    Keeps secrets out of URLs (and therefore out of access logs)."""
    try:
        msg = await asyncio.wait_for(ws.receive_json(), timeout=10)
    except Exception:
        return None
    return msg if isinstance(msg, dict) and msg.get("type") == "auth" else None


@app.websocket("/ws/call/{call_id}")
async def ws_call(ws: WebSocket, call_id: str, role: str = "patient"):
    await ws.accept()
    call = await db.calls.find_one({"_id": ms.oid(call_id)}) if ms.oid(call_id) else None
    hello = await _ws_auth(ws)
    if not call or role not in ("patient", "contact") or not hello:
        await ws.close(code=4404)
        return
    sid = str(hello.get("sid") or secrets.token_hex(6))[:40]  # one browser tab = one participant
    my_contact, guest_name = None, None
    try:
        if role == "contact":
            if hello.get("join_token"):  # a guest with a link or code
                if not check_join_token(call, hello["join_token"]):
                    raise HTTPException(403)
                guest_name = (hello.get("name") or "").strip()[:60] or None
            else:  # a relative signed in with their account
                p = await auth.principal_from_token(hello.get("token"))
                if p.kind != "relative" or call["patient_id"] not in p.contacts:
                    raise HTTPException(403)
                my_contact = p.contacts[call["patient_id"]]
        else:
            p = await auth.principal_from_token(hello.get("token"))
            if not (p.is_patient_side or p.kind == "caregiver"):
                raise HTTPException(403)
            auth.require_access(p, call["patient_id"], allow_device=True)
    except HTTPException:
        await ws.close(code=4403)
        return

    room = hub.rooms[call_id]
    if role == "contact":
        # Meets are one to one: a second person is turned away, but the same person may reconnect.
        if room.get("contact") is not None and call.get("participant_sid") not in (None, sid):
            await ws.send_json({"type": "full"})
            await ws.close(code=4409)
            return
        updates: dict = {}
        if call.get("participant_sid") != sid:  # someone new in the seat: forget who was there before
            updates["participant_sid"] = sid
            updates["contact_id"] = my_contact["_id"] if my_contact else None
        elif my_contact and call.get("contact_id") != my_contact["_id"]:
            updates["contact_id"] = my_contact["_id"]
        if guest_name:
            updates["guest_name"] = guest_name
        if updates:
            await db.calls.update_one({"_id": call["_id"]}, {"$set": updates})
            call.update(updates)

    previous = room.get(role)
    if previous is not None:
        try:
            await previous.close(code=4000)
        except Exception:
            pass
    room[role] = ws
    _off_record.setdefault(call_id, bool(call.get("off_record")))
    owner = await db.users.find_one({"_id": call["patient_id"]}, {"name": 1})
    await ws.send_json({"type": "welcome", "role": role, "peers": [r for r in room if r != role],
                        "off_record": _off_record[call_id], "status": call["status"],
                        "patient_name": owner["name"] if owner else None})
    await hub.to_room(call_id, {"type": "peer_joined", "role": role}, exclude=role)
    if role == "contact":
        if call["status"] == "ringing":
            await db.calls.update_one({"_id": call["_id"]}, {"$set": {"status": "active", "answered_at": clock.now()}})
            await hub.call_changed(call["_id"])
        await hub.to_room(call_id, {"type": "participant", "known": bool(call.get("contact_id"))}, only="patient")

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
_dist = (Path(__file__).resolve().parents[2] / "frontend" / "dist").resolve()
if (_dist / "index.html").exists():
    app.mount("/assets", StaticFiles(directory=_dist / "assets"), name="assets")
    _index_html = (_dist / "index.html").read_text(encoding="utf-8")

    def _page() -> str:
        """index.html with the Firebase web config injected at request time, so one build runs anywhere."""
        fb = None
        if settings.firebase_project_id and settings.firebase_web_api_key:
            fb = {"apiKey": settings.firebase_web_api_key, "projectId": settings.firebase_project_id,
                  "authDomain": settings.firebase_auth_domain or f"{settings.firebase_project_id}.firebaseapp.com",
                  "appId": settings.firebase_app_id}
        blob = json.dumps({"firebase": fb}).replace("<", "\\u003c")
        return _index_html.replace("</head>", f"<script>window.__APP_CONFIG__={blob}</script></head>", 1)

    @app.get("/{path:path}")
    async def spa(path: str):
        if path.startswith(("api/", "ws/")):
            raise HTTPException(404)
        file = (_dist / path).resolve()
        if path and file.is_file() and _dist in file.parents:  # never serve anything outside dist/
            return FileResponse(file)
        return HTMLResponse(_page(), headers={"Cache-Control": "no-store"})
