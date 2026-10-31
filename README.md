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

The full product and design spec (personas, data model, agent design, judging-criteria mapping, build plan) is in
[docs/DESIGN.md](docs/DESIGN.md).

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

Then fill in `GROQ_API_KEY` (the default LLM) and `VOYAGE_API_KEY`. Both are optional (see *Offline fallbacks*).
`ANTHROPIC_API_KEY` with `LLM_MODE=claude` uses Claude instead.

```bash
cd backend && .venv/Scripts/python -m uvicorn app.main:app --reload --port 8000
```

```bash
cd frontend && npm install && npm run dev
```

Open http://localhost:5173. The landing page links to the **patient screen** (`/patient`) and the **caregiver
dashboard** (`/caregiver`). On first start the backend seeds the demo family with six weeks of history. After signing
in, choose **Explore the demo family** to open it. Use **Reset demo** on the dashboard to start over.

For a single-port build, run `npm run build` in `frontend/`. FastAPI then serves `frontend/dist` on :8000.

### Sign-in (Firebase Auth + MongoDB)

Firebase only proves *who* someone is. Everything about *what they may see* lives in MongoDB: `users`,
`memberships` (owner / family / viewer per patient), `devices`, `invites` and `pairing_codes` (both expire through TTL
indexes), and an `auth_events` access log (TTL, 90 days).

- **Caregivers** sign in with **Google** or email/password. New caregivers create a care circle, join one with an
  8-letter invite code from an owner, or open the demo family.
- **Patients sign in their own way.** On `/patient` they can sign in with their own **Google** or email account, then
  either start their own profile (and invite family from their home screen) or type the 8-letter sign-in code a
  caregiver created under **Family & devices**, which links the account to the profile family already set up. Many
  patients, each with their own account and care circle. A family account is refused on the patient screen, and an
  owner can remove the patient's sign-in at any time.
- **Or the patient never types a password.** The same screen also shows a 6-digit number in very large type. A
  caregiver enters it under **Family & devices → Pair a tablet** (or taps the one-click button when signed in on the
  same browser). The tablet then stays signed in, and the caregiver can unpair it remotely if it's lost.
- **Family joining a call** need no account: each call's link carries an unguessable join token.
- Every API route checks the caller's role for that patient. WebSockets authenticate with their first message, so
  tokens never appear in URLs or logs. The tablet can place calls but can't open the memory inspector.

To turn on Firebase (project `uncharted-d58fe`):

1. Firebase Console → **Authentication → Sign-in method**: enable **Google**, **Email/Password** and **Anonymous**.
2. Firebase Console → **Project settings → Your apps → Web app**: copy `apiKey` and `appId` into `frontend/.env`
   (template: `frontend/.env.example`; project id and auth domain are already filled in).
3. In `backend/.env`, set `FIREBASE_PROJECT_ID=uncharted-d58fe`. The backend needs only the project id; it verifies
   tokens with Google's public certificates. **Don't put a service-account key in this repo.**
4. Restart both servers. The login page warns if the frontend and backend disagree on the auth mode.

Without Firebase config, both sides run in **dev auth mode**: the email form creates a local account with no password
check. It's for local development only, and the backend logs a warning at startup.

### Two-laptop demo

Browsers allow camera and microphone only on `https://` or `localhost`. For a second laptop on the same Wi-Fi, run
`npm run dev:https` on the host (self-signed certificate; laptop B accepts the warning once) and set
`PUBLIC_BASE_URL=https://<host-ip>:5173` in `backend/.env` so join links point at the right host. Across networks,
use a tunnel (e.g. `cloudflared tunnel --url http://localhost:5173`). On one machine, the receiver can simply open their
contact link in a second Chrome window.

### The one-button family call

The patient's home screen leads with a big **Join the family call** button (photos, the mic and the emergency button
stay for calling one person). It **always opens the meeting**: the patient never sees a refusal. Relatives who are
online and allowed right now (quiet hours and daily caps are respected) are rung, and a relative who comes online later
finds the meeting waiting on their page. The first to **Accept** claims it atomically and it becomes a normal 1:1 call
with that person (card, memory line, recall cards and the memory agent all work as usual); other rings stop. Declining
only stops your own ring. The meeting waits up to 15 minutes (`family_wait_seconds`) for someone, or until the patient
ends it, and is then recorded as unanswered. Ringing needs the relative's page open (no push notification yet).

### Relatives with their own account

