import asyncio
import logging
import re
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import auth, callcard, clock, db, embeddings, lang, llm, recall
from . import memory_store as ms
from .agent import brief as brief_mod
from .agent import guard, pipeline
from .auth import Principal
from .config import settings
from .realtime import hub
from .routes_auth import router as auth_router
from .routes_contact import call_not_answered, ring_message, ring_over
from .routes_contact import router as contact_router
from .seed import seed_demo
from .stats import router as stats_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("pehchaan")

_tasks: set[asyncio.Task] = set()
_brief_tasks: dict[str, asyncio.Task] = {}
_off_record: dict[str, bool] = {}
# While the patient's screen reads aloud (plus a short tail), nothing heard may reach the transcript.
_speaking: dict[str, dict] = {}  # call_id -> {"until": monotonic seconds, "words": set[str]}
TTS_TAIL = 1.5


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
    hub.on_claim = _on_claim
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
app.include_router(contact_router)
app.include_router(stats_router)


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
    out = ms.serialize({k: v for k, v in call.items() if k not in ("join_token", "rung", "declined_by")})
    if call.get("status") in ("ringing", "active") and not (call.get("family") and not call.get("contact_id")):
        out["join_url"] = join_url(call)  # a family call has no link: it rings relatives instead
    return out


def check_join_token(call: dict, token: str | None) -> bool:
    return bool(token) and bool(call.get("join_token")) and secrets.compare_digest(token, call["join_token"])


async def brief_payload(b: dict, call: dict | None = None) -> dict:
    """The patient's side of the brief: only the short "Who is this?" card (no talking points, no hints)."""
    contact = await db.contacts.find_one({"_id": b["contact_id"]})
    patient = await db.users.find_one({"_id": b["owner_id"]})
    return {"brief_id": str(b["_id"]), "mode": b.get("mode"), "created_at": b["created_at"].isoformat(),
            "patient_card": await callcard.patient_card(b, contact, patient, call)}


async def relative_payload(call: dict, b: dict | None) -> dict:
    """The relative's side: talking points, what the patient's card can mention, notes and hints."""
    contact = await db.contacts.find_one({"_id": call["contact_id"]})
    patient = await db.users.find_one({"_id": call["patient_id"]})
    view = await callcard.relative_view(b)
    view["notes"] = await brief_mod.receiver_note(call["patient_id"], contact)
    view["hints"] = view["hints"] or await callcard.relative_hints(call["patient_id"], contact["_id"])
    view["on_screen"] = await callcard.patient_card(b, contact, patient, call)
    return view


async def _refresh_brief(call: dict) -> dict:
    contact = await db.contacts.find_one({"_id": call["contact_id"]})
    b = await brief_mod.build_brief(call["patient_id"], contact, call["_id"])
    call = await db.calls.find_one({"_id": call["_id"]})
    return {"patient": await brief_payload(b, call), "contact": await relative_payload(call, b)}


def contact_link(contact: dict) -> str | None:
    return f"{settings.public_base_url}/c/{contact['link_token']}" if contact.get("link_token") else None


def contact_out(c: dict, caregiver: bool) -> dict:
    """Caregivers get the persistent contact link; the patient's screen never sees tokens or archive notes."""
    out = ms.serialize({k: v for k, v in c.items() if k not in ("link_token", "user_id")})
    if caregiver:
        out["link_url"] = contact_link(c)
    else:
        out.pop("archive_note", None)
        out.pop("account_linked_at", None)
        if c.get("status") == "archived":  # only what voice matching needs
            out = {k: out.get(k) for k in ("id", "name", "nicknames", "relation", "status")}
    return out


async def _ensure_link_tokens(docs: list[dict]):
    for c in docs:
        if not c.get("link_token") and c.get("status") == "active":
            c["link_token"] = secrets.token_urlsafe(12)
            await db.contacts.update_one({"_id": c["_id"], "link_token": {"$exists": False}},
                                         {"$set": {"link_token": c["link_token"]}})


