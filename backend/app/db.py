import asyncio
import logging

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING
from pymongo.errors import OperationFailure
from pymongo.operations import SearchIndexModel

from .config import settings

log = logging.getLogger("pehchaan.db")

client: AsyncIOMotorClient = AsyncIOMotorClient(settings.mongodb_uri, tz_aware=True)
db: AsyncIOMotorDatabase = client[settings.mongodb_db]

users = db.users
contacts = db.contacts
calls = db.calls
transcript_chunks = db.transcript_chunks
memories = db.memories
briefs = db.briefs
memory_events = db.memory_events
# Auth & care circles. Firebase only proves identity; who may see which patient lives here.
memberships = db.memberships  # caregiver user <-> patient, with a role
devices = db.devices  # paired patient tablets (anonymous Firebase uid -> patient)
pairing_codes = db.pairing_codes  # short-lived codes shown on an unpaired tablet (TTL)
invites = db.invites  # family invite codes (TTL)
auth_events = db.auth_events  # sign-ins, pairings, joins (TTL, 90 days)
meets = db.meets  # scheduled meets the caretaker plans for the patient
embedding_cache = db.embedding_cache  # model+text hash -> vector, so nothing is embedded twice (TTL, 30 days)

# Filled in by ensure_indexes(): tells the rest of the app which Atlas features work.
capabilities = {"vector_search": False, "change_streams": False, "pre_images": False}

COLLECTIONS = ["users", "contacts", "calls", "transcript_chunks", "memories", "briefs", "memory_events",
               "memberships", "devices", "pairing_codes", "invites", "auth_events", "embedding_cache", "meets"]


async def ensure_indexes() -> None:
    existing = set(await db.list_collection_names())
    for name in COLLECTIONS:
        if name not in existing:
            await db.create_collection(name)

    # TTL: time-bound memories are deleted by MongoDB once expires_at passes.
    # Documents without expires_at never expire.
    await memories.create_index([("expires_at", ASCENDING)], expireAfterSeconds=0, name="memory_ttl")
    await transcript_chunks.create_index(
        [("created_at", ASCENDING)], expireAfterSeconds=settings.transcript_ttl_seconds, name="transcript_ttl"
    )

    await memories.create_index(
        [("owner_id", ASCENDING), ("contact_id", ASCENDING), ("status", ASCENDING), ("type", ASCENDING)]
    )
    await calls.create_index([("patient_id", ASCENDING), ("contact_id", ASCENDING), ("started_at", DESCENDING)])
    await transcript_chunks.create_index([("call_id", ASCENDING), ("ts", ASCENDING)])
    await briefs.create_index([("call_id", ASCENDING), ("created_at", DESCENDING)])
    await memory_events.create_index([("memory_id", ASCENDING), ("ts", ASCENDING)])
    await memory_events.create_index([("owner_id", ASCENDING), ("ts", DESCENDING)])

    await users.create_index("firebase_uid", unique=True, sparse=True)
    await memberships.create_index([("patient_id", ASCENDING), ("user_id", ASCENDING)], unique=True)
    await memberships.create_index("user_id")
    await devices.create_index("firebase_uid", unique=True)
    await devices.create_index("patient_id")
    await pairing_codes.create_index("code", unique=True)
    await pairing_codes.create_index([("expires_at", ASCENDING)], expireAfterSeconds=0, name="pairing_ttl")
    await invites.create_index("code", unique=True)
    await invites.create_index([("expires_at", ASCENDING)], expireAfterSeconds=0, name="invite_ttl")
    await auth_events.create_index([("patient_id", ASCENDING), ("ts", DESCENDING)])
    await auth_events.create_index([("ts", ASCENDING)], expireAfterSeconds=90 * 24 * 3600, name="auth_events_ttl")
    await calls.create_index([("patient_id", ASCENDING), ("started_at", DESCENDING)])
    await embedding_cache.create_index([("created_at", ASCENDING)], expireAfterSeconds=30 * 24 * 3600, name="embed_cache_ttl")
    await memories.create_index("embedding_model")
    await meets.create_index([("patient_id", ASCENDING), ("scheduled_at", ASCENDING)])
    await calls.create_index([("patient_id", ASCENDING), ("status", ASCENDING)])
    await calls.create_index("code", unique=True, sparse=True)
    await calls.create_index("invited")
    await contacts.create_index([("patient_id", ASCENDING), ("status", ASCENDING)])
    await contacts.create_index("email", sparse=True)
    await users.create_index("login_email", sparse=True)

    # Pre-images let the dashboard show *what* expired when TTL deletes a memory.
    try:
        await db.command("collMod", "memories", changeStreamPreAndPostImages={"enabled": True})
        capabilities["pre_images"] = True
    except OperationFailure as e:
        log.warning("change stream pre-images unavailable: %s", e)

    await _ensure_vector_index()


async def _ensure_vector_index() -> None:
    definition = {
        "fields": [
            {"type": "vector", "path": "embedding", "numDimensions": settings.embedding_dims, "similarity": "cosine"},
            {"type": "filter", "path": "owner_id"},
            {"type": "filter", "path": "contact_id"},
            {"type": "filter", "path": "type"},
            {"type": "filter", "path": "status"},
            {"type": "filter", "path": "visibility"},
            {"type": "filter", "path": "created_at"},
            {"type": "filter", "path": "embedding_model"},  # never compare vectors from different models
        ]
    }
    try:
        existing = {ix["name"]: ix async for ix in memories.list_search_indexes()}
        current = existing.get(settings.vector_index_name)
        if current is None:
            await memories.create_search_index(
                SearchIndexModel(definition=definition, name=settings.vector_index_name, type="vectorSearch")
            )
            log.info("created vector index %s", settings.vector_index_name)
        else:
            paths = {f.get("path") for f in (current.get("latestDefinition") or {}).get("fields", [])}
            if "embedding_model" not in paths:
                await memories.update_search_index(settings.vector_index_name, definition)
                log.info("updated vector index %s with an embedding_model filter", settings.vector_index_name)
    except Exception as e:  # plain mongod has no search support
        log.warning("Atlas Vector Search unavailable, using in-app cosine fallback: %s", e)
        return
    # A new index takes a few seconds to build; until it is queryable, retrieval uses the cosine fallback.
    asyncio.create_task(_await_queryable())


async def _await_queryable() -> None:
    for _ in range(120):
        try:
            async for ix in memories.list_search_indexes(settings.vector_index_name):
                if ix.get("queryable"):
                    capabilities["vector_search"] = True
                    log.info("vector index %s is queryable", settings.vector_index_name)
                    return
        except Exception:
            pass
        await asyncio.sleep(1)
    log.warning("vector index never became queryable; staying on cosine fallback")
