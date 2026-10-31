"""Session, care circles, family invites and patient-device pairing."""
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from . import auth, clock, db
from . import memory_store as ms
from .auth import Principal
from .config import settings

router = APIRouter(prefix="/api")

PAIRING_TTL = timedelta(minutes=15)
INVITE_TTL = timedelta(days=7)
# Unambiguous characters for codes people read aloud or type (no 0/O, 1/I/L).
INVITE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


async def _circles(p: Principal) -> list[dict]:
    out = []
    async for patient in db.users.find({"_id": {"$in": p.patient_ids}}):
        out.append({"patient": ms.serialize(patient), "role": p.roles[patient["_id"]]})
    out.sort(key=lambda c: (not c["patient"].get("demo", False), c["patient"]["name"]))
    return out


def _me(p: Principal, circles: list[dict]) -> dict:
    return {"kind": p.kind, "uid": p.uid, "provider": p.provider, "name": p.name, "email": p.email,
            "picture": p.picture, "user_id": str(p.user["_id"]) if p.user else None, "circles": circles,
            "device": ms.serialize(p.device) if p.device else None, "auth_mode": settings.auth_mode}


async def _relative_links(p: Principal) -> list[dict]:
    """The contacts (relatives) this account is linked to, across patients."""
    out = []
    for c in p.contacts:
        patient = await db.users.find_one({"_id": c["patient_id"]}, {"name": 1})
        out.append({"contact_id": str(c["_id"]), "contact_name": c["name"], "relation": c.get("relation"),
                    "patient_id": str(c["patient_id"]), "patient_name": (patient or {}).get("name", "")})
    return out


@router.get("/me")
async def me(p: Principal = Depends(auth.current)):
    return {**_me(p, await _circles(p)), "relative": await _relative_links(p)}


@router.post("/auth/session")
async def session(p: Principal = Depends(auth.current)):
    """Called once after sign-in: records the sign-in and returns the user's circles."""
    if p.kind == "caregiver":
        await auth.log_auth_event("sign_in", p)
    return {**_me(p, await _circles(p)), "relative": await _relative_links(p)}


# ---------------------------------------------------------------- care circles
class CircleIn(BaseModel):
    name: str  # what the patient likes to be called, e.g. "Aaji"
    full_name: str | None = None
    language: str = "en-IN"
    my_relation: str | None = None  # how the caregiver is related, e.g. "son"


@router.post("/circles")
async def create_circle(body: CircleIn, p: Principal = Depends(auth.current)):
    auth.require_caregiver(p)
    patient = {"name": body.name.strip(), "full_name": (body.full_name or body.name).strip(), "role": "patient",
               "language": body.language, "created_at": clock.now(), "created_by": p.user["_id"]}
    patient["_id"] = (await db.users.insert_one(patient)).inserted_id
    await db.memberships.insert_one({"patient_id": patient["_id"], "user_id": p.user["_id"], "role": "owner",
                                     "relation": body.my_relation, "created_at": clock.now()})
    auth.invalidate(p.uid)
    await auth.log_auth_event("circle_created", p, patient["_id"])
    return ms.serialize(patient)


@router.post("/circles/demo")
async def join_demo(p: Principal = Depends(auth.current)):
    """Open the seeded demo family (Aaji). Each visitor gets owner access to try everything."""
    auth.require_caregiver(p)
    if not settings.demo_open_to_all:
        raise HTTPException(403, "demo is closed")
    demo = await db.users.find_one({"role": "patient", "demo": True})
    if not demo:
        raise HTTPException(404, "no demo family seeded")
    await db.memberships.update_one(
        {"patient_id": demo["_id"], "user_id": p.user["_id"]},
        {"$setOnInsert": {"role": "owner", "relation": "demo visitor", "created_at": clock.now()}}, upsert=True)
    auth.invalidate(p.uid)
    await auth.log_auth_event("member_joined", p, demo["_id"], via="demo")
    return ms.serialize(demo)