# ---------------------------------------------------------------- system
@app.get("/api/health")
async def health():
    return {"ok": True, "auth": settings.auth_mode, "firebase_project_id": settings.firebase_project_id,
            "llm": llm.status, "embeddings": embeddings.MODE, "embedding_model": embeddings.PRIMARY_MODEL,
            "embedding_status": embeddings.status, "capabilities": db.capabilities,
            "calls": {"ring_timeout_seconds": settings.ring_timeout_seconds,
                      "recall_threshold": recall.threshold()},
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
    if not p.roles:  # a relative's account is a caregiver-kind account without a care circle
        raise HTTPException(403, "caregiver access required")
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


class PatientPatch(BaseModel):
    name: str | None = None
    full_name: str | None = None
    language: str | None = None


@app.patch("/api/patients/{pid}")
async def patch_patient(pid: str, body: PatientPatch, p: Principal = Depends(auth.current)):
    patient_id = auth.require_access(p, pid, role="family")
    fields = {k: v.strip() for k, v in body.model_dump(exclude_none=True).items() if v.strip()}
    if "language" in fields and fields["language"] not in lang.LANGUAGES:
        raise HTTPException(400, "unsupported language")
    if fields:
        await db.users.update_one({"_id": patient_id}, {"$set": fields})
        auth.invalidate()
        await hub.to_patient_home(str(patient_id), {"type": "home_refresh", "reason": "profile changed"})
    return ms.serialize(await db.users.find_one({"_id": patient_id}))


class SpeechStatusIn(BaseModel):
    language: str
    voice_found: bool
    voice_name: str | None = None
    voice_lang: str | None = None
    recognition: bool = True


@app.post("/api/patients/{pid}/speech-status")
async def report_speech(pid: str, body: SpeechStatusIn, p: Principal = Depends(auth.current)):
    """The patient's screen reports whether a voice exists for their language, for the caregiver sidebar."""
    patient_id = auth.require_access(p, pid, allow_device=True)
    await db.users.update_one({"_id": patient_id}, {"$set": {"speech_status": {
        **body.model_dump(), "at": clock.now(),
        "device": (p.device or {}).get("label", "tablet") if p.kind == "device" else "their own account" if p.kind == "patient" else p.display}}})
    return {"ok": True}


@app.get("/api/patients/{pid}/speech-status")
async def speech_status(pid: str, p: Principal = Depends(auth.current)):
    patient_id = auth.require_access(p, pid)
    patient = await db.users.find_one({"_id": patient_id}, {"language": 1, "speech_status": 1})
    return ms.serialize({"language": lang.normalise(patient.get("language")), "status": patient.get("speech_status")})


@app.get("/api/patients/{pid}/contacts")
async def contacts(pid: str, status: str = "active", p: Principal = Depends(auth.current)):
    patient_id = auth.require_access(p, pid, allow_device=True)
    q: dict = {"patient_id": patient_id}
    if status != "all":
        q["status"] = {"$in": status.split(",")}
    docs = [c async for c in db.contacts.find(q).sort([("primary_caregiver", -1), ("created_at", 1)])]
    caregiver = not p.is_patient
    if caregiver:
        await _ensure_link_tokens(docs)
    # One line from the latest patient-facing recap with each person, for the home screen tiles.
    recaps = {r["_id"]: r async for r in db.calls.aggregate([
        {"$match": {"patient_id": patient_id, "status": "processed", "recap": {"$nin": [None, ""]}}},
        {"$sort": {"started_at": -1}},
        {"$group": {"_id": "$contact_id", "recap": {"$first": "$recap"}, "at": {"$first": "$started_at"}}}])}
    pname = (await db.users.find_one({"_id": patient_id}, {"name": 1}) or {}).get("name", "")
    accounts = {}
    if caregiver:
        ids = [c["user_id"] for c in docs if c.get("user_id")]
        accounts = {u["_id"]: u async for u in db.users.find({"_id": {"$in": ids}}, {"email": 1, "name": 1})}
    out = []
    for c in docs:
        o = contact_out(c, caregiver)
        if (u := accounts.get(c.get("user_id"))):
            o["account"] = {"email": u.get("email"), "name": u.get("name")}
        if (r := recaps.get(c["_id"])) and c.get("status") == "active":
            line = callcard.recap_line(r["recap"], c["name"])
            o["last_recap"] = {"text": callcard.you_text(line, pname) if line else None, "at": r["at"].isoformat()}
        out.append(o)
    return out


class ContactIn(BaseModel):
    patient_id: str
    name: str
    relation: str
    relation_detail: str | None = None
    known_as: str | None = None  # the name the patient uses, e.g. "Lata tai"
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
        "known_as": (body.known_as or "").strip() or None, "link_token": secrets.token_urlsafe(12),
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
    return contact_out(contact, True)


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
    known_as: str | None = None
    relation: str | None = None
    relation_detail: str | None = None
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
    # The person on screen may have changed (name, photo): refresh any live call's name tag, and home tiles.
    hub.schedule_brief_refresh(str(contact["_id"]))
    await hub.to_patient_home(str(pid), {"type": "home_refresh", "reason": "contact changed"})
    return contact_out(await db.contacts.find_one({"_id": contact["_id"]}), True)


@app.delete("/api/contacts/{cid}")
async def delete_contact(cid: str, mode: str = "delete", p: Principal = Depends(auth.current)):
    contact = await get_or_404(db.contacts, cid, "contact")
    auth.require_access(p, contact["patient_id"], role="family")
    if mode == "archive":
        return await archive_contact(cid, ArchiveIn(), p)
    # Cascade: forgetting a person forgets every memory about them (event log keeps ids only).
    removed = 0
    async for m in db.memories.find({"contact_id": contact["_id"]}):
        await ms.forget(m, p.display)
        removed += 1
    await db.contacts.delete_one({"_id": contact["_id"]})
    await hub.to_patient_home(str(contact["patient_id"]), {"type": "home_refresh", "reason": "contact removed"})
    return {"deleted": True, "memories_removed": removed}


class ArchiveIn(BaseModel):
    note: str | None = None  # how the family answers when the patient asks for this person


@app.post("/api/contacts/{cid}/archive")
async def archive_contact(cid: str, body: ArchiveIn, p: Principal = Depends(auth.current)):
    """Someone who has died or is no longer in touch. Their tile is hidden, calls are refused, and their
    memories become caregiver_only (the previous visibility is kept so unarchiving restores it)."""
    contact = await get_or_404(db.contacts, cid, "contact")
    pid = auth.require_access(p, contact["patient_id"], role="family")
    fields = {"status": "archived", "archived_at": clock.now(), "archived_by": p.display}
    if body.note is not None:
        fields["archive_note"] = body.note.strip()
    await db.contacts.update_one({"_id": contact["_id"]}, {"$set": fields})
    hidden = 0
    if contact.get("status") != "archived":
        async for m in db.memories.find({"contact_id": contact["_id"], "visibility_before_archive": {"$exists": False}}):
            await ms.set_fields(m, {"visibility_before_archive": m.get("visibility", "patient"),
                                    "visibility": "caregiver_only"}, "archived", p.display)
            hidden += 1
    await hub.to_patient_home(str(pid), {"type": "home_refresh", "reason": "contact archived"})
    return {"archived": True, "memories_hidden": hidden}


@app.post("/api/contacts/{cid}/unarchive")
async def unarchive_contact(cid: str, p: Principal = Depends(auth.current)):
    contact = await get_or_404(db.contacts, cid, "contact")
    pid = auth.require_access(p, contact["patient_id"], role="family")
    await db.contacts.update_one({"_id": contact["_id"]}, {"$set": {"status": "active"},
                                                           "$unset": {"archived_at": "", "archived_by": ""}})
    restored = 0
    async for m in db.memories.find({"contact_id": contact["_id"], "visibility_before_archive": {"$exists": True}}):
        await db.memories.update_one({"_id": m["_id"]}, {"$unset": {"visibility_before_archive": ""}})
        await ms.set_fields(m, {"visibility": m["visibility_before_archive"]}, "unarchived", p.display)
        restored += 1
    await hub.to_patient_home(str(pid), {"type": "home_refresh", "reason": "contact unarchived"})
    return {"archived": False, "memories_restored": restored}


class NoteIn(BaseModel):
    note: str


@app.patch("/api/contacts/{cid}/archive-note")
async def set_archive_note(cid: str, body: NoteIn, p: Principal = Depends(auth.current)):
    contact = await get_or_404(db.contacts, cid, "contact")
    auth.require_access(p, contact["patient_id"], role="family")
    await db.contacts.update_one({"_id": contact["_id"]}, {"$set": {"archive_note": body.note.strip()}})
    return {"ok": True}


@app.post("/api/contacts/{cid}/link")
async def regenerate_link(cid: str, p: Principal = Depends(auth.current)):
    """A new persistent contact link; the old one stops working at once."""
    contact = await get_or_404(db.contacts, cid, "contact")
    auth.require_access(p, contact["patient_id"], role="family")
    token = secrets.token_urlsafe(12)
    await db.contacts.update_one({"_id": contact["_id"]}, {"$set": {"link_token": token}})
    await hub.to_contact(str(contact["_id"]), {"type": "link_revoked"})
    return {"link_url": contact_link({"link_token": token})}


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
    if contact.get("status") not in ("active", "archived"):
        return {"allowed": False, "can_override": False,
                "message": f"{contact['name']} is not in your family list yet. Your family will check first."}

    check = await guard.check(pid, contact)
    now = clock.now()
    if check.get("archived") or (not body.emergency and not check["allowed"] and not (body.force and check["can_override"])):
        # Kept as a "blocked" attempt: caregivers see when and how often the guard steps in.
        await db.calls.insert_one({"patient_id": pid, "contact_id": contact["_id"], "status": "blocked",
                                   "started_at": now, "initiated_by": "patient", "guard": check})
        return check

    call = {"patient_id": pid, "contact_id": contact["_id"], "status": "ringing", "started_at": now,
            "initiated_by": "patient" if p.is_patient else p.display, "emergency": body.emergency,
            "guard": check if not check["allowed"] else None, "off_record": False,
            "join_token": secrets.token_urlsafe(18)}
    call["_id"] = (await db.calls.insert_one(call)).inserted_id
    await hub.call_changed(call["_id"])
    _brief_tasks[str(call["_id"])] = spawn(brief_mod.build_brief(pid, contact, call["_id"]))
    # Ring every open contact link of this person; no answer within the timeout -> "unanswered".
    patient = await db.users.find_one({"_id": pid}, {"name": 1})
    ringing = len(hub.contacts.get(str(contact["_id"]), ()))
    await hub.to_contact(str(contact["_id"]), ring_message(call, patient, contact))
    spawn(_ring_timeout(call["_id"]))
    return {"allowed": True, "call_id": str(call["_id"]), "join_url": join_url(call),
            "overrode": not check["allowed"], "ringing_devices": ringing}


class FamilyCallIn(BaseModel):
    patient_id: str


@app.post("/api/calls/family")
async def start_family_call(body: FamilyCallIn, p: Principal = Depends(auth.current)):
    """"Join the family call": one button for the patient. It always opens the meeting. Relatives who are online
    and allowed right now (quiet hours and daily caps are respected) are rung; anyone who comes online later finds
    it waiting. The first to accept claims it, and it becomes a normal 1:1 call with them."""
    pid = auth.require_access(p, body.patient_id, allow_device=True)
    now = clock.now()
    patient = await db.users.find_one({"_id": pid}, {"name": 1})
    again = await db.calls.find_one({"patient_id": pid, "family": True, "status": "ringing", "contact_id": None,
                                     "started_at": {"$gte": now - timedelta(seconds=settings.family_wait_seconds)}})
    if again:  # a second tap while it is still ringing: the same call
        return {"allowed": True, "family": True, "call_id": str(again["_id"]), "ringing": len(again.get("rung", []))}
    rung = []
    async for c in db.contacts.find({"patient_id": pid, "status": "active"}).sort("created_at", 1):
        if not hub.contacts.get(str(c["_id"])):
            continue
        check = await guard.check(pid, c)
        if check["allowed"] or check["can_override"]:  # a soft "called a while ago" gap still rings; hard rules don't
            rung.append(c)
    base = {"patient_id": pid, "contact_id": None, "family": True, "started_at": now, "initiated_by": "patient",
            "emergency": False, "guard": None, "off_record": False, "join_token": secrets.token_urlsafe(18),
            "rung": [c["_id"] for c in rung], "declined_by": []}
    call = {**base, "status": "ringing"}
    call["_id"] = (await db.calls.insert_one(call)).inserted_id
    await hub.call_changed(call["_id"])
    for c in rung:
        await hub.to_contact(str(c["_id"]), ring_message(call, patient, c))
    spawn(_ring_timeout(call["_id"], settings.family_wait_seconds))
    return {"allowed": True, "family": True, "call_id": str(call["_id"]), "ringing": len(rung)}


async def _on_claim(call_oid, contact: dict):
    """A relative answered a family call: from here it is a normal call with them."""
    _brief_tasks[str(call_oid)] = spawn(brief_mod.build_brief(contact["patient_id"], contact, call_oid))
    await hub.to_room(str(call_oid), {"type": "claimed", "who": await callcard.who(contact),
                                      "contact": contact_out(contact, False)}, only="patient")
    await hub.call_changed(call_oid)


@app.delete("/api/contacts/{cid}/account")
async def unlink_account(cid: str, p: Principal = Depends(auth.current)):
    """A caregiver detaches a relative's account from their contact (the contact link keeps working)."""
    contact = await get_or_404(db.contacts, cid, "contact")
    auth.require_access(p, contact["patient_id"], role="family")
    await db.contacts.update_one({"_id": contact["_id"]}, {"$unset": {"user_id": "", "account_linked_at": ""}})
    auth.invalidate()
    await auth.log_auth_event("relative_unlinked", p, contact["patient_id"], label=contact["name"])
    return {"ok": True}


async def _ring_timeout(call_oid, seconds: float | None = None):
    await asyncio.sleep(seconds or settings.ring_timeout_seconds)
    res = await db.calls.update_one(
        {"_id": call_oid, "status": "ringing", "accepted_at": {"$exists": False}},
        {"$set": {"status": "unanswered", "ended_at": clock.now()}})
    if res.modified_count:
        await call_not_answered(call_oid, "unanswered")


@app.get("/api/calls")
async def list_calls(patient_id: str, limit: int = 40, include_blocked: bool = True, p: Principal = Depends(auth.current)):
    pid = auth.require_access(p, patient_id)
    names = {c["_id"]: c["name"] async for c in db.contacts.find({"patient_id": pid})}
    q: dict = {"patient_id": pid}
    if not include_blocked:
        q["status"] = {"$ne": "blocked"}
    out = []
    async for c in db.calls.find(q).sort("started_at", -1).limit(min(limit, 300)):
        out.append({**call_out(c), "contact_name": names.get(c.get("contact_id")) or ("Family call" if c.get("family") else "?")})
    return out


@app.get("/api/calls/{call_id}")
async def get_call(call_id: str, p: Principal = Depends(auth.current)):
    call = await get_or_404(db.calls, call_id, "call")
    auth.require_access(p, call["patient_id"], allow_device=True)
    contact = await db.contacts.find_one({"_id": call["contact_id"]})
    patient = await db.users.find_one({"_id": call["patient_id"]})
    out = {**call_out(call), "contact": contact_out(contact, not p.is_patient) if contact else None,
           "patient": ms.serialize(patient)}
    if p.is_patient:
        # The name tag can show at once, from the contact alone (no brief, no LLM needed).
        out["who"] = await callcard.who(contact)
        out["also_here_people"] = await callcard.also_here(call)
        out["language"] = lang.normalise(patient.get("language"))
    return out


@app.get("/api/calls/{call_id}/brief")
async def get_brief(call_id: str, p: Principal = Depends(auth.current)):
    call = await get_or_404(db.calls, call_id, "call")
    auth.require_access(p, call["patient_id"], allow_device=True)
    if not call.get("contact_id"):  # a family call nobody has answered: no one to describe yet
        return {"brief_id": None, "mode": None, "created_at": None,
                "patient_card": {"who": None, "memory": None, "also_here": []}}
    return await brief_payload(await _call_brief(call), call)


@app.get("/api/calls/{call_id}/about")
async def get_about(call_id: str, p: Principal = Depends(auth.current)):
    """The patient's "About them" side panel for whoever they are talking to."""
    call = await get_or_404(db.calls, call_id, "call")
    auth.require_access(p, call["patient_id"], allow_device=True)
    patient = await db.users.find_one({"_id": call["patient_id"]})
    contact = await db.contacts.find_one({"_id": call["contact_id"]}) if call.get("contact_id") else None
    return await callcard.about_panel(patient, contact, call)


async def _call_brief(call: dict, wait: float | None = None) -> dict | None:
    task = _brief_tasks.get(str(call["_id"]))
    if task and not task.done():
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=wait)
        except asyncio.TimeoutError:
            return None
    latest = await db.briefs.find_one({"call_id": call["_id"]}, sort=[("created_at", -1)])
    if not latest and wait is None:
        contact = await db.contacts.find_one({"_id": call["contact_id"]})
        latest = await brief_mod.build_brief(call["patient_id"], contact, call["_id"])
    return latest


