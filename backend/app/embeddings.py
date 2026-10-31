"""Embeddings: Voyage AI when configured, otherwise a deterministic local hash embedding.

The local embedding (word + character-trigram feature hashing) is far weaker than a
real model but keeps the full pipeline (vector index, $vectorSearch, re-ranking)
working offline with the same dimensionality.
"""
import hashlib
import logging
import math
import re

import httpx
import numpy as np

from .config import settings

log = logging.getLogger("pehchaan.embeddings")

# Plain HTTP so the base URL is configurable: Voyage keys use api.voyageai.com, keys created in
# MongoDB Atlas (Model API Keys) use ai.mongodb.com. Both speak the same embeddings API.
_http = httpx.AsyncClient(timeout=30) if settings.voyage_api_key else None

MODE = "voyage" if _http else "local"

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


async def embed(texts: list[str], input_type: str = "document") -> list[list[float]]:
    if not texts:
        return []
    if _http:
        try:
            res = await _http.post(
                f"{settings.voyage_base_url.rstrip('/')}/embeddings",
                headers={"Authorization": f"Bearer {settings.voyage_api_key}"},
                json={"input": texts, "model": settings.voyage_model, "input_type": input_type,
                      "output_dimension": settings.embedding_dims},
            )
            res.raise_for_status()
            return [d["embedding"] for d in sorted(res.json()["data"], key=lambda d: d["index"])]
        except Exception as e:
            log.warning("voyage embed failed, falling back to local: %s", e)
    return [_local_embed(t) for t in texts]


async def embed_one(text: str, input_type: str = "document") -> list[float]:
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