@router.get("/circles/{pid}/members")
async def members(pid: str, p: Principal = Depends(auth.current)):
    patient_id = auth.require_access(p, pid)
    out = []
    pipeline = [
        {"$match": {"patient_id": patient_id}},
        {"$lookup": {"from": "users", "localField": "user_id", "foreignField": "_id", "as": "user"}},
        {"$unwind": "$user"},
        {"$sort": {"created_at": 1}},
    ]
    async for m in db.memberships.aggregate(pipeline):
        u = m["user"]
        out.append({"user_id": str(u["_id"]), "name": u.get("name"), "email": u.get("email"),
                    "picture": u.get("picture"), "provider": u.get("provider"), "role": m["role"],
                    "relation": m.get("relation"), "last_seen": ms.serialize(u.get("last_seen")),
                    "is_me": p.user is not None and u["_id"] == p.user["_id"]})
    return out


class RoleIn(BaseModel):
    role: str


@router.patch("/circles/{pid}/members/{uid}")
async def set_role(pid: str, uid: str, body: RoleIn, p: Principal = Depends(auth.current)):
    patient_id = auth.require_access(p, pid, role="owner")
    if body.role not in auth.ROLE_RANK:
        raise HTTPException(400, "unknown role")
    await db.memberships.update_one({"patient_id": patient_id, "user_id": ms.oid(uid)}, {"$set": {"role": body.role}})
    auth.invalidate()
    return {"ok": True}


@router.delete("/circles/{pid}/members/{uid}")
async def remove_member(pid: str, uid: str, p: Principal = Depends(auth.current)):
    patient_id = auth.require_access(p, pid, role="owner")
    owners = await db.memberships.count_documents({"patient_id": patient_id, "role": "owner"})
    target = await db.memberships.find_one({"patient_id": patient_id, "user_id": ms.oid(uid)})
    if target and target["role"] == "owner" and owners <= 1:
        raise HTTPException(400, "a circle needs at least one owner")
    await db.memberships.delete_one({"patient_id": patient_id, "user_id": ms.oid(uid)})
    auth.invalidate()
    await auth.log_auth_event("member_removed", p, patient_id)
    return {"ok": True}


# ---------------------------------------------------------------- invites
class InviteIn(BaseModel):
    role: str = "family"


@router.post("/circles/{pid}/invites")
async def create_invite(pid: str, body: InviteIn, p: Principal = Depends(auth.current)):
    """Owners invite family. role="patient" is a sign-in code for the patient's own account.
    A patient signed in with their own account may invite family too (not a shared tablet)."""
    patient_id = auth.require_access(p, pid, role="owner", allow_device=p.kind == "patient")
    allowed = ("family", "viewer", "owner") if p.kind == "patient" else ("family", "viewer", "owner", "patient")
    if body.role not in allowed:
        raise HTTPException(400, "unknown role")
    code = "".join(secrets.choice(INVITE_ALPHABET) for _ in range(8))
    expires = clock.now() + INVITE_TTL
    await db.invites.insert_one({"code": code, "patient_id": patient_id, "role": body.role,
                                 "created_by": p.user["_id"], "created_at": clock.now(), "expires_at": expires})
    path = f"/patient?claim={code}" if body.role == "patient" else f"/login?invite={code}"
    return {"code": code, "role": body.role, "expires_at": expires.isoformat(),
            "url": f"{settings.public_base_url}{path}"}


async def _valid_invite(code: str) -> dict:
    inv = await db.invites.find_one({"code": code.strip().upper().replace("-", "").replace(" ", "")})
    if not inv or inv["expires_at"] <= clock.now():  # TTL deletion can lag up to a minute
        raise HTTPException(404, "This code is not valid any more. Ask for a new one.")
    return inv


class AcceptIn(BaseModel):
    code: str


@router.post("/invites/accept")
async def accept_invite(body: AcceptIn, p: Principal = Depends(auth.current)):
    auth.require_caregiver(p)
    inv = await _valid_invite(body.code)
    if inv["role"] == "patient":
        raise HTTPException(400, "This code is for the patient's own sign-in. Open it on the patient screen (/patient).")
    await db.memberships.update_one(
        {"patient_id": inv["patient_id"], "user_id": p.user["_id"]},
        {"$setOnInsert": {"role": inv["role"], "created_at": clock.now(), "invited_by": inv["created_by"]}},
        upsert=True)
    await db.invites.update_one({"_id": inv["_id"]}, {"$push": {"used_by": p.user["_id"]}})
    auth.invalidate(p.uid)
    await auth.log_auth_event("member_joined", p, inv["patient_id"], via="invite")
    return ms.serialize(await db.users.find_one({"_id": inv["patient_id"]}))