@app.get("/api/calls/{call_id}/receiver")
async def receiver_info(call_id: str, t: str | None = None):
    """For the family member joining from a link: needs the call's join token, no account."""
    call = await get_or_404(db.calls, call_id, "call")
    if not check_join_token(call, t) or not call.get("contact_id"):
        raise HTTPException(403, "This call link is not valid.")
    if call["status"] == "ringing" and not call.get("accepted_at"):
        # Someone opened the link: stop the no-answer timer and tell the patient they're coming.
        await db.calls.update_one({"_id": call["_id"]}, {"$set": {"accepted_at": clock.now()}})
        await hub.to_room(call_id, {"type": "accepted"}, only="patient")
    contact = await db.contacts.find_one({"_id": call["contact_id"]})
    patient = await db.users.find_one({"_id": call["patient_id"]})
    view = await relative_payload(call, await _call_brief(call, wait=8))
    others = [{"id": str(c["_id"]), "name": c["name"], "relation": c.get("relation")}
              async for c in db.contacts.find({"patient_id": patient["_id"], "status": "active",
                                               "_id": {"$ne": contact["_id"]}}).sort("created_at", 1)]
    return {"call": call_out(call), "contact": contact_out(contact, False), "patient": {"name": patient["name"]},
            "notes": view["notes"], "view": view, "others": others,
            "also_here": [str(i) for i in call.get("also_here", [])]}


