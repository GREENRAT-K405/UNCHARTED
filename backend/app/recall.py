"""Live recall cards: while the relative talks, surface one matching memory on the patient's screen.

Every ~10 s the last ~30 s of the relative's (stored) transcript is embedded and searched with
$vectorSearch, pre-filtered to this patient, this contact, status active and visibility patient.
Above a similarity threshold the patient sees a small card for ~10 s. At most one card per 60 s,
never the same memory twice in a call. Embeddings only, no LLM; embed() uses the embedding cache.
With the local fallback embedding the match is effectively a keyword match. Shown cards are logged
on the call document (`recall_cards`) so caregivers can see them.
"""
import asyncio
import logging
import time
from collections import deque

from bson import ObjectId

from . import callcard, clock, db, embeddings
from . import memory_store as ms
from .config import settings

log = logging.getLogger("pehchaan.recall")

WINDOW = 30.0
MIN_GAP = 60.0
TYPES = ("fact", "event", "plan", "preference", "topic")

_buffers: dict[str, deque] = {}
_loops: dict[str, asyncio.Task] = {}


def on_contact_chunk(call_id: str, text: str, hub) -> None:
    """Called for every stored chunk the relative says. Starts the call's loop on first use."""
    _buffers.setdefault(call_id, deque(maxlen=60)).append((time.monotonic(), text))
    task = _loops.get(call_id)
    if task is None or task.done():
        _loops[call_id] = asyncio.create_task(_loop(call_id, hub))


def threshold() -> float:
    return settings.recall_threshold if embeddings.MODE == "voyage" else settings.recall_threshold_local


async def _loop(call_id: str, hub):
    shown: set[str] = set()
    last_card = -MIN_GAP
    last_text = ""
    try:
        call = await db.calls.find_one({"_id": ObjectId(call_id)})
        if not call:
            return
        # The patient's own card already shows the brief's top memory.
        if call.get("brief_id") and (brief := await db.briefs.find_one({"_id": call["brief_id"]})):
            top = await callcard.top_memory(brief, "")
            if top:
                shown.add(top["id"])
        patient = await db.users.find_one({"_id": call["patient_id"]}, {"name": 1})
        while True:
            await asyncio.sleep(settings.recall_tick_seconds)
            call = await db.calls.find_one({"_id": ObjectId(call_id)}, {"status": 1, "patient_id": 1, "contact_id": 1})
            if not call or call["status"] not in ("ringing", "active") or call_id not in hub.rooms:
                return
            now = time.monotonic()
            buf = _buffers.get(call_id) or deque()
            text = " ".join(t for ts, t in buf if now - ts <= WINDOW)
            if not text or text == last_text or now - last_card < MIN_GAP or "patient" not in hub.rooms[call_id]:
                continue
            last_text = text
            # The whole window and its last few lines, in one batched request: a window that mixes two
            # topics dilutes both, while a single line alone can be too short.
            lines = [t for ts, t in buf if now - ts <= WINDOW][-3:]
            queries = list(dict.fromkeys([text] + lines))
            best = None
            for vec in await embeddings.embed(queries, "query"):
                hits = await ms.search(call["patient_id"], call["contact_id"], vec, limit=4, visibility="patient",
                                       types=TYPES)
                hit = next(((d, s) for d, s in hits if str(d["_id"]) not in shown and s >= threshold()), None)
                if hit and (best is None or hit[1] > best[1]):
                    best = (*hit, vec.model)
            if not best:
                continue
            doc, score, model = best
            shown.add(str(doc["_id"]))
            last_card = now
            card = {"memory_id": str(doc["_id"]), "text": callcard.you_text(doc["content"], patient.get("name", "")),
                    "score": round(score, 3)}
            await db.calls.update_one({"_id": call["_id"]}, {"$push": {"recall_cards": {
                "memory_id": doc["_id"], "content": doc["content"], "score": round(score, 3),
                "embedding_model": model, "ts": clock.now()}}})
            await hub.to_room(call_id, {"type": "recall", "card": card}, only="patient")
    except asyncio.CancelledError:
        return
    except Exception:
        log.exception("recall loop failed for call %s", call_id)
    finally:
        _buffers.pop(call_id, None)
        _loops.pop(call_id, None)
