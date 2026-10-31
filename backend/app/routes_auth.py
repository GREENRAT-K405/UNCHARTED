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


@router.get("/me")
async def me(p: Principal = Depends(auth.current)):
    return _me(p, await _circles(p))


@router.post("/auth/session")
async def session(p: Principal = Depends(auth.current)):
    """Called once after sign-in: records the sign-in and returns the user's circles."""
    if p.kind == "caregiver":
        await auth.log_auth_event("sign_in", p)
    return _me(p, await _circles(p))


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
    patient_id = auth.require_access(p, pid, role="owner")
    if body.role not in ("family", "viewer", "owner"):
        raise HTTPException(400, "unknown role")
    code = "".join(secrets.choice(INVITE_ALPHABET) for _ in range(8))
    expires = clock.now() + INVITE_TTL
    await db.invites.insert_one({"code": code, "patient_id": patient_id, "role": body.role,
                                 "created_by": p.user["_id"], "created_at": clock.now(), "expires_at": expires})
    return {"code": code, "role": body.role, "expires_at": expires.isoformat(),
            "url": f"{settings.public_base_url}/login?invite={code}"}


class AcceptIn(BaseModel):
    code: str


@router.post("/invites/accept")
async def accept_invite(body: AcceptIn, p: Principal = Depends(auth.current)):
    auth.require_caregiver(p)
    inv = await db.invites.find_one({"code": body.code.strip().upper().replace("-", "")})
    if not inv or inv["expires_at"] <= clock.now():  # TTL deletion can lag up to a minute
        raise HTTPException(404, "This invite code is not valid any more. Ask for a new one.")
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
    if p.kind == "device":
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
    if p.kind != "device":
        return {"paired": False}
    patient = await db.users.find_one({"_id": p.device["patient_id"]})
    return {"paired": True, "device": ms.serialize(p.device), "patient": ms.serialize(patient)}


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
