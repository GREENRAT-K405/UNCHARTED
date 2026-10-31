"""Face gallery (photos + descriptors per relative), live identification on a call, and unverified guests."""
import secrets
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from . import auth, clock, db, faces
from . import memory_store as ms
from .auth import Principal

router = APIRouter(prefix="/api")

MAX_FACES_PER_PERSON = 8
MAX_PHOTO_CHARS = 160_000  # a 256px JPEG data URL is ~20-40k


# ---------------------------------------------------------------- helpers
def check_photo(photo: str | None) -> str | None:
    if photo is None:
        return None
    if not photo.startswith("data:image/jpeg;base64,") or len(photo) > MAX_PHOTO_CHARS:
        raise HTTPException(400, "photo must be a small JPEG")
    return photo


def clean_face_input(descriptor, photo) -> dict:
    try:
        d = faces.clean_descriptor(descriptor)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    return {"id": secrets.token_hex(6), "descriptor": d, "photo": check_photo(photo), "added_at": clock.now()}


def contact_out(c: dict) -> dict:
    """A contact for the API: never includes face descriptors."""
    out = {k: v for k, v in c.items() if k not in ("faces", "firebase_uid")}
    out["face_count"] = len(c.get("faces", []))
    out["has_account"] = bool(c.get("firebase_uid"))
    return ms.serialize(out)


async def gallery_for(pid: ObjectId) -> list[dict]:
    out = []
    async for c in db.contacts.find({"patient_id": pid, "status": {"$in": ["active", "guest"]},
                                     "faces.0": {"$exists": True}}, {"faces.descriptor": 1}):
        out.append({"id": c["_id"], "faces": c["faces"]})
    return out


async def lookalike_of(descriptors: list[list[float]], pid: ObjectId, except_id: ObjectId | None = None) -> str | None:
    """Name of someone already enrolled whose face is dangerously close to these new photos, if any."""
    from . import faces as fm
    from .config import settings

    gallery = [g for g in await gallery_for(pid) if g["id"] != except_id]
    for d in descriptors:
        ranking = fm.ranked(__import__("numpy").asarray(d, dtype="float32"), gallery)
        if ranking and ranking[0][1] < settings.face_lookalike_threshold:
            c = await db.contacts.find_one({"_id": ObjectId(ranking[0][0])}, {"name": 1})
            return c["name"] if c else "someone"
    return None


async def person_card(contact: dict, call_id: ObjectId | None = None) -> dict:
    """What the patient sees about someone: name, photo, relation, previous meet and what was discussed."""
    q = {"patient_id": contact["patient_id"], "contact_id": contact["_id"], "status": "processed"}
    if call_id:
        q["_id"] = {"$ne": call_id}
    last = await db.calls.find_one(q, sort=[("started_at", -1)])
    meets = await db.calls.count_documents(q)
    guest = contact.get("status") == "guest"
    return {
        "contact_id": str(contact["_id"]), "kind": "guest" if guest else "relative", "name": contact["name"],
        "relation": "New person" if guest else contact.get("relation"), "relation_detail": contact.get("relation_detail"),
        "photo": contact.get("photo"), "unverified": guest, "meets": meets, "first_meeting": meets == 0,
        "last_meet_at": last["started_at"].isoformat() if last else None,
        "last_title": last.get("title") if last else None,
        "last_summary": faces.short(last.get("summary") or last.get("recap")) if last else None,
        "last_note": faces.short(last.get("caretaker_note"), 140) if last and last.get("caretaker_note") else None,
    }


async def assign_participant(call: dict, contact: dict):
    """Record who is on the other end of this call."""
    await db.calls.update_one({"_id": call["_id"]}, {"$set": {"contact_id": contact["_id"],
                                                              "participant_at": clock.now()}})
    call["contact_id"] = contact["_id"]


# ---------------------------------------------------------------- gallery management
@router.get("/contacts/{cid}/faces")
async def list_faces(cid: str, p: Principal = Depends(auth.current)):
    c = await db.contacts.find_one({"_id": ms.oid(cid)})
    if not c:
        raise HTTPException(404, "person not found")
    auth.require_access(p, c["patient_id"])
    return [{"id": f["id"], "photo": f.get("photo"), "added_at": ms.serialize(f.get("added_at"))}
            for f in c.get("faces", [])]


class FaceIn(BaseModel):
    descriptor: list[float]
    photo: str | None = None


