"""Authentication (Firebase ID tokens) and authorization (care circles in MongoDB).

Three kinds of people sign in:
  caregiver  the caretaker/admin: creates the care circle, manages relatives, schedules meets.
             Role inside a circle (owner / family / viewer) lives in `memberships`.
  patient    signs in with Google using the email the caretaker entered (`users.login_email`),
             or is a tablet paired with a 6-digit code (anonymous Firebase sign-in).
  relative   signs in with Google using the email the caretaker entered for them (`contacts.email`).

Anyone else who signs in is simply a new caretaker with no circle yet (they can create one).
Email-based matching requires a *verified* email, so nobody can claim a relative's address by
signing up with it.

Firebase ID tokens are RS256 JWTs signed by Google; we verify them with Google's published
certificates (no service account needed, only the project id).
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field

import requests
from bson import ObjectId
from fastapi import HTTPException, Request
from google.auth import jwt as google_jwt

from . import clock, db
from .config import settings

log = logging.getLogger("pehchaan.auth")

CERTS_URL = "https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com"
ROLE_RANK = {"viewer": 0, "family": 1, "owner": 2}

_certs: dict = {"keys": None, "expires": 0.0}
_principal_cache: dict[str, tuple[float, "Principal"]] = {}
PRINCIPAL_TTL = 30.0


@dataclass
class Principal:
    uid: str
    kind: str  # caregiver | patient | device | unpaired_device | relative
    provider: str
    email: str | None = None
    name: str | None = None
    picture: str | None = None
    user: dict | None = None
    device: dict | None = None
    patient: dict | None = None  # kind == patient
    contacts: dict[ObjectId, dict] = field(default_factory=dict)  # kind == relative: patient id -> contact
    roles: dict[ObjectId, str] = field(default_factory=dict)

    @property
    def display(self) -> str:
        if self.kind == "device":
            return f"device:{self.device.get('label', 'tablet')}" if self.device else "device"
        if self.kind == "relative" and self.contacts:
            return next(iter(self.contacts.values())).get("name") or "relative"
        if self.kind == "patient" and self.patient:
            return self.patient.get("name") or "patient"
        return self.name or self.email or "caregiver"

    @property
    def patient_ids(self) -> list[ObjectId]:
        return list(self.roles)

    @property
    def is_patient_side(self) -> bool:
        """The patient on a tablet or signed in with Google: both act as the patient."""
        return self.kind in ("device", "patient")


# ---------------------------------------------------------------- token verification
def _fetch_certs() -> dict:
    now = time.time()
    if _certs["keys"] and now < _certs["expires"]:
        return _certs["keys"]
    resp = requests.get(CERTS_URL, timeout=10)
    resp.raise_for_status()
    max_age = 3600
    for part in resp.headers.get("Cache-Control", "").split(","):
        if part.strip().startswith("max-age="):
            max_age = int(part.strip().split("=")[1])
    _certs.update(keys=resp.json(), expires=now + max_age)
    return _certs["keys"]


def _verify_firebase(token: str) -> dict:
    claims = google_jwt.decode(token, certs=_fetch_certs(), audience=settings.firebase_project_id,
                               clock_skew_in_seconds=10)
    if claims.get("iss") != f"https://securetoken.google.com/{settings.firebase_project_id}":
        raise ValueError("wrong issuer")
    if not claims.get("sub"):
        raise ValueError("missing subject")
    return claims


def _verify_dev(token: str) -> dict:
    """Dev mode only (no FIREBASE_PROJECT_ID): 'dev:<uid>|<email>|<name>' or 'dev-anon:<uid>'."""
    if token.startswith("dev-anon:"):
        uid = token.split(":", 1)[1]
        return {"sub": f"anon-{uid}", "firebase": {"sign_in_provider": "anonymous"}}
    if token.startswith("dev:"):
        uid, _, rest = token[4:].partition("|")
        email, _, name = rest.partition("|")
        return {"sub": f"dev-{uid}", "email": email or None, "name": name or None, "email_verified": True,
                "firebase": {"sign_in_provider": "dev"}}
    raise ValueError("not a dev token")


async def verify_token(token: str) -> dict:
    try:
        if settings.auth_mode == "firebase":
            return await asyncio.to_thread(_verify_firebase, token)
        return _verify_dev(token)
    except Exception as e:
        raise HTTPException(401, f"invalid token: {e}") from None


# ---------------------------------------------------------------- principal resolution
def invalidate(uid: str | None = None):
    if uid is None:
        _principal_cache.clear()
    else:
        _principal_cache.pop(uid, None)


async def _match_email(email: str | None):
    """Is this verified email a patient's login or a relative the caretaker added?"""
    if not email:
        return None
    patient = await db.users.find_one({"role": "patient", "login_email": email})
    if patient:
        return "patient", patient
    contacts = [c async for c in db.contacts.find({"email": email, "status": "active"})]
    if contacts:
        return "relative", contacts
    return None


