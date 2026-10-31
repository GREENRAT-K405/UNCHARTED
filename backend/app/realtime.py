"""Realtime hub: WebRTC signaling + transcript streaming per call, and the change-stream relay.

MongoDB change streams on `memories` and `calls` are the event source for the caregiver
dashboard and for refreshing the patient's overlay mid-call when a memory changes.
"""
import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timezone

from bson import ObjectId
from fastapi import WebSocket

from . import db
from . import memory_store as ms

log = logging.getLogger("pehchaan.realtime")


class Hub:
    def __init__(self):
        self.rooms: dict[str, dict[str, WebSocket]] = defaultdict(dict)  # call_id -> role -> ws
        self.caregivers: dict[str, set[WebSocket]] = defaultdict(set)  # patient_id -> sockets
        self._brief_tasks: dict[str, asyncio.Task] = {}
        self._watchers: list[asyncio.Task] = []
        self.refresh_brief = None  # set by main: async (call_id) -> card payload

    # ---- sending -------------------------------------------------------
    async def _send(self, ws: WebSocket, msg: dict):
        try:
            await ws.send_json(msg)
        except Exception:
            pass

    async def to_room(self, call_id: str, msg: dict, exclude: str | None = None, only: str | None = None):
        for role, ws in list(self.rooms.get(call_id, {}).items()):
            if role != exclude and (only is None or role == only):
                await self._send(ws, msg)

    async def to_caregivers(self, patient_id: str, msg: dict):
        for ws in list(self.caregivers.get(patient_id, set())):
            await self._send(ws, msg)

    # ---- live brief refresh -------------------------------------------
    def schedule_brief_refresh(self, contact_id: str):
        """Debounced: when a memory about this contact changes, rebuild the brief of any live call with them."""
        for call_id, members in self.rooms.items():
            if "patient" not in members:
                continue
            key = f"{call_id}:{contact_id}"
            task = self._brief_tasks.get(key)
            if task and not task.done():
                task.cancel()
            self._brief_tasks[key] = asyncio.create_task(self._refresh(call_id, contact_id))

    async def _refresh(self, call_id: str, contact_id: str):
        await asyncio.sleep(1.5)
        call = await db.calls.find_one({"_id": ObjectId(call_id)})
        if not call or str(call["contact_id"]) != contact_id or call.get("status") not in ("ringing", "active"):
            return
        if self.refresh_brief:
            payload = await self.refresh_brief(call)
            await self.to_room(call_id, {"type": "brief", "brief": payload, "reason": "memory updated"}, only="patient")

    # ---- change streams -------------------------------------------------
    async def start(self):
        try:
            # Probe: fails fast on a standalone mongod without a replica set.
            async with db.memories.watch(max_await_time_ms=1) as probe:
                await probe.try_next()
            db.capabilities["change_streams"] = True
            self._watchers.append(asyncio.create_task(self._watch_memories()))
            self._watchers.append(asyncio.create_task(self._watch_calls()))
            log.info("change streams active")
        except Exception as e:
            log.warning("change streams unavailable, emitting events from app code: %s", e)
            ms.fallback_emit = self._fallback_memory_event

    async def stop(self):
        for t in self._watchers:
            t.cancel()

    async def _watch_memories(self):
        opts = {"full_document": "updateLookup"}
        if db.capabilities["pre_images"]:
            opts["full_document_before_change"] = "whenAvailable"
        while True:
            try:
                async with db.memories.watch(**opts) as stream:
                    async for change in stream:
                        await self._on_memory_change(change)
            except asyncio.CancelledError:
                return
            except Exception as e:
                log.warning("memory change stream error, restarting: %s", e)
                await asyncio.sleep(2)

    async def _on_memory_change(self, change: dict):
        op = change["operationType"]
        doc = change.get("fullDocument") or change.get("fullDocumentBeforeChange")
        mem_id = str(change["documentKey"]["_id"])
        updated = []
        if op == "update":
            updated = [k for k in change.get("updateDescription", {}).get("updatedFields", {}) if k != "embedding"]
            if updated and set(updated) <= {"last_retrieved_at", "retrieval_count", "embedding_model"}:
                return  # retrieval bookkeeping, not interesting for the live feed
        pre = change.get("fullDocumentBeforeChange") or {}
        # TTL deletes happen once the real clock passes expires_at; a caregiver "forget" usually doesn't.
        expired = op == "delete" and bool(pre.get("expires_at")) and pre["expires_at"] <= datetime.now(timezone.utc)
        msg = {"type": "memory", "op": op, "id": mem_id, "memory": ms.serialize(doc) if doc else None,
               "fields": updated, "expired": expired}
        if doc:
            await self.to_caregivers(str(doc["owner_id"]), msg)
            if doc.get("contact_id"):
                self.schedule_brief_refresh(str(doc["contact_id"]))
        # Without a pre-image we can't tell whose memory it was, so we don't broadcast it at all.

    async def _fallback_memory_event(self, op: str, memory_id: ObjectId):
        doc = await db.memories.find_one({"_id": memory_id})
        await self._on_memory_change({"operationType": op, "documentKey": {"_id": memory_id}, "fullDocument": doc})

    async def _watch_calls(self):
        while True:
            try:
                async with db.calls.watch(full_document="updateLookup") as stream:
                    async for change in stream:
                        doc = change.get("fullDocument")
                        if doc:
                            doc.pop("join_token", None)
                            await self.to_caregivers(str(doc["patient_id"]),
                                                     {"type": "call", "op": change["operationType"],
                                                      "call": ms.serialize(doc)})
            except asyncio.CancelledError:
                return
            except Exception as e:
                log.warning("call change stream error, restarting: %s", e)
                await asyncio.sleep(2)

    async def call_changed(self, call_id: ObjectId):
        """Used only when change streams are unavailable."""
        if db.capabilities["change_streams"]:
            return
        doc = await db.calls.find_one({"_id": call_id}, {"join_token": 0})
        if doc:
            await self.to_caregivers(str(doc["patient_id"]), {"type": "call", "op": "update", "call": ms.serialize(doc)})


hub = Hub()
