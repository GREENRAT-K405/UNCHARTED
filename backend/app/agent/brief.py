"""Pre-call brief: hybrid retrieval (deterministic + semantic), explainable re-ranking, composition.

Every candidate's score breakdown is saved on the brief so the Memory Inspector can
show exactly why each memory was (or was not) selected.
"""
import asyncio
import json
import math
from datetime import timedelta

from bson import ObjectId

from .. import callcard, clock, db, embeddings, lang, llm
from .. import memory_store as ms
from ..config import settings
from . import offline
from .prompts import BRIEF_SCHEMA, BRIEF_SYSTEM

W_SIM, W_REC, W_IMP, W_TRUST = 0.5, 0.2, 0.15, 0.15
OPEN_LOOP_BOOST = 0.3
OPEN_LOOP_DAYS = 3
MAX_ITEMS = 6
RECENT_DAYS = 21
RECENT_TYPES = ("topic", "event", "plan", "preference")


def _recency(doc, now) -> float:
    days = max(0.0, (now - doc["created_at"]).total_seconds() / 86400)
    return math.exp(-days / 14)


def _open_loop(doc, now) -> bool:
    ed = doc.get("event_date")
    return doc["type"] in ("event", "plan") and ed is not None and abs((ed - now).total_seconds()) <= OPEN_LOOP_DAYS * 86400


