"""What each side of a call sees, built from the stored brief.

Patient: a short "Who is this?" card: who (the name they use, the relation from their point
of view, the stored photo), at most one memory (the top-ranked non-relationship item of the
brief) and who else is in the room. Never a talking point, guardrail or hint.

Relative: talking points, what the patient's card can mention, receiver notes and the
family's hints (e.g. how to answer when the patient asks for someone who has died).

The stored brief and its score breakdown are unchanged; only presentation lives here.
"""
from datetime import timedelta

from bson import ObjectId
from pymongo import ReturnDocument

from . import clock, db, lang
from . import memory_store as ms

IDENTITY_WINDOW = timedelta(days=30)  # how long repeated "who is this?" keeps the extra identity detail
SKIP_TYPES = ("relationship", "constraint", "sensitive")


def known_as(contact: dict) -> str:
    """The name the patient uses for them ("Lata tai"), else their name."""
    return (contact.get("known_as") or "").strip() or contact["name"]


def your_relation(contact: dict) -> str:
    return f"your {contact.get('relation') or 'family'}"


def you_text(text: str, patient_name: str) -> str:
    """Third person about the patient -> second person ("Riya will visit Aaji" -> "... visit you")."""
    if patient_name:
        text = text.replace(f"{patient_name}'s", "your").replace(f" {patient_name}", " you")
    text = text.strip()
    return text if text.endswith((".", "!", "?", "।")) else text + "."


async def identity_boosted(contact_id: ObjectId) -> bool:
    rel = await db.memories.find_one({"contact_id": contact_id, "type": "relationship", "status": "active"},
                                     {"identity_asks": 1, "last_identity_ask_at": 1})
    last = (rel or {}).get("last_identity_ask_at")
    return bool(last and clock.now() - last <= IDENTITY_WINDOW)


async def who(contact: dict | None) -> dict | None:
    """The name tag. None (no overlay at all) unless the contact is a real, active person."""
    if not contact or contact.get("status") != "active":
        return None
    boosted = await identity_boosted(contact["_id"])
    return {"contact_id": str(contact["_id"]), "name": known_as(contact), "full_name": contact["name"],
            "relation": your_relation(contact), "photo": contact.get("photo"), "color": contact.get("color"),
            "detail": contact.get("relation_detail") if boosted else None}


async def also_here(call: dict) -> list[dict]:
    ids = call.get("also_here") or []
    if not ids:
        return []
    found = {c["_id"]: c async for c in db.contacts.find({"_id": {"$in": ids}, "status": "active"})}
    return [{"id": str(i), "name": known_as(found[i]), "relation": your_relation(found[i])} for i in ids if i in found]


async def top_memory(brief: dict, patient_name: str) -> dict | None:
    """The top-ranked non-relationship item, re-checked: it must still be active and patient-visible."""
    lines = (brief.get("card") or {}).get("lines", [])
    for item in sorted(brief.get("items", []), key=lambda i: i["final"], reverse=True)[:8]:
        if item["type"] in SKIP_TYPES:
            continue
        doc = await db.memories.find_one({"_id": item["memory_id"], "status": "active", "visibility": "patient"},
                                         {"content": 1, "type": 1, "expires_at": 1})
        if not doc or not ms.is_live(doc):
            continue
        # Prefer the composer's wording when a line is about this memory alone (and in the patient's language).
        line = next((l for l in lines if l.get("memory_ids") == [str(doc["_id"])]), None)
        return {"id": str(doc["_id"]), "type": doc["type"],
                "text": line["text"] if line else you_text(doc["content"], patient_name)}
    return None


async def patient_card(brief: dict | None, contact: dict | None, patient: dict | None, call: dict | None = None) -> dict:
    tag = await who(contact)
    if tag is None:
        return {"who": None, "memory": None, "also_here": []}
    memory = None
    if brief:
        rel = (brief.get("card") or {}).get("relation_phrase")
        if rel and lang.normalise((patient or {}).get("language")) != lang.DEFAULT:
            tag["relation"] = rel
        memory = await top_memory(brief, (patient or {}).get("name", ""))
    return {"who": tag, "memory": memory, "also_here": await also_here(call) if call else [],
            "brief_id": str(brief["_id"]) if brief else None,
            "updated_at": brief["created_at"].isoformat() if brief else None,
            "language": lang.normalise((patient or {}).get("language"))}


async def relative_hints(owner_id: ObjectId, exclude_contact: ObjectId | None = None) -> list[dict]:
    """The family's notes on archived people ("If Aaji asks for Baba..."). For relatives only."""
    q = {"patient_id": owner_id, "status": "archived", "archive_note": {"$nin": [None, ""]}}
    return [{"name": c["name"], "relation": c.get("relation"), "note": c["archive_note"]}
            async for c in db.contacts.find(q) if c["_id"] != exclude_contact]


async def relative_view(brief: dict | None) -> dict:
    if not brief:
        return {"talking_points": [], "known": [], "hints": []}
    selected = [i for i in brief.get("items", []) if i.get("selected") and i["type"] not in SKIP_TYPES]
    # The brief may be older than a caregiver's edit: only what is still active and patient-visible.
    visible = {d["_id"] async for d in db.memories.find(
        {"_id": {"$in": [i["memory_id"] for i in selected]}, "status": "active", "visibility": "patient"}, {"_id": 1})}
    selected = [i for i in selected if i["memory_id"] in visible]
    return {"talking_points": (brief.get("card") or {}).get("suggestions", []),
            "known": [{"id": str(i["memory_id"]), "text": i["content"]} for i in selected],
            "hints": brief.get("relative_hints", [])}


# ---------------------------------------------------------------- overlay events on the call document
OVERLAY_FIELDS = {"shown": "shown", "button": "button", "voice": "voice", "also_here": "also_here_changes"}


async def record_overlay(call_id: ObjectId, kind: str) -> dict | None:
    """Count and time-stamp overlay events. A second "who is this?" in one call reinforces identity."""
    if kind not in OVERLAY_FIELDS:
        return None
    call = await db.calls.find_one_and_update(
        {"_id": call_id},
        {"$inc": {f"overlay.{OVERLAY_FIELDS[kind]}": 1},
         "$push": {"overlay.events": {"$each": [{"kind": kind, "ts": clock.now()}], "$slice": -100}}},
        return_document=ReturnDocument.AFTER)
    if call and kind in ("button", "voice"):
        ov = call.get("overlay", {})
        if ov.get("button", 0) + ov.get("voice", 0) == 2:  # exactly once per call
            await reinforce_identity(call)
    return call


async def reinforce_identity(call: dict):
    """Slightly raise the relationship memory's importance and mark it, so the next brief (and the
    name tag) lead with clearer identity information. Logged in memory_events."""
    rel = await db.memories.find_one({"owner_id": call["patient_id"], "contact_id": call["contact_id"],
                                      "type": "relationship", "status": "active"})
    if not rel:
        return
    await ms.set_fields(rel, {"importance": round(min(1.0, rel.get("importance", 0.9) + 0.05), 2),
                              "identity_asks": rel.get("identity_asks", 0) + 1,
                              "last_identity_ask_at": clock.now()}, "identity_asked", "patient")