@app.get("/api/calls/{call_id}/transcript")
async def get_transcript(call_id: str, p: Principal = Depends(auth.current)):
    call = await get_or_404(db.calls, call_id, "call")
    auth.require_access(p, call["patient_id"])
    return [ms.serialize(c) async for c in db.transcript_chunks.find({"call_id": call["_id"]}).sort("ts", 1)]


class ChunkIn(BaseModel):
    text: str
    t: str | None = None  # join token -> the text is from the contact


def _words(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.lower()))


def _is_read_aloud(call_id: str, speaker: str, text: str) -> bool:
    """Heard while (or just after) the patient's screen was reading aloud. The patient's own mic is
    dropped entirely in that window; the relative's only if it echoes the spoken words."""
    w = _speaking.get(call_id)
    if not w or time.monotonic() > w["until"]:
        return False
    if speaker == "patient":
        return True
    said = _words(text)
    return bool(said) and len(said & w["words"]) / len(said) >= 0.6


async def save_chunk(call_id: str, speaker: str, text: str) -> dict | None:
    text = text.strip()
    if not text or speaker not in ("patient", "contact"):
        return None
    if _is_read_aloud(call_id, speaker, text):
        return None  # the app's own read-aloud never reaches the transcript or memories
    call_oid = ms.oid(call_id)
    if speaker == "patient":
        call = await db.calls.find_one({"_id": call_oid}, {"patient_id": 1})
        patient = await db.users.find_one({"_id": call["patient_id"]}, {"language": 1}) if call else None
        if patient is not None and lang.asks_who(text, patient.get("language")):
            # "Who is this?": the same as pressing the button (simple rules, no LLM).
            await callcard.record_overlay(call_oid, "voice")
            await hub.to_room(call_id, {"type": "who", "via": "voice"}, only="patient")
    msg = {"type": "transcript", "speaker": speaker, "text": text, "ts": clock.now().isoformat()}
    if _off_record.get(call_id):
        await hub.to_room(call_id, {**msg, "off_record": True})
        return None  # never stored, never remembered
    await db.transcript_chunks.insert_one({"call_id": call_oid, "speaker": speaker, "text": text,
                                           "ts": clock.now(), "created_at": datetime.now(timezone.utc)})
    await hub.to_room(call_id, msg)
    if speaker == "contact":
        recall.on_contact_chunk(call_id, text, hub)
    call = await db.calls.find_one({"_id": call_oid}, {"patient_id": 1})
    if call:
        await hub.to_caregivers(str(call["patient_id"]), {**msg, "call_id": call_id})
    return msg


