"""Embeddings: Voyage AI when configured, otherwise a deterministic local hash embedding.

Every vector carries the name of the model that produced it (Vector.model), and every
memory stores it as `embedding_model`. Vector search only compares vectors from the
same model, so a temporary fallback (for example Voyage rate-limiting a free-tier key)
can never mix two incompatible vector spaces. A background job (reembed_stale) later
upgrades fallback vectors to the primary model.

Voyage results are cached in MongoDB (`embedding_cache`, TTL 30 days), so the same text
is never embedded twice - re-seeding the demo costs no API calls.

The local embedding (word + character-trigram feature hashing) is far weaker than a
real model but keeps the full pipeline working offline with the same dimensionality.
"""
import hashlib
import logging
import math
import re
from datetime import datetime, timezone

import numpy as np

from . import db
from .config import settings

log = logging.getLogger("pehchaan.embeddings")

LOCAL_MODEL = "local-hash-v1"

_voyage = None
if settings.voyage_api_key:
    try:
        import voyageai

        # No client-side retries: on a rate limit we fall back immediately and re-embed later.
        _voyage = voyageai.AsyncClient(api_key=settings.voyage_api_key, base_url=settings.voyage_base_url or None,
                                       max_retries=0, timeout=30)
    except Exception as e:  # pragma: no cover
        log.warning("voyage client unavailable: %s", e)

MODE = "voyage" if _voyage else "local"
PRIMARY_MODEL = f"voyage/{settings.voyage_model}" if _voyage else LOCAL_MODEL
status = {"fallbacks": 0, "cache_hits": 0, "api_calls": 0, "last_error": None}


class Vector(list):
    """A list of floats that remembers which model produced it."""

    model: str = LOCAL_MODEL

    @classmethod
    def of(cls, values, model: str) -> "Vector":
        v = cls(values)
        v.model = model
        return v


_STOP = set("a an the is are was were be to of in on at for and or with my your her his their our i you he she it this that".split())


def _features(text: str):
    words = [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOP]
    for w in words:
        yield "w:" + w, 1.0
        padded = f"#{w}#"
        for i in range(len(padded) - 2):
            yield "c:" + padded[i : i + 3], 0.35
    for a, b in zip(words, words[1:]):
        yield f"b:{a}_{b}", 0.6


def _local_embed(text: str) -> list[float]:
    vec = np.zeros(settings.embedding_dims, dtype=np.float32)
    for feat, weight in _features(text):
        h = int.from_bytes(hashlib.md5(feat.encode()).digest()[:8], "little")
        idx = h % settings.embedding_dims
        sign = 1.0 if (h >> 63) & 1 else -1.0
        vec[idx] += sign * weight
    norm = float(np.linalg.norm(vec))
    if norm > 0:
        vec /= norm
    return vec.tolist()


def _cache_key(text: str, input_type: str) -> str:
    return hashlib.sha1(f"{PRIMARY_MODEL}|{input_type}|{text}".encode()).hexdigest()


async def embed(texts: list[str], input_type: str = "document") -> list[Vector]:
    """Embed a batch. All vectors in one result share a model: either all primary or all local."""
    if not texts:
        return []
    if _voyage:
        keys = [_cache_key(t, input_type) for t in texts]
        cached = {d["_id"]: d["v"] async for d in db.embedding_cache.find({"_id": {"$in": keys}})}
        status["cache_hits"] += len(cached)
        missing = [(k, t) for k, t in zip(keys, texts) if k not in cached]
        try:
            if missing:
                status["api_calls"] += 1
                res = await _voyage.embed([t for _, t in missing], model=settings.voyage_model, input_type=input_type,
                                          output_dimension=settings.embedding_dims)
                now = datetime.now(timezone.utc)
                for (k, _), v in zip(missing, res.embeddings):
                    cached[k] = v
                    await db.embedding_cache.update_one({"_id": k}, {"$set": {"v": v, "created_at": now}}, upsert=True)
            status["last_error"] = None
            return [Vector.of(cached[k], PRIMARY_MODEL) for k in keys]
        except Exception as e:
            status["fallbacks"] += 1
            status["last_error"] = str(e)[:200]
            log.warning("voyage embed failed (%s); using local vectors for this batch, will re-embed later",
                        type(e).__name__)
    return [Vector.of(_local_embed(t), LOCAL_MODEL) for t in texts]


async def embed_one(text: str, input_type: str = "document") -> Vector:
    return (await embed([text], input_type))[0]


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    va, vb = np.asarray(a), np.asarray(b)
    denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
    if denom == 0 or math.isnan(denom):
        return 0.0
    # Map cosine [-1, 1] to [0, 1], matching Atlas vectorSearchScore for cosine.
    return (1.0 + float(va @ vb) / denom) / 2.0


NEUTRAL_SIMILARITY = 0.5  # what cosine() gives unrelated vectors; used when two vectors aren't comparable


def similarity(query: Vector, doc: dict) -> float:
    """Cosine between a query vector and a stored memory, but only within the same model."""
    if doc.get("embedding_model", LOCAL_MODEL) != query.model:
        return NEUTRAL_SIMILARITY
    return cosine(query, doc.get("embedding"))


async def reembed_stale(batch: int = 96) -> int:
    """Upgrade memories whose vectors came from a fallback model. One API request per call."""
    stale = [d async for d in db.memories.find({"embedding_model": {"$ne": PRIMARY_MODEL}}, {"content": 1}).limit(batch)]
    if not stale:
        return 0
    vectors = await embed([d["content"] for d in stale], "document")
    if vectors[0].model != PRIMARY_MODEL:
        return 0  # still rate-limited; try again later
    for d, v in zip(stale, vectors):
        await db.memories.update_one({"_id": d["_id"]}, {"$set": {"embedding": list(v), "embedding_model": v.model}})
    log.info("re-embedded %d memories with %s", len(stale), PRIMARY_MODEL)
    return len(stale)