A relative doesn't need an account (their contact link works alone), but they can have one: on their link page, **Sign
in or create an account** (same sign-in as caregivers), which keeps the link on the account automatically. After that
they open **/family** from any device: **I'm ready** to be rung, **Share an update**, what to ask about and how to
answer about an archived person, and their past calls. One account can hold several people (relatives of several
patients), and a caregiver can also be a relative (Rahul is both). Caregivers see which contacts have an account under
**Contacts & rules** and can unlink it. A relative's account is denied every caregiver and tablet route.

### Calling a relative

Every contact has a **persistent link** (`/c/<token>`; copy or replace it under **Contacts & rules**). The relative opens
it once, taps **I'm ready**, and keeps the page open. When the patient taps their photo, the page **rings** (Accept /
Decline); Accept goes through the consent screen into the call. If nobody answers within 30 seconds, the patient sees a
calm "Riya is busy right now. Let's try again later." and the call is recorded as unanswered. When nothing is ringing,
the same page lets the relative **share an update** ("I got engaged"), which goes through the memory agent as the
contact speaking about themselves, read notes before calling, and see their past calls. The per-call `/join/…` link
still works.

### Offline fallbacks

Each external piece degrades instead of breaking, and the dashboard sidebar shows which mode is active:

| Piece | Normal | Fallback |
|---|---|---|
| Memory agent and brief writer | Groq `openai/gpt-oss-120b` with strict JSON-schema output (`GROQ_API_KEY`); Claude `claude-opus-5-5` with `LLM_MODE=claude` | Rule-based extractor and template brief (`app/agent/offline.py`). A brief that takes longer than 10 s also uses the template |
| Embeddings | Voyage `voyage-3.5`, 1024 dims (voyageai.com key, or an Atlas-issued key with `VOYAGE_BASE_URL=https://ai.mongodb.com/v1`) | Local hashed embedding, same width |
| Retrieval | Atlas `$vectorSearch` with pre-filters | Exact in-app cosine scan |
| Live updates | MongoDB change streams | Events emitted from app code |
| Speech-to-text | Web Speech API (Chrome) | "Type instead" box on the call screen |

Every vector is stored with the model that produced it (`embedding_model`, also a filter field on the vector index),
and searches only compare vectors from the same model. If Voyage is briefly unavailable or rate-limited (free-tier
keys without a payment method get 3 requests per minute), new memories get local vectors, and a background job
re-embeds them with Voyage a minute later. Voyage results are cached in `embedding_cache` (TTL 30 days), so the same
text is never embedded twice.

When Claude is used, requests set `fallbacks: "default"` (beta `server-side-fallback-2026-07-01`). If a safety classifier declines,
the API retries on its recommended fallback model. If the whole chain declines, the app uses the offline path.

## How the memory works

### Memory document (`memories`)
`owner_id`, `contact_id`, `type` (relationship · fact · event · plan · preference · constraint · topic · sensitive ·
feedback), `content`, `embedding`, `source` {kind, origin, call_id, speaker, quote}, `trust`, `confidence`,
`importance`, `status` (active · pending_review · superseded · rejected), `visibility` (patient · caregiver_only ·
never_surface), `supersedes` / `superseded_by` / `conflicts_with`, `version`, `event_date`, `expires_at`,
`retrieval_count`, `feedback`. Constraint memories also have a structured `rule`.

### Write path (`app/agent/pipeline.py`)
1. **Extract.** The LLM reads the speaker-tagged transcript and returns a summary, a recap for the patient, and atomic
   candidate memories with absolute dates.
2. **Neighbours.** Each candidate is embedded and searched with `$vectorSearch` over the same patient and contact.
   Same-type facts and relationships are always included.
3. **Reconcile.** The LLM chooses ADD / UPDATE / SUPERSEDE / IGNORE / CONFLICT for all candidates in one call.
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
of today), `$vectorSearch` filtered by owner, contact, `status: active` and `visibility: patient`, a second
`$vectorSearch` for **recent conversation** that also pre-filters on the index's `type` (topic, event, plan, preference)
and `created_at` (last 21 days) fields, and the last two call summaries. Each candidate is then re-ranked:

```
final = 0.5·similarity + 0.2·recency + 0.15·importance + 0.15·trust (+0.3 open-loop boost)
recency = exp(-days / 14)
```

The relationship memory is always shown. Sensitive memories are passed to the LLM only as **guardrails** and are never
shown. Every brief is stored with each candidate's score breakdown, and the **Memory Inspector** and **Briefs** tab
display those scores.

