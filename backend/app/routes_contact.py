"""Relatives: the persistent contact link (/c/{token}) and relatives' own accounts (/family).

Every contact has one random link token. A relative can use the link alone (no account), or keep
it on their own account (the same Firebase sign-in a caregiver uses) so they find their way back
without the link. Either way the page is a waiting room that registers presence over a WebSocket,
rings when the patient calls, and, when nothing is ringing, lets the relative share an update
about their own life (through the normal memory pipeline), read notes and see past calls.

Two kinds of ring share one code path:
  - a call to one person (the patient tapped their photo): rings that contact's open pages;
  - a family call (the patient pressed "Join the family call"): rings every contact that is online
    and allowed right now. The first to accept claims it atomically and the call becomes a normal
    1:1 call with that person; everyone else's ring stops.
"""
import asyncio
import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from . import auth, callcard, clock, db
from . import memory_store as ms
from .agent import brief as brief_mod
from .agent import pipeline
from .auth import Principal
from .realtime import hub

log = logging.getLogger("pehchaan.contact")
router = APIRouter()

PAST_CALLS = 10


async def contact_by_token(token: str | None) -> dict:
    contact = await db.contacts.find_one({"link_token": token}) if token else None
    if not contact or not secrets.compare_digest(contact["link_token"], token):
        raise HTTPException(404, "This link isn't valid any more. Ask the family for a new one.")
    if contact.get("status") != "active":
        raise HTTPException(410, "This link is no longer active.")
    return contact


# ---------------------------------------------------------------- ringing
async def ringing_call(contact: dict) -> dict | None:
    """A call to this person, or an open family meeting of their patient that nobody has joined yet
    (so a relative who comes online later still finds it)."""
    cid = contact["_id"]
    return await db.calls.find_one({"status": "ringing", "$or": [
        {"contact_id": cid},
        {"contact_id": None, "family": True, "patient_id": contact["patient_id"], "declined_by": {"$ne": cid}}]},
        sort=[("started_at", -1)])


def ring_message(call: dict, patient: dict | None, contact: dict) -> dict:
    """A family call's join token is only handed to whoever answers it."""
    msg = {"type": "ring", "call_id": str(call["_id"]), "contact_id": str(contact["_id"]),
           "patient_name": (patient or {}).get("name", ""), "emergency": bool(call.get("emergency")),
           "family": bool(call.get("family"))}
    if not call.get("family"):
        msg["join_token"] = call["join_token"]
    return msg


def ring_targets(call: dict) -> list:
    return [call["contact_id"]] if call.get("contact_id") else list(call.get("rung", []))


async def ring_over(call: dict, reason: str):
    for cid in ring_targets(call):
        await hub.to_contact(str(cid), {"type": "ring_over", "call_id": str(call["_id"]), "reason": reason})


async def call_not_answered(call_oid, reason: str):
    """Unanswered or declined: a calm message on the patient's screen, and the ring stops everywhere."""
    call = await db.calls.find_one({"_id": call_oid})
    await hub.to_room(str(call_oid), {"type": "no_answer", "reason": reason, "family": bool(call.get("family"))},
                      only="patient")
    await ring_over(call, reason)
    await hub.call_changed(call_oid)


async def _accept(ws: WebSocket, contact: dict, call: dict):
    cid, call_id = contact["_id"], str(call["_id"])
    if call.get("contact_id") is None and call.get("family"):
        if call["patient_id"] != contact["patient_id"]:
            return
        # Atomic claim: only one relative can take a family call.
        res = await db.calls.update_one({"_id": call["_id"], "status": "ringing", "contact_id": None},
                                        {"$set": {"contact_id": cid, "accepted_at": clock.now()}})
        if not res.modified_count:
            await ws.send_json({"type": "ring_over", "call_id": call_id, "reason": "taken"})
            return
        for other in {*call.get("rung", []), cid}:
            await hub.to_contact(str(other), {"type": "ring_over", "call_id": call_id, "reason": "answered"},
                                 exclude=ws if other == cid else None)
        if hub.on_claim:
            await hub.on_claim(call["_id"], contact)
    elif call.get("contact_id") == cid:
        await db.calls.update_one({"_id": call["_id"]}, {"$set": {"accepted_at": clock.now()}})
        await hub.to_contact(str(cid), {"type": "ring_over", "call_id": call_id, "reason": "answered"}, exclude=ws)
    else:
        return
    await hub.to_room(call_id, {"type": "accepted"}, only="patient")
    await ws.send_json({"type": "accepted", "call_id": call_id, "join_token": call["join_token"]})