@app.post("/api/calls/{call_id}/transcript")
async def post_chunk(call_id: str, body: ChunkIn, request: Request):
    call = await get_or_404(db.calls, call_id, "call")
    if check_join_token(call, body.t):
        speaker = "contact"
        if call["status"] == "ringing":  # the relative is evidently on the call (e.g. typing instead)
            await db.calls.update_one({"_id": call["_id"], "status": "ringing"}, {"$set": {
                "status": "active", "answered_at": clock.now(), "accepted_at": call.get("accepted_at") or clock.now()}})
            await hub.call_changed(call["_id"])
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
    if call["status"] == "ringing" and not call.get("answered_at"):
        # Hung up before anyone answered: not a conversation, nothing to remember.
        await db.calls.update_one({"_id": call["_id"]}, {"$set": {"status": "cancelled", "ended_at": clock.now()}})
        await hub.to_room(call_id, {"type": "hangup"})
        await ring_over(call, "cancelled")
        await hub.call_changed(call["_id"])
        _speaking.pop(call_id, None)
        return {"status": "cancelled"}
    if call["status"] in ("ringing", "active"):
        now = clock.now()
        await db.calls.update_one({"_id": call["_id"]}, {"$set": {
            "status": "ended", "ended_at": now,
            "duration_sec": int((now - call["started_at"]).total_seconds())}})
        await hub.to_room(call_id, {"type": "hangup"})
        await hub.call_changed(call["_id"])
        _speaking.pop(call_id, None)
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
    call_id: str | None = None  # a relative on a call: their join token is the pass
    t: str | None = None