# ---------------------------------------------------------------- patient devices
@router.post("/devices/pair-code")
async def pair_code(p: Principal = Depends(auth.current)):
    """Called by an unpaired tablet: returns a 6-digit code to show in very large type."""
    if p.is_patient:
        return {"paired": True}
    if p.kind != "unpaired_device":
        raise HTTPException(400, "only a patient device needs a pairing code")
    existing = await db.pairing_codes.find_one({"device_uid": p.uid, "expires_at": {"$gt": clock.now()}})
    if existing:
        return {"code": existing["code"], "expires_at": existing["expires_at"].isoformat()}
    for _ in range(10):
        code = f"{secrets.randbelow(10**6):06d}"
        if not await db.pairing_codes.find_one({"code": code}):
            break
    expires = clock.now() + PAIRING_TTL
    await db.pairing_codes.insert_one({"code": code, "device_uid": p.uid, "created_at": clock.now(),
                                       "expires_at": expires})
    return {"code": code, "expires_at": expires.isoformat()}


@router.get("/devices/me")
async def device_me(p: Principal = Depends(auth.current)):
    """What the patient screen should show: home (paired tablet or patient account), the
    profile step (signed in, not a patient yet), or sign-in / pairing (anonymous)."""
    if p.kind == "device":
        patient = await db.users.find_one({"_id": p.device["patient_id"]})
        return {"paired": True, "device": ms.serialize(p.device), "patient": ms.serialize(patient)}
    if p.kind == "patient":
        return {"paired": True, "patient": ms.serialize(p.user), "account": {"email": p.email}}
    if p.kind == "caregiver":
        return {"paired": False, "account": {"email": p.email, "name": p.name, "family": bool(p.roles)}}
    return {"paired": False}


# ---------------------------------------------------------------- patient accounts
def _require_fresh_account(p: Principal) -> Principal:
    """Only a signed-in account that isn't anyone's patient or caregiver yet can become a patient."""
    if p.kind == "patient":
        raise HTTPException(400, f"This account already signs in as {p.name}.")
    if p.kind != "caregiver":
        raise HTTPException(400, "Sign in with an account first.")
    if p.roles:
        raise HTTPException(400, "This is a family member's account. Use the patient's own account.")
    return p


class RegisterIn(BaseModel):
    name: str  # what they like to be called
    full_name: str | None = None
    language: str = "en-IN"


@router.post("/patients/register")
async def register_patient(body: RegisterIn, p: Principal = Depends(auth.current)):
    """A patient signing up on their own: their account becomes a patient record (a circle with
    no family yet; they invite family from their home screen)."""
    _require_fresh_account(p)
    if not body.name.strip():
        raise HTTPException(400, "Please tell us what to call you.")
    patient = await db.users.find_one_and_update({"_id": p.user["_id"]}, {"$set": {
        "role": "patient", "name": body.name.strip(), "full_name": (body.full_name or body.name).strip(),
        "language": body.language, "self_registered": True}}, return_document=True)
    auth.invalidate(p.uid)
    await auth.log_auth_event("circle_created", p, patient["_id"], via="patient")
    return ms.serialize(patient)


@router.post("/patients/claim")
async def claim_patient(body: AcceptIn, p: Principal = Depends(auth.current)):
    """Link the signed-in account to the patient record family already made, using a sign-in code."""
    _require_fresh_account(p)
    inv = await _valid_invite(body.code)
    if inv["role"] != "patient":
        raise HTTPException(400, "That's a family invite. Family members sign in at /login.")
    patient = await db.users.find_one({"_id": inv["patient_id"]})
    old_uid = patient.get("firebase_uid")
    # The empty account record goes first: firebase_uid is unique across users.
    await db.users.delete_one({"_id": p.user["_id"]})
    await db.users.update_one({"_id": patient["_id"]}, {"$set": {
        "firebase_uid": p.uid, "email": p.email, "provider": p.provider, "login_linked_at": clock.now()}})
    await db.invites.delete_one({"_id": inv["_id"]})  # single use
    auth.invalidate(p.uid)
    if old_uid:
        auth.invalidate(old_uid)
    await auth.log_auth_event("patient_login_linked", p, patient["_id"], who=patient["name"])
    return ms.serialize(await db.users.find_one({"_id": patient["_id"]}))