async def _decline(contact: dict, call: dict):
    cid, call_id = contact["_id"], str(call["_id"])
    if call.get("contact_id") is None and call.get("family"):
        # A family call is an open meeting: one relative saying no just stops their own ring.
        await db.calls.update_one({"_id": call["_id"]}, {"$addToSet": {"declined_by": cid}})
        await hub.to_contact(str(cid), {"type": "ring_over", "call_id": call_id, "reason": "declined"})
    elif call.get("contact_id") == cid:
        res = await db.calls.update_one({"_id": call["_id"], "status": "ringing"},
                                        {"$set": {"status": "declined", "ended_at": clock.now()}})
        if res.modified_count:
            await call_not_answered(call["_id"], "declined")


async def serve_presence(ws: WebSocket, contacts: list[dict]):
    """Presence and ringing for one open page: a contact link (one contact) or an account page (all of
    the account's linked contacts)."""
    by_id = {str(c["_id"]): c for c in contacts}
    for cid in by_id:
        hub.contacts[cid].add(ws)
    await ws.send_json({"type": "welcome", "contacts": list(by_id)})
    for c in contacts:
        if call := await ringing_call(c):
            patient = await db.users.find_one({"_id": call["patient_id"]}, {"name": 1})
            await ws.send_json(ring_message(call, patient, c))
    try:
        while True:
            msg = await ws.receive_json()
            kind = msg.get("type")
            if kind == "ping":
                await ws.send_json({"type": "pong"})
                continue
            contact = by_id.get(msg.get("contact_id") or (next(iter(by_id)) if len(by_id) == 1 else None))
            call = await db.calls.find_one({"_id": ms.oid(msg.get("call_id"))}) if msg.get("call_id") else None
            if not contact or not call or call["status"] != "ringing":
                continue
            if kind == "accept":
                await _accept(ws, contact, call)
            elif kind == "decline":
                await _decline(contact, call)
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("presence socket error")
    finally:
        for cid in by_id:
            hub.contacts[cid].discard(ws)


@router.websocket("/ws/contact")
async def ws_contact(ws: WebSocket):
    """Presence for an open contact link. First frame: {"type": "auth", "link_token": ...}."""
    await ws.accept()
    try:
        hello = await asyncio.wait_for(ws.receive_json(), timeout=10)
        contact = await contact_by_token(hello.get("link_token") if hello.get("type") == "auth" else None)
    except Exception:
        await ws.close(code=4403)
        return
    await serve_presence(ws, [contact])


@router.websocket("/ws/relative")
async def ws_relative(ws: WebSocket):
    """Presence for a relative's own account page. First frame: {"type": "auth", "token": <ID token>}."""
    await ws.accept()
    try:
        hello = await asyncio.wait_for(ws.receive_json(), timeout=10)
        p = await auth.principal_from_token(hello.get("token") if hello.get("type") == "auth" else None)
        if p.kind != "caregiver" or not p.contacts:
            raise HTTPException(403)
    except Exception:
        await ws.close(code=4403)
        return
    await serve_presence(ws, p.contacts)


# ---------------------------------------------------------------- what a relative sees
async def contact_view(contact: dict) -> dict:
    patient = await db.users.find_one({"_id": contact["patient_id"]})
    past = [{"id": str(c["_id"]), "started_at": c["started_at"].isoformat(), "recap": c.get("recap"),
             "duration_sec": c.get("duration_sec")}
            async for c in db.calls.find({"contact_id": contact["_id"], "status": "processed"})
            .sort("started_at", -1).limit(PAST_CALLS)]
    latest = await db.briefs.find_one({"contact_id": contact["_id"]}, sort=[("created_at", -1)])
    view = await callcard.relative_view(latest)
    updates = [{"text": u["text"], "created_at": u["created_at"].isoformat()}
               async for u in db.contact_updates.find({"contact_id": contact["_id"]}).sort("created_at", -1).limit(5)]
    call = await ringing_call(contact)
    # No "recently believed" notes here: they come from caregiver-only memories, and a page that persists
    # on a relative's account should never show those.
    return {"contact": {"id": str(contact["_id"]), "name": contact["name"], "relation": contact.get("relation"),
                        "photo": contact.get("photo"), "color": contact.get("color")},
            "patient": {"id": str(patient["_id"]), "name": patient["name"]},
            "linked": bool(contact.get("user_id")),
            "hints": await callcard.relative_hints(patient["_id"], contact["_id"]),
            "talking_points": view["talking_points"], "known": view["known"],
            "past_calls": past, "updates": updates,
            "ringing": ring_message(call, patient, contact) if call else None}


class UpdateIn(BaseModel):
    text: str