@router.post("/contacts/{cid}/faces")
async def add_face(cid: str, body: FaceIn, p: Principal = Depends(auth.current)):
    c = await db.contacts.find_one({"_id": ms.oid(cid)})
    if not c:
        raise HTTPException(404, "person not found")
    auth.require_access(p, c["patient_id"], role="family")
    if len(c.get("faces", [])) >= MAX_FACES_PER_PERSON:
        raise HTTPException(400, f"at most {MAX_FACES_PER_PERSON} photos per person")
    face = clean_face_input(body.descriptor, body.photo)
    sets = {"photo": c.get("photo") or face["photo"]}
    await db.contacts.update_one({"_id": c["_id"]}, {"$push": {"faces": face}, "$set": sets})
    return {"id": face["id"], "face_count": len(c.get("faces", [])) + 1}


@router.delete("/contacts/{cid}/faces/{fid}")
async def delete_face(cid: str, fid: str, p: Principal = Depends(auth.current)):
    c = await db.contacts.find_one({"_id": ms.oid(cid)})
    if not c:
        raise HTTPException(404, "person not found")
    auth.require_access(p, c["patient_id"], role="family")
    remaining = [f for f in c.get("faces", []) if f["id"] != fid]
    if c.get("status") == "active" and not remaining:
        raise HTTPException(400, "keep at least one photo, or remove the person")
    photo = next((f.get("photo") for f in remaining if f.get("photo")), None)
    await db.contacts.update_one({"_id": c["_id"]}, {"$set": {"faces": remaining, "photo": photo}})
    return {"face_count": len(remaining)}


# ---------------------------------------------------------------- matching
class TestIn(BaseModel):
    descriptors: list[list[float]]


@router.post("/patients/{pid}/faces/test")
async def test_match(pid: str, body: TestIn, p: Principal = Depends(auth.current)):
    """Caretaker tool: point a webcam at someone and see who the app thinks they are. Stores nothing."""
    patient_id = auth.require_access(p, pid)
    try:
        descs = faces.clean_descriptors(body.descriptors)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    res = faces.decide(descs, await gallery_for(patient_id))
    out = {k: res.get(k) for k in ("status", "distance", "confidence", "frames_agree")}
    if res.get("contact_id"):
        c = await db.contacts.find_one({"_id": ObjectId(res["contact_id"])}, {"name": 1, "relation": 1, "photo": 1, "status": 1})
        out["person"] = {"name": c["name"], "relation": c.get("relation"), "photo": c.get("photo")} if c else None
        if res.get("runner_up"):
            r = await db.contacts.find_one({"_id": ObjectId(res["runner_up"]["contact_id"])}, {"name": 1})
            out["runner_up"] = {"name": r["name"] if r else "?", "distance": res["runner_up"]["distance"]}
    return out


class IdentifyIn(BaseModel):
    descriptors: list[list[float]]
    snapshot: str | None = None  # small JPEG of the face, saved only if this is a new person


