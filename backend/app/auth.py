"""Authentication (Firebase ID tokens) and authorization (care circles in MongoDB).

Identity:
  - Caregivers sign in with Google or email/password through Firebase Auth.
  - A patient can sign in with their own Google or email account on the patient screen.
    The account is the patient record's `firebase_uid`: either the patient creates their
    own profile, or links to the one family made using a sign-in code from the dashboard.
  - Or the patient's tablet signs in *anonymously* and is paired to the patient by a
    caregiver entering the 6-digit code the tablet shows. The patient never types a
    password and is never logged out; caregivers can unpair the device remotely.
  - Family members who join a call use a per-call join token, not an account.

Authorization (all in MongoDB): `memberships` maps caregiver users to patients with a
role (owner > family > viewer); `devices` maps paired tablets to one patient.

Firebase ID tokens are RS256 JWTs signed by Google; we verify them with Google's
published certificates (no service account needed, only the project id).
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
    kind: str  # caregiver | patient | device | unpaired_device
    provider: str
    email: str | None = None
    name: str | None = None
    picture: str | None = None
    user: dict | None = None
    device: dict | None = None
    roles: dict[ObjectId, str] = field(default_factory=dict)
    # Contacts (relatives) this account is linked to, across patients. A relative's account is the same
    # kind of account as a caregiver's: a family member can be both (Rahul is an owner and the contact "Rahul").
    contacts: list[dict] = field(default_factory=list)

    @property
    def display(self) -> str:
        if self.kind == "device":
            return f"device:{self.device.get('label', 'tablet')}" if self.device else "device"
        return self.name or self.email or "caregiver"

    @property
    def is_patient(self) -> bool:
        """The patient themselves: their own account or their paired tablet."""
        return self.kind in ("patient", "device")

    @property
    def patient_ids(self) -> list[ObjectId]:
        return list(self.roles)


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
        return {"sub": f"dev-{uid}", "email": email or None, "name": name or None,
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
    elif (patient := await db.users.find_one({"firebase_uid": uid, "role": "patient"})) is not None:
        # The patient's own account: same access as their paired tablet. Their name stays the one
        # the family chose ("Aaji"), not the account's display name.
        await db.users.update_one({"_id": patient["_id"]}, {"$set": {
            "email": claims.get("email"), "provider": provider, "last_seen": clock.now()}})
        p = Principal(uid=uid, kind="patient", provider=provider, email=claims.get("email"),
                      name=patient.get("name"), picture=claims.get("picture"), user=patient,
                      roles={patient["_id"]: "device"})
    else:
        user = await db.users.find_one_and_update(
            {"firebase_uid": uid},
            {"$set": {"email": claims.get("email"), "name": claims.get("name") or claims.get("email"),
                      "picture": claims.get("picture"), "provider": provider, "last_seen": clock.now()},
             "$setOnInsert": {"role": "caregiver", "created_at": clock.now()}},
            upsert=True, return_document=True)
        roles = {m["patient_id"]: m["role"] async for m in db.memberships.find({"user_id": user["_id"]})}
        linked = [c async for c in db.contacts.find({"user_id": user["_id"], "status": "active"})]
        p = Principal(uid=uid, kind="caregiver", provider=provider, email=claims.get("email"),
                      name=user.get("name"), picture=claims.get("picture"), user=user, roles=roles, contacts=linked)
    _principal_cache[uid] = (time.time() + PRINCIPAL_TTL, p)
    return p


async def principal_from_token(token: str | None) -> Principal:
    if not token:
        raise HTTPException(401, "sign in required")
    return await resolve(await verify_token(token))


async def current(request: Request) -> Principal:
    """FastAPI dependency: the signed-in caregiver or paired device."""
    header = request.headers.get("authorization", "")
    token = header[7:] if header.lower().startswith("bearer ") else None
    return await principal_from_token(token)


# ---------------------------------------------------------------- authorization helpers
def require_caregiver(p: Principal) -> Principal:
    if p.kind != "caregiver":
        raise HTTPException(403, "caregiver sign-in required")
    return p


def require_access(p: Principal, patient_id, *, role: str = "viewer", allow_device: bool = False) -> ObjectId:
    """Raise unless the principal may act on this patient with at least `role`."""
    pid = patient_id if isinstance(patient_id, ObjectId) else (ObjectId(str(patient_id)) if ObjectId.is_valid(str(patient_id)) else None)
    if pid is None or pid not in p.roles:
        raise HTTPException(404, "patient not found")
    held = p.roles[pid]
    if held == "device":
        if not allow_device:
            raise HTTPException(403, "not available on the patient's device")
        return pid
    if ROLE_RANK[held] < ROLE_RANK[role]:
        raise HTTPException(403, f"requires {role} access")
    return pid


async def log_auth_event(kind: str, p: Principal | None, patient_id: ObjectId | None = None, **extra):
    await db.auth_events.insert_one({
        "kind": kind, "patient_id": patient_id, "uid": p.uid if p else None,
        "user_id": p.user["_id"] if p and p.user else None, "who": p.display if p else extra.pop("who", None),
        "provider": p.provider if p else None, "ts": clock.now(), **extra})
