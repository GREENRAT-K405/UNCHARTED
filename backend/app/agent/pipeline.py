"""Post-call memory agent: extract -> reconcile -> write.

The decisive rule is enforced in code, not only in the prompt: a lower-trust source
can never update or supersede a higher-trust memory; that becomes a CONFLICT that
waits in the caregiver's review queue.
"""
import json
import logging
from datetime import datetime, time

from bson import ObjectId

from .. import clock, db, embeddings, lang, llm
from .. import memory_store as ms
from ..config import settings
from . import offline
from .prompts import EXTRACT_SCHEMA, EXTRACT_SYSTEM, RECONCILE_SCHEMA, RECONCILE_SYSTEM

log = logging.getLogger("pehchaan.agent")

PENDING_CONFIDENCE = 0.6


def _trust_for(speaker: str, about: str) -> tuple[float, str]:
    if speaker == "patient":
        return ms.TRUST["patient"], "patient"
    if about == "contact_self":
        return ms.TRUST["contact_self"], "contact_self"
    return ms.TRUST["contact_other"], "contact_other"


def may_override(origin: str, ctype: str, trust: float, target: dict) -> bool:
    """Trust depends on the source *and* the subject.

    - The patient's claims never replace anything stored by someone more reliable.
    - Relationships are owned by the caregiver; nobody less trusted changes them.
    - A contact is the authority on their own life: "I moved to Pune" supersedes an older
      caregiver note "lives in Mumbai" (people move house, change jobs).
    - Otherwise the source must be at least as trusted as the memory it replaces.
    """
    target_trust = target.get("trust", 0)
    if origin == "patient":
        return target_trust <= trust
    if ctype == "relationship" or target["type"] == "relationship":
        return trust >= target_trust
    if origin == "contact_self" and target["type"] in ("fact", "event", "plan", "preference"):
        return True
    return trust + 0.05 >= target_trust


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        d = datetime.strptime(value[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    # Store as local midnight, converted to UTC.
    return datetime.combine(d, time(0, 0), tzinfo=settings.tz)


async def _transcript(call_id: ObjectId) -> list[dict]:
    lines = []
    async for c in db.transcript_chunks.find({"call_id": call_id}).sort("ts", 1):
        if lines and lines[-1]["speaker"] == c["speaker"]:
            lines[-1]["text"] += " " + c["text"]
        else:
            lines.append({"speaker": c["speaker"], "text": c["text"], "ts": c["ts"]})
    return lines


async def _extract(lines, contact, patient, existing, call_date, written: bool = False) -> tuple[dict, str]:
    transcript = "\n".join(
        f"[{(patient['name'] if l['speaker'] == 'patient' else contact['name'])} ({l['speaker']})]: {l['text']}"
        for l in lines
    )
    known = "\n".join(f"- ({m['type']}, trust {m['trust']}) {m['content']}" for m in existing) or "- (nothing yet)"
    kind = (f"This is not a call: {contact['name']} wrote this update on their Pehchaan link for the family. "
            f"Every line is said by {contact['name']}.\n" if written else "")
    user = (
        f"{kind}{'Date' if written else 'Call date'}: {call_date.strftime('%A %Y-%m-%d')} (timezone {settings.timezone}).\n"
        f"Patient: {patient['name']} (language: {lang.name(patient.get('language'))}). "
        f"Contact: {contact['name']}, the patient's {contact.get('relation', 'contact')}.\n\n"
        f"Already known about {contact['name']}:\n{known}\n\n{'Update' if written else 'Transcript'}:\n{transcript}"
    )
    result = await llm.structured(EXTRACT_SYSTEM, user, EXTRACT_SCHEMA, effort="medium")
    if result is not None:
        return result, llm.status["mode"]
    return offline.extract(lines, contact, patient["name"], call_date, generic=written), "offline"


async def _reconcile(items: list[dict]) -> dict:
    if not items:
        return {"decisions": []}
    payload = [
        {
            "candidate_index": it["index"],
            "candidate": {k: it["candidate"][k] for k in ("type", "content", "speaker", "quote")},
            "candidate_trust": it["trust"],
            "existing": it["neighbours"],
        }
        for it in items
    ]
    result = await llm.structured(RECONCILE_SYSTEM, json.dumps(payload, indent=1), RECONCILE_SCHEMA, effort="medium")
    return result if result is not None else offline.reconcile(items)


async def process_call(call_id: ObjectId) -> dict:
    call = await db.calls.find_one({"_id": call_id})
    if not call:
        raise ValueError("call not found")
    contact = await db.contacts.find_one({"_id": call["contact_id"]})
    patient = await db.users.find_one({"_id": call["patient_id"]})
    await db.calls.update_one({"_id": call_id}, {"$set": {"status": "processing"}})

    lines = await _transcript(call_id)
    call_date = (call.get("started_at") or clock.now()).astimezone(settings.tz)

    if not lines:
        await db.calls.update_one({"_id": call_id}, {"$set": {
            "status": "processed", "summary": "No conversation was transcribed.",
            "recap": f"You just talked to {contact['name']}.", "agent": {"actions": []}}})
        return {"actions": []}

    extracted, mode, actions = await remember(lines, contact, patient, call_date, source_kind="call", call_id=call_id)
    await db.calls.update_one({"_id": call_id}, {"$set": {
        "status": "processed",
        "summary": extracted.get("summary", ""),
        "recap": extracted.get("patient_recap", ""),
        "processed_at": clock.now(),
        "agent": {"mode": mode, "candidates": len(extracted.get("candidates", [])), "actions": actions},
    }})
    return {"mode": mode, "actions": actions}


async def process_update(contact: dict, patient: dict, text: str, update_id: ObjectId) -> dict:
    """A written update from the contact's own link ("I got engaged"): the same extract -> reconcile ->
    trust guard -> write path as a call, with the contact as the speaker."""
    lines = [{"speaker": "contact", "text": text.strip(), "ts": clock.now()}]
    extracted, mode, actions = await remember(lines, contact, patient, clock.now().astimezone(settings.tz),
                                              source_kind="contact_update", update_id=update_id)
    return {"mode": mode, "summary": extracted.get("summary", ""), "actions": actions}


async def remember(lines: list[dict], contact: dict, patient: dict, when: datetime, *, source_kind: str,
                   call_id: ObjectId | None = None, update_id: ObjectId | None = None) -> tuple[dict, str, list[dict]]:
    """Extract candidates, find neighbours, reconcile, and apply each decision behind the trust guard."""
    owner_id = patient["_id"]
    existing = [m async for m in db.memories.find(
        {"owner_id": owner_id, "contact_id": contact["_id"], "status": "active"})]
    extracted, mode = await _extract(lines, contact, patient, existing, when, written=source_kind == "contact_update")
    candidates = extracted.get("candidates", [])

    # Find neighbours for every candidate (vector search + same-type structural facts).
    threshold = settings.reconcile_sim_threshold if embeddings.MODE == "voyage" else 0.6
    to_reconcile, fast_add = [], []
    # One batched request; the same vectors are stored with the new memories (no second embed per memory).
    cand_vectors = await embeddings.embed([c["content"] for c in candidates], "document")
    for i, (cand, vec) in enumerate(zip(candidates, cand_vectors)):
        trust, kind = _trust_for(cand["speaker"], cand["about"])
        hits = await ms.search(owner_id, contact["_id"], vec, limit=5, statuses=("active", "pending_review"))
        neighbours = {str(d["_id"]): (d, s) for d, s in hits if s >= threshold}
        if cand["type"] in ("fact", "relationship"):
            for d in existing:
                if d["type"] == cand["type"] and str(d["_id"]) not in neighbours:
                    neighbours[str(d["_id"])] = (d, embeddings.similarity(vec, d))
        item = {
            "index": i, "candidate": cand, "trust": trust, "source_kind": kind, "vector": vec,
            "neighbours": [
                {"id": mid, "type": d["type"], "content": d["content"], "trust": d.get("trust", 0.5),
                 "status": d["status"], "similarity": round(s, 3)}
                for mid, (d, s) in neighbours.items()
            ],
        }
        (to_reconcile if item["neighbours"] else fast_add).append(item)

    decisions = {d["candidate_index"]: d for d in (await _reconcile(to_reconcile))["decisions"]}
    for item in fast_add:
        decisions[item["index"]] = {"candidate_index": item["index"], "action": "ADD", "target_id": None,
                                    "merged_content": None, "reason": "no similar memory exists"}

    source = {"kind": source_kind}
    if call_id:
        source["call_id"] = call_id
    if update_id:
        source["update_id"] = update_id
    actions = []
    for item in fast_add + to_reconcile:
        decision = decisions.get(item["index"]) or {"action": "ADD", "target_id": None, "merged_content": None,
                                                    "reason": "no decision returned"}
        try:
            actions.append(await _apply(item, decision, owner_id, contact, source))
        except Exception as e:  # keep processing the rest of the candidates
            log.exception("failed to apply decision %s", decision)
            actions.append({"action": "ERROR", "content": item["candidate"]["content"], "reason": str(e)})
    return extracted, mode, actions


async def _apply(item: dict, decision: dict, owner_id: ObjectId, contact: dict, origin_source: dict) -> dict:
    cand, trust = item["candidate"], item["trust"]
    action = decision["action"]
    target = None
    if decision.get("target_id"):
        target = await db.memories.find_one({"_id": ms.oid(decision["target_id"])})
        if target is None and action != "ADD":
            action = "ADD"

    # A fact or plan never replaces (or conflicts with) who someone is: if the LLM paired a candidate with
    # the relationship memory, use the closest neighbour of the candidate's own type instead, or add it.
    if target is not None and target["type"] == "relationship" and cand["type"] != "relationship" \
            and action in ("UPDATE", "SUPERSEDE", "CONFLICT"):
        same = sorted((n for n in item["neighbours"] if n["type"] == cand["type"]), key=lambda n: -n["similarity"])
        target = await db.memories.find_one({"_id": ms.oid(same[0]["id"])}) if same else None
        decision["reason"] = f"{decision.get('reason', '')} (retargeted: not about the relationship)".strip()
        if target is None:
            action = "ADD"

    # Code-level guard, regardless of what the LLM decided.
    if target is not None and action in ("UPDATE", "SUPERSEDE") and not may_override(item["source_kind"], cand["type"], trust, target):
        action = "CONFLICT"
        decision["reason"] = f"{decision.get('reason', '')} (downgraded: source trust {trust} < {target['trust']})".strip()

    event_date = _parse_date(cand.get("event_date"))
    mtype = "sensitive" if cand.get("sensitive") else cand["type"]
    expires_at = None
    if mtype in ("event", "plan"):
        expires_at = ms.expiry_for(event_date, cand.get("expires_after_event_days"))
    elif mtype == "topic":
        expires_at = ms.expiry_for(clock.now(), 14)

    visibility = "patient"
    if mtype == "sensitive":
        visibility = "never_surface"
    elif item["source_kind"] == "patient":
        visibility = "caregiver_only"

    status = "pending_review" if cand.get("confidence", 1) < PENDING_CONFIDENCE else "active"
    if item["source_kind"] == "patient" and mtype in ("fact", "relationship"):
        status = "pending_review"  # patient claims about others are never auto-trusted

    source = {
        **origin_source, "origin": item["source_kind"], "speaker": cand["speaker"],
        "quote": cand.get("quote", "")[:300],
    }
    common = dict(owner_id=owner_id, contact_id=contact["_id"], type=mtype, source=source, trust=trust,
                  embedding=item["vector"],
                  confidence=cand.get("confidence", 0.8), importance=cand.get("importance", 0.5),
                  visibility=visibility, event_date=event_date, expires_at=expires_at)
    result = {"action": action, "content": cand["content"], "reason": decision.get("reason", ""),
              "target_id": str(target["_id"]) if target else None, "target_content": target["content"] if target else None}

    if action == "IGNORE" and target is not None:
        await db.memories.update_one({"_id": target["_id"]},
                                     {"$set": {"updated_at": clock.now()}, "$inc": {"reinforced": 1}})
        await ms.log_event(target["_id"], "reinforced", owner_id, "agent", origin_source.get("call_id"))
        result["memory_id"] = str(target["_id"])
    elif action == "UPDATE" and target is not None:
        extra = {}
        if event_date:
            extra = {"event_date": event_date, "expires_at": expires_at} if expires_at else {"event_date": event_date}
        await ms.update_content(target, decision.get("merged_content") or cand["content"], "agent", extra)
        result["memory_id"] = str(target["_id"])
    elif action == "SUPERSEDE" and target is not None:
        new = await ms.insert_memory(**common, content=cand["content"], status=status, supersedes=target["_id"])
        await ms.supersede(target, new["_id"])
        result["memory_id"] = str(new["_id"])
    elif action == "CONFLICT" and target is not None:
        new = await ms.insert_memory(**{**common, "visibility": "caregiver_only"}, content=cand["content"],
                                     status="pending_review", conflicts_with=target["_id"])
        result["memory_id"] = str(new["_id"])
    else:
        new = await ms.insert_memory(**common, content=cand["content"], status=status)
        result["action"] = "ADD"
        result["memory_id"] = str(new["_id"])
    return result
