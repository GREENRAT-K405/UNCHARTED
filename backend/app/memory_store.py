"""Memory documents: creation, lifecycle helpers, audit log and vector retrieval."""
import logging
from datetime import datetime, timedelta
from typing import Any

from bson import ObjectId

from . import clock, db, embeddings
from .config import settings

log = logging.getLogger("pehchaan.memory")

# Set by the realtime hub when change streams are unavailable, so writes are still
# pushed live to dashboards. With change streams on, MongoDB is the event source.
fallback_emit = None


async def _emit(op: str, memory_id: ObjectId):
    if fallback_emit is not None:
        await fallback_emit(op, memory_id)

MEMORY_TYPES = ["relationship", "fact", "event", "plan", "preference", "constraint", "topic", "sensitive", "feedback"]

# Source trust: in this domain the patient is the *least* reliable source.
TRUST = {
    "caregiver": 1.0,
    "contact_self": 0.85,  # the contact talking about themselves
    "contact_other": 0.7,  # the contact talking about someone else
    "patient": 0.4,
}


def oid(value: Any) -> ObjectId | None:
    if value is None or isinstance(value, ObjectId):
        return value
    try:
        return ObjectId(str(value))
    except Exception:
        return None


def serialize(value: Any) -> Any:
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [serialize(v) for v in value]
    if isinstance(value, dict):
        return {("id" if k == "_id" else k): serialize(v) for k, v in value.items() if k != "embedding"}
    return value


def is_live(doc: dict, now: datetime | None = None) -> bool:
    """True unless the memory has expired on the demo clock (TTL uses the real clock)."""
    exp = doc.get("expires_at")
    return exp is None or exp > (now or clock.now())


async def log_event(memory_id: ObjectId, action: str, owner_id: ObjectId, actor: str, related_id: ObjectId | None = None):
    # Ids and action only - never content - so "forget" really forgets.
    await db.memory_events.insert_one(
        {"memory_id": memory_id, "owner_id": owner_id, "action": action, "actor": actor,
         "related_id": related_id, "ts": clock.now()}
    )


async def insert_memory(
    *,
    owner_id: ObjectId,
    contact_id: ObjectId | None,
    type: str,
    content: str,
    source: dict,
    trust: float,
    confidence: float = 0.9,
    importance: float = 0.5,
    status: str = "active",
    visibility: str = "patient",
    event_date: datetime | None = None,
    expires_at: datetime | None = None,
    supersedes: ObjectId | None = None,
    rule: dict | None = None,
    conflicts_with: ObjectId | None = None,
    actor: str = "agent",
    embedding: "embeddings.Vector | None" = None,
) -> dict:
    now = clock.now()
    vec = embedding if embedding is not None else await embeddings.embed_one(content)
    doc = {
        "owner_id": owner_id,
        "contact_id": contact_id,
        "type": type,
        "content": content,
        "embedding": list(vec),
        "embedding_model": getattr(vec, "model", embeddings.LOCAL_MODEL),
        "source": source,
        "trust": round(trust, 2),
        "confidence": round(confidence, 2),
        "importance": round(importance, 2),
        "status": status,
        "visibility": visibility,
        "supersedes": supersedes,
        "superseded_by": None,
        "conflicts_with": conflicts_with,
        "version": 1,
        "event_date": event_date,
        "created_at": now,
        "updated_at": now,
        "last_retrieved_at": None,
        "retrieval_count": 0,
        "feedback": {"helpful": 0, "not_helpful": 0},
    }
    if rule:
        doc["rule"] = rule
    if expires_at:
        doc["expires_at"] = expires_at
    res = await db.memories.insert_one(doc)
    doc["_id"] = res.inserted_id
    await log_event(res.inserted_id, "created", owner_id, actor, supersedes or conflicts_with)
    await _emit("insert", res.inserted_id)
    return doc


async def supersede(old: dict, new_id: ObjectId, actor: str = "agent"):
    await db.memories.update_one(
        {"_id": old["_id"]},
        {"$set": {"status": "superseded", "superseded_by": new_id, "updated_at": clock.now()}},
    )
    await log_event(old["_id"], "superseded", old["owner_id"], actor, new_id)
    await _emit("update", old["_id"])


async def update_content(mem: dict, content: str, actor: str, extra: dict | None = None):
    vec = await embeddings.embed_one(content)
    sets = {"content": content, "embedding": list(vec), "embedding_model": vec.model, "updated_at": clock.now()}
    sets.update(extra or {})
    await db.memories.update_one({"_id": mem["_id"]}, {"$set": sets, "$inc": {"version": 1}})
    await log_event(mem["_id"], "updated" if actor == "agent" else "corrected", mem["owner_id"], actor)
    await _emit("update", mem["_id"])


async def set_fields(mem: dict, fields: dict, action: str, actor: str):
    await db.memories.update_one({"_id": mem["_id"]}, {"$set": {**fields, "updated_at": clock.now()}})
    await log_event(mem["_id"], action, mem["owner_id"], actor)
    await _emit("update", mem["_id"])


async def forget(mem: dict, actor: str):
    await db.memories.delete_one({"_id": mem["_id"]})
    await log_event(mem["_id"], "deleted", mem["owner_id"], actor)
    await _emit("delete", mem["_id"])


def expiry_for(event_date: datetime | None, days_after: float | None) -> datetime | None:
    if event_date is None:
        return None
    return event_date + timedelta(days=days_after if days_after is not None else 2)


async def search(
    owner_id: ObjectId,
    contact_id: ObjectId | None,
    query_vector: "embeddings.Vector",
    *,
    limit: int = 10,
    statuses: tuple[str, ...] = ("active",),
    visibility: str | None = None,
) -> list[tuple[dict, float]]:
    """Semantic search over one patient's memories about one contact.

    Uses Atlas $vectorSearch with pre-filters when available and falls back to
    an exact in-app cosine scan (fine at hackathon scale) otherwise. Only memories embedded
    by the same model as the query are compared.
    """
    filters: list[dict] = [{"owner_id": owner_id}, {"status": {"$in": list(statuses)}},
                           {"embedding_model": getattr(query_vector, "model", embeddings.LOCAL_MODEL)}]
    if contact_id is not None:
        filters.append({"contact_id": contact_id})
    if visibility:
        filters.append({"visibility": visibility})

    if db.capabilities["vector_search"]:
        pipeline = [
            {"$vectorSearch": {
                "index": settings.vector_index_name,
                "path": "embedding",
                "queryVector": list(query_vector),
                "numCandidates": max(100, limit * 10),
                "limit": limit,
                "filter": {"$and": filters},
            }},
            {"$addFields": {"score": {"$meta": "vectorSearchScore"}}},
        ]
        try:
            out = []
            async for doc in db.memories.aggregate(pipeline):
                if is_live(doc):
                    out.append((doc, float(doc.pop("score"))))
            return out
        except Exception as e:
            log.warning("$vectorSearch failed, using in-app cosine: %s", e)

    scored = []
    async for doc in db.memories.find({"$and": filters}):
        if is_live(doc):
            scored.append((doc, embeddings.cosine(query_vector, doc.get("embedding"))))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:limit]