@router.get("/circles/{pid}/login")
async def patient_login(pid: str, p: Principal = Depends(auth.current)):
    patient_id = auth.require_access(p, pid)
    patient = await db.users.find_one({"_id": patient_id})
    if not patient.get("firebase_uid"):
        return {"linked": False}
    return {"linked": True, "email": patient.get("email"), "provider": patient.get("provider"),
            "last_seen": ms.serialize(patient.get("last_seen")), "self_registered": patient.get("self_registered", False)}


@router.delete("/circles/{pid}/login")
async def remove_patient_login(pid: str, p: Principal = Depends(auth.current)):
    """Unlink the patient's own account (lost phone, wrong account). Their tablet pairings stay."""
    patient_id = auth.require_access(p, pid, role="owner")
    patient = await db.users.find_one({"_id": patient_id})
    await db.users.update_one({"_id": patient_id}, {"$unset": {"firebase_uid": "", "email": "", "provider": ""}})
    if patient.get("firebase_uid"):
        auth.invalidate(patient["firebase_uid"])
    await auth.log_auth_event("patient_login_removed", p, patient_id)
    return {"ok": True}


class PairIn(BaseModel):
    code: str
    label: str = "Patient's tablet"


@router.post("/circles/{pid}/devices")
async def pair_device(pid: str, body: PairIn, p: Principal = Depends(auth.current)):
    patient_id = auth.require_access(p, pid, role="family")
    entry = await db.pairing_codes.find_one({"code": body.code.strip().replace(" ", "")})
    if not entry or entry["expires_at"] <= clock.now():
        raise HTTPException(404, "That code didn't match. Check the number on the tablet and try again.")
    await db.devices.update_one(
        {"firebase_uid": entry["device_uid"]},
        {"$set": {"patient_id": patient_id, "label": body.label.strip() or "Patient's tablet",
                  "paired_by": p.user["_id"], "paired_by_name": p.display, "paired_at": clock.now(),
                  "last_seen": clock.now(), "revoked": False}},
        upsert=True)
    await db.pairing_codes.delete_one({"_id": entry["_id"]})
    auth.invalidate(entry["device_uid"])
    await auth.log_auth_event("device_paired", p, patient_id, label=body.label)
    return {"ok": True}


@router.get("/circles/{pid}/devices")
async def list_devices(pid: str, p: Principal = Depends(auth.current)):
    patient_id = auth.require_access(p, pid)
    return [ms.serialize({k: v for k, v in d.items() if k != "firebase_uid"})
            async for d in db.devices.find({"patient_id": patient_id}).sort("paired_at", -1)]


@router.delete("/devices/{device_id}")
async def revoke_device(device_id: str, p: Principal = Depends(auth.current)):
    device = await db.devices.find_one({"_id": ms.oid(device_id)})
    if not device:
        raise HTTPException(404, "device not found")
    auth.require_access(p, device["patient_id"], role="family")
    await db.devices.update_one({"_id": device["_id"]}, {"$set": {"revoked": True, "revoked_at": clock.now()}})
    auth.invalidate(device["firebase_uid"])
    await auth.log_auth_event("device_unpaired", p, device["patient_id"], label=device.get("label"))
    return {"ok": True}


@router.get("/circles/{pid}/access-log")
async def access_log(pid: str, p: Principal = Depends(auth.current)):
    patient_id = auth.require_access(p, pid, role="owner")
    return [ms.serialize(e) async for e in db.auth_events.find(
        {"$or": [{"patient_id": patient_id}, {"user_id": {"$in": [m["user_id"] async for m in db.memberships.find(
            {"patient_id": patient_id})]}, "kind": "sign_in"}]}).sort("ts", -1).limit(40)]
