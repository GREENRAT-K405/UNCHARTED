# Pehchaan: a memory-aware video calling companion

**Track 1: Agentic Memory**, MongoDB Student Hackathon, SVNIT.

A person living with Alzheimer's taps a family member's photo (or says "Call my son"). A video call starts, and a
**"Who is this?"** card tells them, in large text and out loud, who the caller is and what they talked about last time.
After each call, a memory agent reads the transcript and decides what to **add, update, supersede, ignore or flag** in
MongoDB. The next call starts with a better brief. Caregivers can inspect, correct, expire and delete every memory, and
those changes reach the patient's screen live through change streams.

```
patient taps Riya ─► repeat-call guard (constraint memories) ─► pre-call brief ($vectorSearch + re-rank)
        │                                                              │
        ▼                                                              ▼
  1:1 WebRTC call ◄── "Who is this?" overlay (live-refreshed via change streams)
        │  each browser transcribes only its own mic → speaker-tagged chunks
        ▼
  call ends ─► memory agent: extract → reconcile (trust-aware) → write ─► memories (TTL, versions, audit log)
                                                                              │ change stream
                                                                              ▼
                                                            caregiver dashboard: review queue, inspector
```

## Quick start

Prerequisites: Python 3.11+, Node 18+, Chrome, and either Docker or a MongoDB Atlas cluster.

```bash
docker compose up -d
```

This starts `mongodb/mongodb-atlas-local`, which has Vector Search, change streams and TTL. To use Atlas instead, put your
`MONGODB_URI` in `backend/.env`.

```bash
cd backend && python -m venv .venv && .venv/Scripts/pip install -r requirements.txt   # macOS/Linux: .venv/bin/pip
```

```bash
cp backend/.env.example backend/.env
```

Then fill in `ANTHROPIC_API_KEY` and `VOYAGE_API_KEY`. Both are optional (see *Offline fallbacks*).

```bash
cd backend && .venv/Scripts/python -m uvicorn app.main:app --reload --port 8000
```

```bash
cd frontend && npm install && npm run dev
```

Open http://localhost:5173. The landing page links to the **patient screen** (`/patient`) and the **caregiver
dashboard** (`/caregiver`). Demo data (Aaji, her son Rahul, granddaughter Riya, …) is seeded on first start. Use
**Reset demo** on the dashboard to start over.

For a single-port build, run `npm run build` in `frontend/`. FastAPI then serves `frontend/dist` on :8000.

### Two-laptop demo

Browsers allow camera and microphone only on `https://` or `localhost`. For a second laptop, either open the dev server
through a tunnel (e.g. `cloudflared tunnel --url http://localhost:5173`) or, in Chrome on laptop B, add the LAN URL
under `chrome://flags/#unsafely-treat-insecure-origin-as-secure`. Set `PUBLIC_BASE_URL` in `backend/.env` so join links
point at the right host. On one machine, the receiver can simply open the join link in a second Chrome window.

### Offline fallbacks

Each external piece degrades instead of breaking, and the dashboard sidebar shows which mode is active:

| Piece | Normal | Fallback |
|---|---|---|
| Memory agent and brief writer | Claude (`claude-opus-5-5`, structured JSON output) | Rule-based extractor and template brief (`app/agent/offline.py`) |
| Embeddings | Voyage `voyage-3.5`, 1024 dims | Local hashed embedding, same width |
| Retrieval | Atlas `$vectorSearch` with pre-filters | Exact in-app cosine scan |
| Live updates | MongoDB change streams | Events emitted from app code |
| Speech-to-text | Web Speech API (Chrome) | "Type instead" box on the call screen |

Claude requests set `fallbacks: "default"` (beta `server-side-fallback-2026-07-01`). If a safety classifier declines,
the API retries on its recommended fallback model. If the whole chain declines, the app uses the offline path.

## How the memory works

### Memory document (`memories`)
`owner_id`, `contact_id`, `type` (relationship · fact · event · plan · preference · constraint · topic · sensitive ·
feedback), `content`, `embedding`, `source` {kind, origin, call_id, speaker, quote}, `trust`, `confidence`,
`importance`, `status` (active · pending_review · superseded · rejected), `visibility` (patient · caregiver_only ·
never_surface), `supersedes` / `superseded_by` / `conflicts_with`, `version`, `event_date`, `expires_at`,
`retrieval_count`, `feedback`. Constraint memories also have a structured `rule`.

### Write path (`app/agent/pipeline.py`)
1. **Extract.** Claude reads the speaker-tagged transcript and returns a summary, a recap for the patient, and atomic
   candidate memories with absolute dates.
2. **Neighbours.** Each candidate is embedded and searched with `$vectorSearch` over the same patient and contact.
   Same-type facts and relationships are always included.
3. **Reconcile.** Claude chooses ADD / UPDATE / SUPERSEDE / IGNORE / CONFLICT for all candidates in one call.
4. **Trust guard, enforced in code** (`may_override`). Trust scores: caregiver 1.0, contact about themselves 0.85,
   contact about others 0.7, patient 0.4. *In this domain the user is the least reliable source.* Trust also depends on
   the subject. A contact is the authority on their own life, so Riya's "I moved to Pune" supersedes the caregiver's
   older "lives in Mumbai". Patient claims never overwrite anything, and only the caregiver changes relationships.
   Anything blocked by the guard becomes a `pending_review` CONFLICT.