async def share(contact: dict, body: UpdateIn) -> dict:
    """"I got engaged", "My exam is on Friday": the contact speaking about themselves (trust 0.85),
    through extract, reconcile, the trust guard, sensitive-flagging and event dates."""
    text = body.text.strip()
    if len(text) < 3:
        raise HTTPException(400, "Please write a little more.")
    if len(text) > 1000:
        raise HTTPException(400, "Please keep it under 1000 characters.")
    patient = await db.users.find_one({"_id": contact["patient_id"]})
    doc = {"patient_id": patient["_id"], "contact_id": contact["_id"], "text": text, "created_at": clock.now()}
    doc["_id"] = (await db.contact_updates.insert_one(doc)).inserted_id
    result = await pipeline.process_update(contact, patient, text, doc["_id"])
    await db.contact_updates.update_one({"_id": doc["_id"]}, {"$set": {
        "mode": result["mode"], "summary": result["summary"], "actions": result["actions"]}})
    await hub.to_caregivers(str(patient["_id"]), {"type": "contact_update", "contact_id": str(contact["_id"]),
                                                  "contact_name": contact["name"], "summary": result["summary"]})
    items = []
    for a in result["actions"]:
        mem = await db.memories.find_one({"_id": ms.oid(a.get("memory_id"))}, {"visibility": 1, "status": 1}) \
            if a.get("memory_id") else None
        items.append({"text": a["content"], "action": a["action"],
                      "private": bool(mem and mem.get("visibility") == "never_surface"),
                      "review": bool(mem and mem.get("status") == "pending_review")})
    return {"saved": len(items), "items": items}


@router.get("/api/c/{token}")
async def contact_page(token: str):
    return await contact_view(await contact_by_token(token))


@router.post("/api/c/{token}/update")
async def share_update(token: str, body: UpdateIn):
    return await share(await contact_by_token(token), body)


# ---------------------------------------------------------------- relatives' own accounts
def _relative_account(p: Principal) -> Principal:
    if p.kind != "caregiver" or p.user is None:
        raise HTTPException(403, "Sign in with your own account.")
    return p


async def _my_contact(p: Principal, cid: str) -> dict:
    _relative_account(p)
    contact = next((c for c in p.contacts if str(c["_id"]) == cid), None)
    fresh = await db.contacts.find_one({"_id": contact["_id"]}) if contact else None
    if not fresh or fresh.get("user_id") != p.user["_id"] or fresh.get("status") != "active":
        raise HTTPException(404, "This person isn't on your account.")
    return fresh


class LinkIn(BaseModel):
    token: str


@router.post("/api/relative/link")
async def link_contact(body: LinkIn, p: Principal = Depends(auth.current)):
    """Keep a contact link on this account. The link's token proves who they are; after this they can
    sign in instead of keeping the link."""
    _relative_account(p)
    if "invite=" in body.token or "/login" in body.token:
        raise HTTPException(400, "That's a caregiver invite. Here you need your own personal link (it contains /c/). "
                                 "Ask the family to copy it from Contacts & rules.")
    token = body.token.strip().rstrip("/").rsplit("/c/", 1)[-1]  # accept a pasted link too
    contact = await contact_by_token(token)
    if contact.get("user_id") and contact["user_id"] != p.user["_id"]:
        raise HTTPException(409, "This person is already linked to another account. Ask the family to unlink it first.")
    await db.contacts.update_one({"_id": contact["_id"]}, {"$set": {"user_id": p.user["_id"],
                                                                    "account_linked_at": clock.now()}})
    auth.invalidate(p.uid)
    await auth.log_auth_event("relative_linked", p, contact["patient_id"], label=contact["name"])
    patient = await db.users.find_one({"_id": contact["patient_id"]}, {"name": 1})
    return {"contact_id": str(contact["_id"]), "contact_name": contact["name"], "patient_name": patient["name"]}


@router.delete("/api/relative/contacts/{cid}/link")
async def unlink_self(cid: str, p: Principal = Depends(auth.current)):
    contact = await _my_contact(p, cid)
    await db.contacts.update_one({"_id": contact["_id"]}, {"$unset": {"user_id": "", "account_linked_at": ""}})
    auth.invalidate(p.uid)
    await auth.log_auth_event("relative_unlinked", p, contact["patient_id"], label=contact["name"])
    return {"ok": True}


@router.get("/api/relative/contacts/{cid}")
async def relative_contact_page(cid: str, p: Principal = Depends(auth.current)):
    return await contact_view(await _my_contact(p, cid))


@router.post("/api/relative/contacts/{cid}/update")
async def relative_share_update(cid: str, body: UpdateIn, p: Principal = Depends(auth.current)):
    return await share(await _my_contact(p, cid), body)