async def build_brief(owner_id: ObjectId, contact: dict, call_id: ObjectId | None = None, *, compose: bool = True) -> dict:
    now = clock.now()
    cid = contact["_id"]
    last_calls = [c async for c in db.calls.find(
        {"patient_id": owner_id, "contact_id": cid, "status": "processed"}).sort("started_at", -1).limit(2)]

    query = f"things to talk about with {contact['name']}"
    if last_calls and last_calls[0].get("recap"):
        query += ". " + last_calls[0]["recap"]
    qvec = await embeddings.embed_one(query, "query")

    pool: dict[str, dict] = {}

    def add(doc, reason, sim=None):
        key = str(doc["_id"])
        entry = pool.setdefault(key, {"doc": doc, "reasons": [], "sim": None})
        if reason not in entry["reasons"]:
            entry["reasons"].append(reason)
        if sim is not None:
            entry["sim"] = sim

    base = {"owner_id": owner_id, "contact_id": cid, "status": "active", "visibility": "patient"}
    # A. deterministic: who they are and core facts
    async for d in db.memories.find({**base, "type": {"$in": ["relationship", "fact"]}}):
        if ms.is_live(d, now):
            add(d, "core fact")
    # C. open loops: events/plans close to today
    async for d in db.memories.find({**base, "type": {"$in": ["event", "plan"]}}):
        if ms.is_live(d, now) and _open_loop(d, now):
            add(d, "open loop")
    # D. semantic: $vectorSearch with owner/contact/status/visibility pre-filters
    for d, score in await ms.search(owner_id, cid, qvec, limit=10, visibility="patient"):
        add(d, "semantic match", score)
    # D2. recent conversation: the same search, also pre-filtered on the index's type and created_at fields
    for d, score in await ms.search(owner_id, cid, qvec, limit=5, visibility="patient", types=RECENT_TYPES,
                                    created_after=now - timedelta(days=RECENT_DAYS)):
        add(d, "recent topic", score)

    # E. guardrails: never shown, used to keep the composer away from them
    guard_docs = [d async for d in db.memories.find({
        "owner_id": owner_id, "status": "active", "type": {"$in": ["sensitive", "constraint"]},
        "$or": [{"contact_id": cid}, {"contact_id": None}]})]

    items = []
    for key, entry in pool.items():
        d = entry["doc"]
        sim = entry["sim"] if entry["sim"] is not None else embeddings.similarity(qvec, d)
        rec = _recency(d, now)
        boost = OPEN_LOOP_BOOST if _open_loop(d, now) else 0.0
        fb = d.get("feedback") or {}
        importance = max(0.0, min(1.0, d.get("importance", 0.5) + 0.05 * fb.get("helpful", 0) - 0.1 * fb.get("not_helpful", 0)))
        final = W_SIM * sim + W_REC * rec + W_IMP * importance + W_TRUST * d.get("trust", 0.5) + boost
        items.append({
            "memory_id": d["_id"], "content": d["content"], "type": d["type"],
            "event_date": d.get("event_date"),
            "similarity": round(sim, 3), "recency": round(rec, 3), "importance": round(importance, 3),
            "trust": d.get("trust", 0.5), "boost": boost, "final": round(final, 3),
            "reason": ", ".join(entry["reasons"]), "selected": False,
        })
    items.sort(key=lambda x: x["final"], reverse=True)

    # The relationship memory is always shown (recognition over recall); then the best-scoring rest.
    selected = [i for i in items if i["type"] == "relationship"][:1]
    for i in items:
        if len(selected) >= MAX_ITEMS:
            break
        if i not in selected:
            selected.append(i)
    for i in selected:
        i["selected"] = True

    patient = await db.users.find_one({"_id": owner_id}, {"name": 1, "language": 1})
    language = lang.normalise((patient or {}).get("language"))
    # Repeated "who is this?" on recent calls: lead with clearer identity information.
    identity_first = await callcard.identity_boosted(cid)
    card = None
    mode = "offline"
    view_items = [{"id": str(i["memory_id"]), "type": i["type"], "content": i["content"],
                   "event_date": i["event_date"]} for i in selected]
    if compose:
        user = json.dumps({
            "today": now.astimezone(settings.tz).strftime("%A %Y-%m-%d"),
            "patient_language": lang.name(language),
            "identity_first": identity_first,
            "contact": {"name": contact["name"], "known_as": callcard.known_as(contact),
                        "relation": contact.get("relation"), "relation_detail": contact.get("relation_detail"),
                        "nicknames": contact.get("nicknames", [])},
            "memories": [{**v, "event_date": v["event_date"].astimezone(settings.tz).strftime("%A %Y-%m-%d")
                          if v["event_date"] else None} for v in view_items],
            # The patient-facing recap, not the family summary: it never contains private items.
            "last_calls": [{"when": c["started_at"].astimezone(settings.tz).strftime("%A %d %b"),
                            "recap": c.get("recap", "")} for c in last_calls],
            "guardrails": [g["content"] for g in guard_docs if g["type"] == "sensitive"],
        }, indent=1)
        try:
            # A slow LLM must never hold up the call: after the timeout the template brief is used.
            card = await asyncio.wait_for(llm.structured(BRIEF_SYSTEM, user, BRIEF_SCHEMA, effort="low", max_tokens=4000),
                                          timeout=settings.brief_llm_timeout)
        except asyncio.TimeoutError:
            card = None
        if card is not None:
            mode = llm.status["mode"]
    if card is None:
        card = offline.brief(contact, view_items, last_calls, now, patient["name"] if patient else "",
                             identity_first=identity_first)

    doc = {
        "owner_id": owner_id, "contact_id": cid, "call_id": call_id, "created_at": now, "mode": mode,
        "query": query, "card": card, "items": items,
        "weights": {"similarity": W_SIM, "recency": W_REC, "importance": W_IMP, "trust": W_TRUST,
                    "open_loop_boost": OPEN_LOOP_BOOST},
        "guardrail_ids": [g["_id"] for g in guard_docs],
        "last_call_ids": [c["_id"] for c in last_calls],
        "language": language, "identity_first": identity_first,
        # Shown only on the relative's side, never to the patient or the composer.
        "relative_hints": await callcard.relative_hints(owner_id, cid),
    }
    res = await db.briefs.insert_one(doc)
    doc["_id"] = res.inserted_id
    sel_ids = [i["memory_id"] for i in selected]
    if sel_ids:
        await db.memories.update_many({"_id": {"$in": sel_ids}},
                                      {"$set": {"last_retrieved_at": now}, "$inc": {"retrieval_count": 1}})
    if call_id:
        await db.calls.update_one({"_id": call_id}, {"$set": {"brief_id": res.inserted_id}})
    return doc


async def receiver_note(owner_id: ObjectId, contact: dict) -> list[str]:
    """What the patient has recently believed or asked about this contact - helps family keep stories consistent."""
    since = clock.now() - timedelta(days=7)
    notes = []
    async for d in db.memories.find({
        "owner_id": owner_id, "contact_id": contact["_id"], "source.origin": "patient",
        "status": {"$in": ["pending_review", "active"]}, "visibility": {"$ne": "never_surface"},
        "type": {"$ne": "sensitive"}, "created_at": {"$gte": since}}).sort("created_at", -1).limit(4):
        notes.append(f"Recently believed: \"{d['content']}\"")
    return notes