5. **Lifecycle.** Events and plans get `expires_at` = event date + N days, and the **TTL index** deletes them.
   Sensitive items are `never_surface`. Patient claims go to `caregiver_only` and `pending_review`.
6. Every action is written to `memory_events`. The log stores **ids and actions only**, so "forget" really forgets.

### Read path (`app/agent/brief.py`)
The candidate pool is built from several sources: deterministic core facts, open loops (events or plans within ±3 days
of today), `$vectorSearch` filtered by owner, contact, `status: active` and `visibility: patient`, and the last two call
summaries. Each candidate is then re-ranked:

```
final = 0.5·similarity + 0.2·recency + 0.15·importance + 0.15·trust (+0.3 open-loop boost)
recency = exp(-days / 14)
```

The relationship memory is always shown. Sensitive memories are passed to Claude only as **guardrails** and are never
shown. Every brief is stored with each candidate's score breakdown, and the **Memory Inspector** and **Briefs** tab
display those scores.

### Memory changes actions, not just text
`app/agent/guard.py` checks caregiver constraint memories before dialing. Quiet hours and a daily cap block the call.
A minimum gap between calls produces a gentle reminder with a **Call anyway** button ("You spoke to Riya at 11:05.
Riya will visit Aaji on Sunday.").

### MongoDB features used
- **Atlas Vector Search**: `memory_vec` index with filter fields `owner_id`, `contact_id`, `type`, `status`,
  `visibility`, `created_at` (`app/db.py`).
- **TTL indexes**: `memories.expires_at` (expireAfterSeconds 0) and `transcript_chunks.created_at` (30 days).
- **Change streams** on `memories` (with pre-images, so TTL deletes still show what expired) and `calls`, relayed over a
  WebSocket to the caregiver dashboard. They also rebuild the patient's overlay mid-call when a caregiver edits a
  memory.
- **App-logic lifecycle rules**: visibility flags, supersede chains, cascade delete when a contact is removed, hard
  "forget".

## Demo script (~5 minutes)

Setup: tab 1 is `/patient`, tab 2 is `/caregiver`. Riya's join link opens in a third window or on a second laptop.
Before the call, the only memories about Riya are the caregiver's notes ("lives in Mumbai", "dog Bruno").

1. **First call.** Aaji taps Riya. The brief shows only caregiver basics. Riya (on her join link) says: *"Aaji, I moved
   to Pune last month. My physics exam is on Friday, and I'll visit you on Sunday!"* Aaji says: *"Riya is my daughter,
   na?"* Riya says: *"Don't tell Aaji about Papa's surgery."* End the call.
2. **Agent runs.** The dashboard's live feed lights up through change streams. *Riya lives in Pune* SUPERSEDES *Mumbai*,
   the exam and the visit are ADDed with expiry dates, *daughter* becomes a CONFLICT in the review queue (it does not
   overwrite the caregiver's "granddaughter"), and the surgery note is saved as `never_surface`. Aaji hears the recap.
3. **Repeat-call guard.** Tap Riya again: *"You spoke to Riya at 10:15. Riya will visit Aaji on Sunday. Do you still
   want to call?"*
4. **Later.** Use **+1 day** / **+2 days** on the demo clock until it's the day after the exam, then call again (Call
   anyway). The brief now says *lives in Pune* and suggests asking how the physics exam went (open-loop boost).
5. **Inspect.** In the Memory Inspector, open the exam memory to see the source quote, score bars, the Mumbai → Pune
   chain and the audit log. The Briefs tab shows the full re-ranking table.
6. **Correction.** In the Review queue, choose *Keep existing (reject)* for the "daughter" claim.
7. **Private.** The surgery memory is visible on the dashboard but appears in no brief. Riya can also press **Go off the
   record**, and nothing said then is stored.
8. **Expiry.** On the Live tab, click **Add expiring memory**. Within ~1–2 minutes the TTL monitor deletes it and the
   feed shows **TTL expired**.

Tips: mid-call, edit one of Riya's memories in the Inspector. The patient's overlay refreshes with "Updated just now".
If the microphone or Wi-Fi misbehaves, use **Type instead** on either side.

### Headless check

With the backend running, this command plays the whole demo through the API and asserts each beat (supersede, conflict,
TTL fields, never-surface, guard, later brief, score breakdowns):

```bash
cd backend && .venv/Scripts/python scripts/smoke.py
```

## Project layout

```
backend/app/
  main.py            REST + WebSocket API
  realtime.py        signaling relay, transcript fan-out, change-stream watchers
  db.py              collections, TTL / regular / vector indexes
  memory_store.py    memory docs, lifecycle helpers, audit log, vector search
  embeddings.py      Voyage or local embeddings
  llm.py             Claude structured-output wrapper
  agent/             pipeline (write path), brief (read path), guard, prompts, offline fallback
  seed.py            demo family
frontend/src/
  pages/PatientHome  big-photo grid, voice "Call my son", guard modal, emergency button
  pages/CallPage     WebRTC call, Who-is-this overlay, captions, off the record, recap
  pages/JoinPage     receiver consent screen
  pages/caregiver/*  live feed, review queue, memory inspector, briefs, calls, contacts & rules
```

## Ethics and safety
Every participant sees a consent banner, and receivers can go off the record. Audio and video are never stored, and
transcripts expire after 30 days. Caregivers control what the patient sees. Patient statements are never treated as
ground truth. Unknown people need caregiver approval before they can be called. Forgetting is a hard delete. Pehchaan
is a communication aid, not a medical device.