### The "Who is this?" card (`app/callcard.py`, `pages/CallPage.jsx`)
The patient's card is short: **who** (the name they use, the relation from their point of view, the stored photo), **at
most one memory** (the brief's top-ranked non-relationship item, re-checked to be active and patient-visible) and who
else is in the room. It appears only once the caller's video is live: large for about 20 seconds, then a small name tag
at the bottom edge of the video. It is read aloud once, 2 seconds after connecting. The **Who is this?** button, or the
patient saying "who is this?" / "who are you?" (simple rules on their own transcript, in their language), brings it
back. Speech recognition pauses while the screen reads aloud, and the server drops anything heard in that window, so
the app's own voice never reaches the transcript. Talking points, receiver notes and the family's hints go to the
relative's side panel instead. Each card shown, button press, spoken question and "also here" change is counted on the
call; asking "who is this?" more than once in a call reinforces that person's relationship memory, so the next card
adds identity detail ("your granddaughter, Rahul's daughter"). While the relative talks, a small **recall card** may
show a matching memory (embeddings and `$vectorSearch` only, at most one a minute).

### Memory changes actions, not just text
`app/agent/guard.py` checks caregiver constraint memories before dialing. Quiet hours and a daily cap block the call.
A minimum gap between calls produces a gentle reminder with a **Call anyway** button ("You spoke to Riya at 11:05.
Riya will visit Aaji on Sunday."). An **archived** person (someone who has died or is no longer in touch) is never
called: the patient is gently offered someone else, and their memories are hidden until a caregiver unarchives them.

### MongoDB features used
- **Atlas Vector Search**: `memory_vec` index with filter fields `owner_id`, `contact_id`, `type`, `status`,
  `visibility`, `created_at` (`app/db.py`).
- **TTL indexes**: `memories.expires_at` (expireAfterSeconds 0) and `transcript_chunks.created_at` (30 days).
- **Change streams** on `memories` (with pre-images, so TTL deletes still show what expired) and `calls`, relayed over a
  WebSocket to the caregiver dashboard. They also rebuild the patient's overlay mid-call when a caregiver edits a
  memory.
- **App-logic lifecycle rules**: visibility flags, supersede chains, cascade delete when a contact is removed, hard
  "forget".
- **Aggregation pipelines** (`app/stats.py`) power the caregiver Overview. One `$facet` over `calls` produces calls
  per day and per hour (`$dateTrunc` / `$hour` in IST), who the patient talks to (`$lookup` into `contacts`), and totals
  versus the previous period. A second `$facet` over `memories` produces the memory bank by type, upcoming events and
  plans, and **recurring confusions** (the same mistaken patient belief grouped and counted). Activity uses
  `memory_events` joined to memories.
- **More TTL**: invite codes (7 days), device pairing codes (15 minutes), access log (90 days).

### Demo family data (`app/seed.py`)

The seeder writes six weeks of history for Aaji (Sunanda Patil, 74, Pune) through the real memory code, with the clock
pinned to each moment:

- 10 contacts, plus a pending "Ramesh" request (scam protection).
- About 150 calls with transcripts. Transcripts older than 30 days are removed by TTL, as in production.
- About 30 "paused" attempts by the repeat-call guard, clustered in the evening (sundowning).
- About 80 memories, including supersede chains, patient-belief conflicts (one recurring three times), rejections,
  low-confidence reviews, private notes, and upcoming events.
- 30 real briefs with score breakdowns, the audit log, 4 family caregivers with roles, a paired tablet, and an access
  log.

Riya stays demo-safe: her caregiver notes are untouched and her last call was days ago.

## Demo script (~5 minutes)

Setup: tab 1 is `/patient` (pair it once from the dashboard), tab 2 is `/caregiver` (signed in, demo family open).
Riya's contact link (**Contacts & rules → Copy link**) opens in a third window or on a second laptop; press **I'm
ready** there. Before the call, Riya's facts are the caregiver's notes ("lives in Mumbai", "dog Bruno") plus a few older
call memories.

1. **First call.** Aaji taps Riya; Riya's page rings, she accepts and agrees to the consent note. Once her video is live,
   Aaji's screen shows *"Riya, your granddaughter"* with one memory, reads it aloud, and shrinks to a name tag after 20
   seconds. Riya's side panel shows talking points and the family's hints. Riya says: *"Aaji, I moved
   to Pune last month. My physics exam is on Friday, and I'll visit you on Sunday!"* Aaji says: *"Riya is my daughter,
   na?"* Riya says: *"Don't tell Aaji about Papa's surgery."* End the call.
2. **Agent runs.** The dashboard's live feed lights up through change streams. *Riya lives in Pune* SUPERSEDES *Mumbai*,
   the exam and the visit are ADDed with expiry dates, *daughter* becomes a CONFLICT in the review queue (it does not
   overwrite the caregiver's "granddaughter"), and the surgery note is saved as `never_surface`. Aaji hears the recap.
3. **Repeat-call guard.** Tap Riya again: *"You spoke to Riya at 10:15. Riya will visit Aaji on Sunday. Do you still
   want to call?"*
4. **Later.** Use **+1 day** / **+2 days** on the demo clock until it's the day after the exam, then call again (Call
   anyway). The brief now says *lives in Pune*, and Riya's panel suggests asking how the physics exam went (open-loop
   boost). If Aaji asked "who is this?" twice on the first call, her card now adds "Rahul's daughter".
5. **Inspect.** In the Memory Inspector, open the exam memory to see the source quote, score bars, the Mumbai → Pune
   chain and the audit log. The Briefs tab shows the full re-ranking table.
6. **Correction.** In the Review queue, choose *Keep existing (reject)* for the "daughter" claim.
7. **Private.** The surgery memory is visible on the dashboard but appears in no brief. Riya can also press **Go off the
   record**, and nothing said then is stored.
8. **Expiry.** On the Live tab, click **Add expiring memory**. Within ~1–2 minutes the TTL monitor deletes it and the
   feed shows **TTL expired**.

Tips: mid-call, edit one of Riya's memories in the Inspector. The patient's card updates silently with "Updated just
now". Riya can add "Who else is with you?" (e.g. Rahul), and Aaji's card says "Also with Riya: Rahul, your son".
Ask the patient screen for **Baba** by voice to see the gentle archived-person screen (Aaji's late husband is seeded as
archived).
If the microphone or Wi-Fi misbehaves, use **Type instead** on either side.

### Headless check

With the backend running in dev auth mode, this command signs in, pairs a test tablet, plays the whole demo through
the API, and asserts each beat (supersede, conflict, TTL fields, never-surface, guard, later brief, score breakdowns):

```bash
cd backend && .venv/Scripts/python scripts/smoke.py
```

## Project layout

```
backend/app/
  main.py            REST + WebSocket API (every route checks the caller's role)
  auth.py            Firebase ID-token verification + care-circle authorization
  routes_auth.py     session, care circles, invites, device pairing, access log
  stats.py           caregiver insights via aggregation pipelines
  realtime.py        signaling relay, transcript fan-out, change-stream watchers
  db.py              collections, TTL / regular / vector indexes
  memory_store.py    memory docs, lifecycle helpers, audit log, vector search
  embeddings.py      Voyage or local embeddings
  llm.py             Groq / Claude structured-output wrapper
  callcard.py        what each side of a call sees: the patient's short card, the relative's notes
  routes_contact.py  the relative's persistent link: presence, ringing, written updates
  recall.py          live recall cards from the relative's rolling transcript
  lang.py            patient languages and "who is this?" phrases
  agent/             pipeline (write path), brief (read path), guard, prompts, offline fallback
  seed.py            demo family: six weeks of history written through the real memory code
frontend/src/
  lib/identity.js    Firebase Auth (separate caregiver and tablet identities) or dev fallback
  pages/Login        Google / email sign-in, invite links
  pages/PatientGate  patient sign-in (own account or family code) and password-free tablet pairing
  pages/PatientHome  big-photo grid, voice "Call my son", guard modal, emergency button
  pages/CallPage     WebRTC call, Who-is-this overlay, captions, off the record, recap
  pages/JoinPage     receiver consent screen
  pages/caregiver/*  overview insights, review queue, memory inspector, briefs, calls, contacts & rules,
                     family & devices, onboarding
  components/charts  stacked columns, bar lists and stat tiles (validated colours, tooltips, table view)
```

## Ethics and safety
Every participant sees a consent banner, and receivers can go off the record. Audio and video are never stored, and
transcripts expire after 30 days. Caregivers control what the patient sees. Patient statements are never treated as
ground truth. Unknown people need caregiver approval before they can be called. Forgetting is a hard delete. Pehchaan
is a communication aid, not a medical device.