@app.post("/api/memories/{mid}/feedback")
async def memory_feedback(mid: str, body: FeedbackIn, request: Request):
    """Learned strategy: feedback re-weights importance so the item ranks differently next brief.
    Given by caregivers, or by the relative on a call about what the patient's memory has about them."""
    mem = await get_or_404(db.memories, mid, "memory")
    call = await db.calls.find_one({"_id": ms.oid(body.call_id)}) if body.call_id and ms.oid(body.call_id) else None
    if call and check_join_token(call, body.t):
        if mem.get("contact_id") != call["contact_id"] or mem.get("visibility") != "patient":
            raise HTTPException(404, "memory not found")
        by = "contact"
    else:
        p = await optional_principal(request)
        if p is None:
            raise HTTPException(401, "sign in required")
        auth.require_access(p, mem["owner_id"], allow_device=True)
        by = "patient" if p.is_patient else p.display
    key = "feedback.helpful" if body.helpful else "feedback.not_helpful"
    delta = 0.05 if body.helpful else -0.1
    new_imp = max(0.0, min(1.0, mem.get("importance", 0.5) + delta))
    await db.memories.update_one({"_id": mem["_id"]}, {"$inc": {key: 1}, "$set": {"importance": new_imp}})
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
        await db.calls.update_one({"_id": call["_id"]}, {"$set": {"status": "active", "answered_at": clock.now(),
                                                                  "accepted_at": call.get("accepted_at") or clock.now()}})
        await hub.call_changed(call["_id"])
        if call.get("contact_id"):
            await hub.to_contact(str(call["contact_id"]), {"type": "ring_over", "call_id": call_id, "reason": "answered"})

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
            elif kind == "overlay" and role == "patient":
                await callcard.record_overlay(call["_id"], msg.get("event", ""))
            elif kind == "tts" and role == "patient":
                if msg.get("speaking"):
                    _speaking[call_id] = {"until": time.monotonic() + 30, "words": _words(msg.get("text", ""))}
                elif call_id in _speaking:
                    _speaking[call_id]["until"] = time.monotonic() + TTS_TAIL
            elif kind == "also_here" and role == "contact":
                await set_also_here(call, msg.get("ids") or [])
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