async def resolve(claims: dict) -> Principal:
    uid = claims["sub"]
    cached = _principal_cache.get(uid)
    if cached and cached[0] > time.time():
        return cached[1]

    provider = claims.get("firebase", {}).get("sign_in_provider", "unknown")
    if provider == "anonymous":
        device = await db.devices.find_one({"firebase_uid": uid, "revoked": {"$ne": True}})
        if device:
            await db.devices.update_one({"_id": device["_id"]}, {"$set": {"last_seen": clock.now()}})
            p = Principal(uid=uid, kind="device", provider=provider, device=device,
                          roles={device["patient_id"]: "device"})
        else:
            p = Principal(uid=uid, kind="unpaired_device", provider=provider)
        _principal_cache[uid] = (time.time() + PRINCIPAL_TTL, p)
        return p

    email = (claims.get("email") or "").strip().lower() or None
    verified = bool(claims.get("email_verified"))
    picture = claims.get("picture")
    now = clock.now()
    user = await db.users.find_one({"firebase_uid": uid})

    if user and user.get("role") == "patient":  # a patient who signed in with Google before
        if user.get("login_email") != email:
            raise HTTPException(403, "This sign-in no longer has access.")
        await db.users.update_one({"_id": user["_id"]}, {"$set": {"last_seen": now, "picture": picture}})
        p = Principal(uid=uid, kind="patient", provider=provider, email=email, name=user.get("name"),
                      picture=picture, patient=user, roles={user["_id"]: "device"})
        _principal_cache[uid] = (time.time() + PRINCIPAL_TTL, p)
        return p

    roles = {}
    if user:
        roles = {m["patient_id"]: m["role"] async for m in db.memberships.find({"user_id": user["_id"]})}
    if not roles and verified:
        match = await _match_email(email)
        if match and match[0] == "patient":
            patient = match[1]
            await db.users.update_one({"_id": patient["_id"]}, {"$set": {"firebase_uid": uid, "last_seen": now,
                                                                         "picture": picture}})
            p = Principal(uid=uid, kind="patient", provider=provider, email=email, name=patient.get("name"),
                          picture=picture, patient=patient, roles={patient["_id"]: "device"})
            _principal_cache[uid] = (time.time() + PRINCIPAL_TTL, p)
            await log_auth_event("sign_in", p, patient["_id"])
            return p
        if match and match[0] == "relative":
            await db.contacts.update_many({"email": email, "status": "active"},
                                          {"$set": {"firebase_uid": uid, "last_login": now}})
            by_patient = {c["patient_id"]: c for c in match[1]}
            p = Principal(uid=uid, kind="relative", provider=provider, email=email, name=match[1][0].get("name"),
                          picture=picture, contacts=by_patient, roles={pid: "relative" for pid in by_patient})
            _principal_cache[uid] = (time.time() + PRINCIPAL_TTL, p)
            return p

    # A caretaker (new or returning).
    user = await db.users.find_one_and_update(
        {"firebase_uid": uid},
        {"$set": {"email": email, "name": claims.get("name") or email, "picture": picture, "provider": provider,
                  "last_seen": now},
         "$setOnInsert": {"role": "caregiver", "created_at": now}},
        upsert=True, return_document=True)
    if not roles:
        roles = {m["patient_id"]: m["role"] async for m in db.memberships.find({"user_id": user["_id"]})}
    p = Principal(uid=uid, kind="caregiver", provider=provider, email=email, name=user.get("name"),
                  picture=picture, user=user, roles=roles)
    _principal_cache[uid] = (time.time() + PRINCIPAL_TTL, p)
    return p


async def principal_from_token(token: str | None) -> Principal:
    if not token:
        raise HTTPException(401, "sign in required")
    return await resolve(await verify_token(token))


async def current(request: Request) -> Principal:
    """FastAPI dependency: whoever is signed in."""
    header = request.headers.get("authorization", "")
    token = header[7:] if header.lower().startswith("bearer ") else None
    return await principal_from_token(token)


# ---------------------------------------------------------------- authorization helpers
def require_caregiver(p: Principal) -> Principal:
    if p.kind != "caregiver":
        raise HTTPException(403, "caretaker sign-in required")
    return p


def require_relative(p: Principal) -> Principal:
    if p.kind != "relative":
        raise HTTPException(403, "relative sign-in required")
    return p


def as_object_id(value) -> ObjectId | None:
    if isinstance(value, ObjectId):
        return value
    return ObjectId(str(value)) if ObjectId.is_valid(str(value)) else None


def require_access(p: Principal, patient_id, *, role: str = "viewer", allow_device: bool = False,
                   allow_relative: bool = False) -> ObjectId:
    """Raise unless the principal may act on this patient with at least `role`."""
    pid = as_object_id(patient_id)
    if pid is None or pid not in p.roles:
        raise HTTPException(404, "patient not found")
    held = p.roles[pid]
    if held == "device":
        if not allow_device:
            raise HTTPException(403, "not available on the patient's screen")
        return pid
    if held == "relative":
        if not allow_relative:
            raise HTTPException(403, "not available to relatives")
        return pid
    if ROLE_RANK[held] < ROLE_RANK[role]:
        raise HTTPException(403, f"requires {role} access")
    return pid


async def log_auth_event(kind: str, p: Principal | None, patient_id: ObjectId | None = None, **extra):
    await db.auth_events.insert_one({
        "kind": kind, "patient_id": patient_id, "uid": p.uid if p else None,
        "user_id": p.user["_id"] if p and p.user else None, "who": p.display if p else extra.pop("who", None),
        "provider": p.provider if p else None, "ts": clock.now(), **extra})