@router.post("/calls/{call_id}/identify")
async def identify(call_id: str, body: IdentifyIn, p: Principal = Depends(auth.current)):
    """The patient's screen sends a few face descriptors from the live video; we say who it is."""
    call = await db.calls.find_one({"_id": ms.oid(call_id)}) if ms.oid(call_id) else None
    if not call:
        raise HTTPException(404, "call not found")
    pid = auth.require_access(p, call["patient_id"], allow_device=True)
    try:
        descs = faces.clean_descriptors(body.descriptors)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    snapshot = check_photo(body.snapshot)

    res = faces.decide(descs, await gallery_for(pid))
    status = res["status"]
    out = {"status": status, "distance": res.get("distance"), "confidence": res.get("confidence"),
           "frames_agree": res.get("frames_agree"), "person": None, "agrees": None}
    current_id = call.get("contact_id")
    current = await db.contacts.find_one({"_id": current_id}) if current_id else None

    if status == "matched":
        contact = await db.contacts.find_one({"_id": ObjectId(res["contact_id"])})
        out["person"] = await person_card(contact, call["_id"])
        if current is None:
            await assign_participant(call, contact)
        elif current["_id"] == contact["_id"]:
            out["agrees"] = True
            if contact.get("status") == "guest" and len(contact.get("faces", [])) < 6 and res["distance"] > 0.3:
                # A guest keeps being re-recognised better the more we see them (never done for relatives).
                await db.contacts.update_one({"_id": contact["_id"]}, {"$push": {"faces": {
                    "id": secrets.token_hex(6), "descriptor": descs[0], "photo": None, "added_at": clock.now()}}})
        elif current.get("status") == "guest" and current.get("created_via") == call["_id"]:
            # We had created a guest for this call, but the face is someone known. If the guest is only seconds
            # old it came from a few bad early frames: discard it. Otherwise keep it for the caretaker to review.
            if (clock.now() - current["created_at"]).total_seconds() < 60:
                await db.contacts.delete_one({"_id": current["_id"]})
            await assign_participant(call, contact)
        else:
            out["agrees"] = False  # signed in as one relative, but the face matches another
            out["claimed"] = {"name": current["name"]}
    elif status == "unsure":
        contact = await db.contacts.find_one({"_id": ObjectId(res["contact_id"])})
        out["candidate"] = {"name": contact["name"], "relation": contact.get("relation"), "photo": contact.get("photo")}
        if current:
            out["person"] = await person_card(current, call["_id"])
    else:  # unknown, or nothing in the gallery yet
        if current is not None:
            out["person"] = await person_card(current, call["_id"])
            out["agrees"] = False if current.get("status") != "guest" else None
        else:
            name = (call.get("guest_name") or "").strip()[:60] or "New person"
            guest = {"patient_id": pid, "name": name, "nicknames": [], "relation": "unknown", "status": "guest",
                     "photo": snapshot, "color": "#8a8f98", "primary_caregiver": False, "created_at": clock.now(),
                     "first_seen": clock.now(), "created_via": call["_id"],
                     "faces": [{"id": secrets.token_hex(6), "descriptor": d, "photo": snapshot if i == 0 else None,
                                "added_at": clock.now()} for i, d in enumerate(descs[:4])]}
            guest["_id"] = (await db.contacts.insert_one(guest)).inserted_id
            await assign_participant(call, guest)
            out["status"] = "new"
            out["person"] = await person_card(guest, call["_id"])
            await ms.log_event(guest["_id"], "guest_created", pid, "face match", call["_id"])
            from .realtime import hub  # imported late: realtime imports memory_store

            await hub.to_caregivers(str(pid), {"type": "contact_request", "contact": contact_out(guest)})
    return out


# ---------------------------------------------------------------- unverified guests
@router.get("/patients/{pid}/guests")
async def list_guests(pid: str, p: Principal = Depends(auth.current)):
    patient_id = auth.require_access(p, pid)
    out = []
    async for g in db.contacts.find({"patient_id": patient_id, "status": "guest"}).sort("first_seen", -1):
        n = await db.calls.count_documents({"contact_id": g["_id"], "status": {"$in": ["processed", "ended", "processing"]}})
        last = await db.calls.find_one({"contact_id": g["_id"], "status": "processed"}, sort=[("started_at", -1)])
        out.append({**contact_out(g), "meets": n, "last_summary": faces.short(last.get("summary")) if last else None})
    return out


class ApproveIn(BaseModel):
    name: str
    relation: str
    relation_detail: str | None = None
    email: str | None = None
    phone: str | None = None


@router.post("/contacts/{cid}/approve")
async def approve_guest(cid: str, body: ApproveIn, p: Principal = Depends(auth.current)):
    """Turn an unverified guest into a relative (they keep their saved face and meet history)."""
    c = await db.contacts.find_one({"_id": ms.oid(cid), "status": "guest"})
    if not c:
        raise HTTPException(404, "guest not found")
    pid = auth.require_access(p, c["patient_id"], role="family")
    patient = await db.users.find_one({"_id": pid})
    email = (body.email or "").strip().lower() or None
    if email and await db.contacts.find_one({"patient_id": pid, "email": email, "_id": {"$ne": c["_id"]},
                                             "status": {"$in": ["active", "guest"]}}):
        raise HTTPException(400, "someone with that email is already in the family")
    fields = {"name": body.name.strip(), "relation": body.relation.strip().lower(), "status": "active",
              "relation_detail": body.relation_detail, "email": email, "phone": (body.phone or "").strip() or None,
              "approved_by": p.display, "approved_at": clock.now()}
    await db.contacts.update_one({"_id": c["_id"]}, {"$set": fields})
    await ms.insert_memory(owner_id=pid, contact_id=c["_id"], type="relationship",
                           content=f"{fields['name']} is {patient['name']}'s {fields['relation']}",
                           source={"kind": "caregiver", "origin": "caregiver", "by": p.display}, trust=1.0,
                           importance=1.0, actor=p.display)
    auth.invalidate()
    return contact_out(await db.contacts.find_one({"_id": c["_id"]}))