async def set_also_here(call: dict, ids: list[str]):
    """The relative says who else is with them; only the patient's active contacts can be picked."""
    wanted = [ms.oid(i) for i in ids[:4] if ms.oid(i)]
    valid = {c["_id"] async for c in db.contacts.find(
        {"_id": {"$in": wanted}, "patient_id": call["patient_id"], "status": "active"})}
    chosen = [i for i in wanted if i in valid and i != call["contact_id"]]
    await db.calls.update_one({"_id": call["_id"]}, {"$set": {"also_here": chosen}})
    await callcard.record_overlay(call["_id"], "also_here")
    people = await callcard.also_here({**call, "also_here": chosen})
    await hub.to_room(str(call["_id"]), {"type": "also_here", "people": people, "ids": [str(i) for i in chosen]})


@app.websocket("/ws/patient/{patient_id}")
async def ws_patient(ws: WebSocket, patient_id: str):
    """The patient's home screen: told to refresh when a call is processed or a contact changes."""
    await ws.accept()
    hello = await _ws_auth(ws)
    try:
        if not hello:
            raise HTTPException(401)
        p = await auth.principal_from_token(hello.get("token"))
        auth.require_access(p, patient_id, allow_device=True)
    except HTTPException:
        await ws.close(code=4403)
        return
    hub.patients[patient_id].add(ws)
    await ws.send_json({"type": "welcome"})
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        hub.patients[patient_id].discard(ws)


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
