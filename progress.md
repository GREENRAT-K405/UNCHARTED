# Pehchaan: project progress and functional design

**Track 1: Agentic Memory**, MongoDB Student Hackathon, SVNIT. Team repo: `GREENRAT-K405/UNCHARTED`.
Last updated 31 Oct 2026.

Pehchaan is a video-calling companion for people living with Alzheimer's. The patient taps a family member's photo (or
says "Call my son"). During the call, a **"Who is this?"** card shows and reads out who the caller is and what they
talked about last time. After the call, a memory agent decides what to remember, update, replace, ignore or flag for
review. The next call starts with a better brief. Caregivers can inspect and correct every memory, and their changes
reach the patient's screen live.

Related documents:
- [README.md](README.md): quick start, demo script, MongoDB features.
- [setup.md](setup.md): environment setup, Atlas, keys, two-laptop demo.
- [docs/DESIGN.md](docs/DESIGN.md): the original product spec, written before the build.

---

## 1. Status at a glance

| Area | Status |
|---|---|
| Patient screen (photo grid, voice "Call my son", repeat-call guard, emergency button) | Done |
| Patient sign-in (family pairing first; own Google/email account, family sign-in code) | Done, **not committed yet** |
| 1:1 WebRTC video call with captions, recap | Done |
| "Who is this?" card: short (who, relation, one memory), shown when video is live, 20 s then name tag, button + voice trigger, read-aloud kept out of the transcript, "also here", events fed back into memory | Done, **not committed yet** (spec-1 task 1) |
| Persistent contact link per relative: waiting room, ringing, Accept/Decline, 30 s no-answer | Done, **not committed yet** (task 2) |
| **One-button "Join the family call"**: rings every online, allowed relative; first to accept gets a normal 1:1 call (atomic claim) | Done, **not committed yet** |
| **Relatives' own accounts** and a `/family` page (ring, updates, notes, past calls); caregivers can also be relatives | Done, **not committed yet** |
| Patient home: family setup first, long-press family menu, recap line under each photo (live) | Done, **not committed yet** (tasks 3–4) |
| Relative page: share an update through the memory agent, notes, past calls | Done, **not committed yet** (task 5) |
| Archive a contact (died / no longer in touch), gentle refusal, family note for relatives | Done, **not committed yet** (task 6) |
| Patient language (English, Hindi, Marathi, Gujarati) for speech, read-aloud, card and recap | Done, **not committed yet** (task 7) |
| Live recall cards during the call | Done, **not committed yet** (task 9, stretch) |
| Relative joins by link, with consent screen, "receiver notes" and a side panel of talking points | Done |
| Per-speaker live transcription (Web Speech API) with "Type instead" fallback | Done |
| Post-call memory agent (extract, reconcile, trust guard, write) | Done |
| Pre-call brief (hybrid retrieval, explainable re-ranking, stored score breakdowns) | Done |
| Caregiver dashboard: Overview, Review queue, Memory inspector, Briefs, Calls, Contacts & rules, Family & devices | Done |
| Firebase Auth + MongoDB care circles (owner / family / viewer), invites, access log | Done |
| Atlas Vector Search, TTL indexes, change streams, aggregation insights | Done |
| LLM: Groq (`openai/gpt-oss-120b`) by default, Claude optional, rule-based offline fallback | Done |
| Embeddings: Voyage `voyage-3.5` (Atlas-issued key) with local fallback and background re-embedding | Done |
| Seeded demo family: six weeks of history written through the real memory code | Done |
| HTTPS dev server for two-laptop calls; clear message when camera/mic is blocked | Done |
| Real two-laptop call over HTTPS | **Not yet tested** |
| Real Firebase sign-in on the patient screen | **Not yet tested** (tested in dev-auth mode and with mocked screens) |
| Ringing on a second device, real voices per language, long-press on touch | **Not yet tested** (headless browser with fake camera/mic only) |

---

## 2. Timeline

All work happened on 31 Oct 2026. Contributors: Param (`GREENRAT-K405`) and Pratham.

| Commit | Who | What |
|---|---|---|
| `51fd6bc` | Param | README |
| `74048c7` | Pratham | First version of Pehchaan: call, overlay, memory agent, brief, dashboard |
| `f6b8365` | Pratham | Kept the design doc as `docs/DESIGN.md` |
| `bdb860c` | Pratham | Firebase auth, care circles, seeded history, caregiver insights |
| `9361eaa` | Param | Configurable embeddings base URL for Atlas model keys; `setup.md` |
| `0afca8d` | Pratham | Atlas-issued Voyage keys; vectors tagged with their model so vector spaces never mix |
| `2915b88` | Pratham | Merge of both embedding changes |
| `922ce20` | Param | HTTPS dev server for two-laptop calls; on-screen reason when media fails |
| `10b111c` | Param | "Family: set up this screen now" button on the patient pairing screen |
| `ba64771` | Param | Groq as an LLM provider for the memory agent |
| *(uncommitted)* | Param | Patient sign-in: own account, family sign-in code, invite family, unlink |
| *(uncommitted)* | Param | spec-1: "Who is this?" card, contact links and ringing, simpler patient screen, home recap lines, relative updates, archive, language, Groq docs fix, type/created_at vector filters, live recall cards |

---

## 3. Architecture

```
 Patient screen (/patient)          Relative (/c/:token, /join/…)     Caregiver dashboard (/caregiver)
 React, big UI, TTS, voice          ring, consent, notes, updates      7 tabs, live feed
        │   ▲                               │   ▲                               │   ▲
        │   │  REST /api + WebSocket /ws    │   │                               │   │ WebSocket: change-stream events
        ▼   │                               ▼   │                               ▼   │
 ┌──────────────────────────────────── FastAPI (Motor) ────────────────────────────────────┐
 │ auth.py / routes_auth.py   Firebase token check → Principal → role per patient          │
 │ main.py                    contacts, calls, memories, briefs, transcripts, demo clock   │
 │ realtime.py                WebRTC signalling relay, transcript fan-out, change streams  │
 │ agent/pipeline.py          post-call write path     agent/brief.py   pre-call read path │
 │ agent/guard.py             repeat-call guard        agent/offline.py rule-based fallback│
 │ callcard.py   each side's view of a call   routes_contact.py  contact link, ringing    │
 │ recall.py     live recall cards            lang.py            languages, "who is this?"│
 │ llm.py  Groq / Claude      embeddings.py  Voyage / local     stats.py  aggregations     │
 └──────────────────────────────────────────┬─────────────────────────────────────────────┘
                                            ▼
                     MongoDB Atlas: Vector Search, TTL, change streams (with pre-images)
```

- **Frontend:** React and Vite. The dev server proxies `/api` and `/ws` to `localhost:8000`.
  `npm run dev:https` serves over HTTPS, so another laptop's browser allows the camera and mic.
- **Calls:** browser-to-browser WebRTC using Google STUN. Signalling goes through a WebSocket hub in the backend
  process. Each browser transcribes only its own microphone, so every transcript line has a reliable speaker tag.
- **Auth:** Firebase proves *who* someone is. MongoDB decides *what they may see*.
- **Single-port mode:** `npm run build`, after which FastAPI serves `frontend/dist` on :8000.

---

## 4. Functional design

### 4.1 People and identities

| Who | How they sign in | What they can do |
|---|---|---|
| **Caregiver** (owner / family / viewer) | Google or email/password on `/login` | Owner: everything, including invites and removing access. Family: review and edit memories, add contacts and rules, pair tablets. Viewer: read-only |
| **Patient, own account** | Google or email/password on `/patient` | Use their own home screen, place calls, invite family |
| **Patient, paired tablet** | Anonymous Firebase user, paired by family with a 6-digit code | Use the home screen and place calls. Cannot invite anyone, because a shared tablet might be used by anyone |
| **Relative** | No account needed: a persistent contact link (`/c/<token>`) or a per-call join link. Or their own account (same sign-in as caregivers), linked to the contact, at `/family` | Wait for calls and answer them, share updates about their own life, see talking points and the family's hints and past recaps, go off the record, say who else is in the room. Denied every caregiver and tablet route |

The code checks every one of these rules on every request. `auth.require_access(principal, patient_id, role=…)` is
called by every route. A patient's account and their paired tablet get the same limited "device" role, which can place
calls but cannot open the memory inspector or call history.

### 4.2 Patient sign-in and onboarding (newest feature)

**Goal:** support many patients, each with their own login, without making setup hard for someone with dementia.

The patient screen (`/patient`, [PatientGate.jsx](frontend/src/pages/PatientGate.jsx)) has three steps:

1. **Sign-in screen.** The main card is family setup: the 6-digit pairing number, and a one-tap "set this up for Aaji"
   button when a caregiver is already signed in on the same browser. Underneath, "Have your own account? Sign in"
   opens "Continue with Google", "Sign in with email" and "New here? Create an account". A `?claim=` link shows the
   patient's own sign-in first.
2. **Profile step.** This appears after an account signs in for the first time. The patient can:
   - type the **sign-in code** their family created in the dashboard. That links the account to the profile and
     memories the family already set up. Opening the family's link (`/patient?claim=CODE`) links it automatically.
   - or **start on their own** by entering what they like to be called and their full name. They become a new patient
     with an empty care circle.
3. **Home screen.** A patient with their own account and no contacts yet sees "Invite my family". It creates an owner
   invite link they can send. The first family member to accept becomes owner and can then add contacts, rules and
   memories. "Signed in as …", "Invite family" and "Sign out" (with a confirmation) are in the long-press family menu.

Safeguards:
- Only a fresh account can become a patient. An account that already looks after someone is refused on the patient
  screen ("This is a family account"). An account that is already a patient's cannot claim a second patient.
- Sign-in codes are single use and expire after 7 days through a TTL index. A family invite can't be used as a
  patient code, and the reverse is also refused.
- An owner can remove the patient's sign-in under **Family & devices**. Paired tablets keep working.
- Every link, sign-up and removal is written to the access log.

Data design: a patient is a `users` document with `role: "patient"`. Its `firebase_uid` field *is* the patient's
login. The uid is never returned by the API.

Backend: `POST /patients/register`, `POST /patients/claim`, `GET`/`DELETE /circles/{pid}/login`, and
`POST /circles/{pid}/invites` with `role: "patient"` ([routes_auth.py](backend/app/routes_auth.py)).

The patient screen leads with **family setup** (the 6-digit number and the one-tap "set this up" button); the patient's
own sign-in is a smaller "Have your own account? Sign in" option. "Invite family" and "Sign out" live in a **family
menu** opened by holding a small corner icon for 3 seconds, so the home screen shows only photos, the mic and the
emergency button (plus "Someone new").

### 4.3 Placing a call

1. The patient taps a photo, or presses the mic and says "Call my son". Voice matching tries the contact's name first,
   then nicknames, then the relation as a whole word.
2. **Repeat-call guard** ([guard.py](backend/app/agent/guard.py)). Caregiver *constraint memories* change what the app
   does:
   - `quiet_hours` blocks the call ("Rahul sleeps 9 AM to 3 PM").
   - `max_calls_per_day` blocks the call.
   - `min_gap_minutes` is a soft block: "You spoke to Riya at 11:05. Riya will visit on Sunday. Call anyway?"

   Blocked attempts are saved as `blocked` calls, so caregivers can see when and how often the guard steps in. The
   emergency button ("Help, call Rahul now") always gets through.
3. Contacts the patient asked to add ("Someone new") stay `pending` until a caregiver approves them. They cannot be
   called before then, which protects against scams.
4. Archived people are refused by the guard (tap or voice) without calling or saying why; the patient sees "Let's call
   someone else today" with two or three photos to choose from. The attempt is logged as a blocked call.
5. The call is created with a random join token, and the pre-call brief starts building in the background (the LLM
   gets 10 s, then the template brief is used).
6. **The one-button family call** (`POST /api/calls/family`). The patient's home leads with "Join the family call". The
   server rings every active contact that has a page open and passes the guard (hard rules such as quiet hours and
   caps skip them; the soft "called a while ago" gap still rings). The call starts with `contact_id: null` and the
   list of who was rung. The first Accept runs an atomic `update_one({status: ringing, contact_id: null})`; the winner
   becomes the call's contact, the brief is built, the patient's screen gets `claimed` with the name tag, and the
   others get `ring_over`. Declines are per person; if all rung relatives decline (or 30 s pass) the call is
   `declined` / `unanswered` and the patient hears "Nobody is free right now". Nobody online: no ring, an
   `unanswered` family call is recorded, and the patient is told at once. A second tap while it rings is the same call.
7. **Ringing** ([routes_contact.py](backend/app/routes_contact.py)). Every open contact link of that person rings
   (WebAudio tone, Accept / Decline). Accept goes to the consent screen and into the call. Decline, or no answer within
   30 s (`RING_TIMEOUT_SECONDS`), shows the patient "Riya is busy right now. Let's try again later." and a big button
   home, and records the call as `declined` or `unanswered`. Hanging up before an answer records `cancelled`. None of
   these count as calls for the guard or the insights.
8. **Home screen memory:** each photo shows one line from the latest patient-facing recap with that person
   ("Talked yesterday · Riya told you about Pune"), refreshed live over `/ws/patient/{id}` when a call is processed.

### 4.4 During the call

- **"Who is this?" card** ([callcard.py](backend/app/callcard.py), [CallPage.jsx](frontend/src/pages/CallPage.jsx)).
  Principles: always give the answer, never test; a wrong name is worse than none; short.
  - *Identity* comes from the call's contact: the name the patient uses (`known_as`), the relation from their point of
    view ("your granddaughter"), the stored photo. No active contact means no card at all.
  - *Content:* who, relation, and **at most one memory**: the brief's top-ranked non-relationship item, re-checked to be
    active and patient-visible. Read aloud says exactly what is shown. The name tag is ready before the brief (no LLM
    needed); the memory line joins when the brief arrives. The patient never sees a spinner or an error.
  - *Timing:* only once the caller's video is playing. Large for 20 s at the bottom of the video (never over the face),
    then a small name tag at the bottom edge. Read aloud once, 2 s after connecting. Big text, solid high-contrast card,
    light and dark mode.
  - *Bringing it back:* the large **Who is this?** button, or the patient saying "who is this / who are you / who am I
    talking to / what is your name / do I know you" (or the Hindi, Marathi, Gujarati phrases in
    [lang.py](backend/app/lang.py)), detected with simple rules on the patient's own transcript chunks.
  - *Read-aloud stays out of the transcript:* speech recognition is aborted while speaking and resumes 1 s after; chunks
    recognised in that window are dropped in the browser, and the server also drops the patient's chunks and any of
    the relative's chunks that echo the spoken words.
  - *Caregiver edits mid-call:* the card text updates silently with "Updated just now" (no re-read, no re-expand);
    contact edits (name, photo) refresh the name tag too.
  - *"Also here":* the relative picks who else is with them from the patient's active contacts; the card adds "Also
    with Riya: Rahul, your son".
  - *Feedback into memory:* card shown, button, voice and "also here" changes are counted and time-stamped on the call
    (`overlay`). The Calls tab shows "Asked who this was 3 times"; the Overview's recurring confusions include repeated
    "who is this?" per person. Asking more than once in a call reinforces that person's relationship memory
    (`identity_asks`, logged as `identity_asked` in `memory_events`), so for 30 days the card and the next brief add
    identity detail ("your granddaughter, Rahul's daughter").
- **Relative's side:** consent screen, then a side panel: what the patient's card says, talking points, the family's
  hints (e.g. how to answer about an archived person), receiver notes, and what the patient's memory has about them
  with 👍/👎 (the feedback that used to sit on the patient's card).
- **Live recall cards** ([recall.py](backend/app/recall.py)): every ~10 s the last ~30 s of the relative's stored
  transcript (and its last few lines, one batched embed) is searched with `$vectorSearch` on this patient, this contact,
  `active`, `patient`-visible. Above 0.74 (Voyage) or 0.66 (local) a small card shows for 10 s; at most one a minute,
  never the same memory twice or the card's own memory. Logged on the call (`recall_cards`). No LLM.
- **Transcription:** each side's browser transcribes its own mic and sends speaker-tagged chunks. Live captions appear
  on both screens. "Type instead" works when speech recognition isn't available.
- **Off the record:** the relative can pause storage, and nothing said during that time is saved.
- **Feedback:** 👍/👎 on what the patient's memory has about the relative (on the relative's panel, authorised by the
  join token) changes each memory's importance (see 4.7). The patient's card no longer asks the patient to judge.
- **Media errors:** if the camera or mic is blocked (no HTTPS, permission denied, in use, missing), the screen says
  why instead of failing silently.

### 4.5 After the call: the memory agent (write path)

[pipeline.py](backend/app/agent/pipeline.py)

1. **Extract.** The LLM reads the speaker-tagged transcript and what is already known about the contact. It returns
   a family summary, a gentle recap for the patient, and atomic *candidate memories*. Each candidate has a type,
   speaker, quote, confidence, importance, an absolute event date, and flags for anything sensitive.
2. **Find neighbours.** All candidates are embedded in one batch, then searched with `$vectorSearch` on the same patient
   and contact. Same-type facts and relationships are always included. A candidate with no close neighbour is added
   without asking the LLM.
3. **Reconcile.** In one LLM call, each candidate gets ADD, UPDATE, SUPERSEDE, IGNORE (which reinforces the existing
   memory) or CONFLICT.
4. **Trust guard, enforced in code** (`may_override`). Trust levels:

   | Source | Trust |
   |---|---|
   | Caregiver | 1.0 |
   | Contact speaking about themselves | 0.85 |
   | Contact speaking about others | 0.7 |
   | Patient | 0.4 |

   The guard applies these rules:
   - The patient's claims never overwrite anything.
   - Only the caregiver changes relationships.
   - A contact is the authority on their own life: Riya's "I moved to Pune" supersedes the caregiver's older "lives in
     Mumbai".

   Anything the guard blocks becomes a `pending_review` CONFLICT. *In this domain, the user is the least reliable
   source.*
5. **Lifecycle on write:**
   - Events and plans get `expires_at` = event date + N days. Topics expire after 14 days.
   - Sensitive items are saved as `never_surface`.
   - The patient's own claims are `caregiver_only`. Their claims about facts and relationships always wait for review.
   - Low-confidence items wait for review.
6. **Audit:** every action goes to `memory_events`, which stores ids and actions only, never content.

### 4.6 Before the call: the brief (read path)

[brief.py](backend/app/agent/brief.py)

- **Candidate pool:**
  - (a) the contact's relationship and core facts;
  - (b) "open loops": events and plans within ±3 days of today;
  - (c) `$vectorSearch` filtered by owner, contact, `status: active` and `visibility: patient`, using a query built from
    the contact and the last call's recap.
- **Re-ranking:** `final = 0.5·similarity + 0.2·recency + 0.15·importance + 0.15·trust (+0.3 open-loop boost)`, where
  `recency = exp(-days/14)`. The relationship memory is always shown, followed by the best five others.
- **Composition:** the LLM writes the card (headline, talking points, speech text) from the selected memories and the
  last two patient-facing recaps. Sensitive memories go in only as *guardrails* ("do not mention"), and are never shown.
- **Explainability:** each brief is stored with every candidate's score breakdown, the weights, the guardrail ids and
  the calls used. The Briefs tab and Memory inspector show these, so you can see why each item was or wasn't picked.

### 4.7 How memory changes behaviour

| Memory | Visible change |
|---|---|
| New facts from a call | The next brief's text changes (Mumbai → Pune) |
| Event with a date | Gets the open-loop boost near the date ("Ask how the physics exam went") |
| Constraint | Blocks or delays the next call |
| 👍/👎 feedback | Changes importance (+0.05 / −0.1), so the item ranks differently next time (a learned strategy) |
| Caregiver correction | Rejects or replaces a memory. The overlay refreshes live, even mid-call |
| Time | The TTL index deletes expired events, and the dashboard feed shows "TTL expired" |

### 4.8 Caregiver dashboard

[Caregiver.jsx](frontend/src/pages/Caregiver.jsx) and the [caregiver/](frontend/src/pages/caregiver/) folder.

| Tab | What it does |
|---|---|
| **Overview** | Insights from aggregation pipelines: calls per day and hour, who the patient talks to, totals versus the previous period, memory bank by type, upcoming events, recurring confusions. Also a live change-stream feed and the live transcript |
| **Review queue** | Conflicts, patient claims and low-confidence memories. "Keep existing (reject)", "New one is right (replace)", edit and approve, or "Approve as private" |
| **Memory inspector** | Every memory with its source quote, trust, confidence, importance, supersede chain, conflicts and audit log. Correct, approve, reject, change visibility, or forget (hard delete) |
| **Briefs** | Every brief with its full re-ranking table. "Preview brief now" shows what the patient would see if they called someone now |
| **Calls** | History including blocked attempts, summaries and the agent's actions per call. "Run memory agent again" reprocesses a call |
| **Contacts & rules** | Add people (with the name the patient uses). Copy or replace each person's contact link. **Remove…** offers *Archive (passed away or no longer in contact)* with a note for relatives, or *Delete permanently*. Archived list with note editing and unarchive. Approve or decline contact requests. Add constraint rules and caregiver notes |
| **Calls** (additions) | No answer / declined / hung up statuses, "who is this?" counts and event times, recall cards shown |
| **Family & devices** | The patient's language, members and roles, invite codes, the patient's own sign-in, tablet pairing and unpairing, access log |

The sidebar shows the live status of change streams, vector search, the agent's mode (groq / claude / offline),
embeddings, auth mode, and whether the patient's device has a voice for their language.

### 4.10 Archived people (the fifth difficult case)

Someone the patient asks for may have died. Archiving hides their photo, refuses calls gently (never "they died"),
sets their memories to `caregiver_only` while keeping `visibility_before_archive` so unarchiving restores each one,
and stores the family's note on how to answer. That note reaches other relatives before and during their calls, never
the patient. The seeded demo family has Aaji's late husband Madhav ("Baba") archived, with two refused voice requests.

### 4.11 The patient's language

Caregivers choose English, Hindi, Marathi or Gujarati under Family & devices. The patient's screens use it for speech
recognition, read-aloud, the spoken phrases and the "who is this?" rules. The brief prompt writes the card and
`relation_phrase` in that language (talking points stay English for the family), and the extract prompt writes the
patient recap in it. Without an installed voice, read-aloud falls back to `en-IN` and the sidebar says so. The offline
agent stays English. For the demo family there is a **demo clock** (+1 day, +2 days, real time) and **Reset
demo**.

### 4.9 Care circles

- A caregiver creates a circle for a patient, or joins one with an 8-letter invite code or link, or opens the demo
  family. A self-registered patient can also start a circle by inviting family.
- A circle always keeps at least one owner. Role changes take effect within 30 seconds (the server's principal cache)
  and immediately for the person who made the change.

---

## 5. Data model (MongoDB)

| Collection | Purpose | TTL |
|---|---|---|
| `users` | Patients (`role: patient`, optional `firebase_uid` = own login) and caregivers | No |
| `memberships` | Caregiver ↔ patient with role | No |
| `devices` | Paired anonymous tablets ↔ patient | No |
| `pairing_codes` | 6-digit tablet codes | 15 minutes |
| `invites` | Family invites and patient sign-in codes | 7 days |
| `auth_events` | Access log | 90 days |
| `contacts` | People in the patient's circle (active / pending / archived); `known_as`, `link_token`, `archive_note`, `user_id` (the relative's own account) | No |
| `calls` | Calls, including blocked, unanswered, declined and cancelled attempts; summary, recap, agent actions; `overlay` counts and events, `also_here`, `recall_cards`, `accepted_at`; family calls: `family`, `rung`, `declined_by` (and `contact_id` null until claimed) | No |
| `contact_updates` | Written updates from a contact link, with the agent's actions | No |
| `transcript_chunks` | Speaker-tagged transcript lines | 30 days |
| `memories` | The memory documents (below) | `expires_at` |
| `briefs` | Every brief with its score breakdown | No |
| `memory_events` | Audit log (ids and actions only) | No |
| `embedding_cache` | Voyage results, so the same text is never embedded twice | 30 days |

**Memory document:**
- **Who and what:** `owner_id`, `contact_id`, `type` (relationship · fact · event · plan · preference · constraint ·
  topic · sensitive · feedback), `content`, `embedding` + `embedding_model`.
- **Where it came from:** `source` {kind, origin, call_id, speaker, quote}.
- **How much to believe it:** `trust`, `confidence`, `importance`.
- **Lifecycle:** `status` (active · pending_review · superseded · rejected), `visibility` (patient · caregiver_only ·
  never_surface), `supersedes` / `superseded_by` / `conflicts_with`, `version`, `event_date`, `expires_at`.
- **Usage:** `retrieval_count`, `last_retrieved_at`, `feedback`. Constraints also have a structured `rule`.
- **New:** `source.kind` can be `contact_update` (with `update_id`); `identity_asks` / `last_identity_ask_at` on
  relationship memories; `visibility_before_archive` while a contact is archived.
- **Users (patients):** `language`, `speech_status` (what the patient's device reported).

**MongoDB features and where they are used:**
- **Vector Search:** index `memory_vec`, filter fields `owner_id`, `contact_id`, `type`, `status`, `visibility`,
  `created_at`, `embedding_model`. Used by the agent's neighbour search, the brief (including a "recent topic" query
  pre-filtered on `type` and `created_at`), and live recall cards (pre-filtered on `type`).
- **TTL:** memories, transcripts, pairing codes, invites, access log, embedding cache.
- **Change streams:** on `memories` (with pre-images, so TTL deletes still show what expired) and on `calls`. Relayed
  to dashboards and used to refresh the patient's overlay mid-call. If unavailable, the app emits the same events
  itself.
- **Aggregation:** `$facet`, `$lookup`, `$dateTrunc` power the Overview insights.

---

## 6. External services and fallbacks

| Piece | Used now | Fallback |
|---|---|---|
| LLM (extract, reconcile, brief) | Groq `openai/gpt-oss-120b` with strict JSON-schema output (`GROQ_API_KEY`). Claude `claude-opus-5-5` when `LLM_MODE=claude` | Rule-based agent and template brief (`agent/offline.py`). Bad keys switch to offline automatically |
| Embeddings | Voyage `voyage-3.5`, 1024 dims, Atlas-issued key (`VOYAGE_BASE_URL=https://ai.mongodb.com/v1`) | Local hashed embedding of the same width. Re-embedded with Voyage in the background later |
| Retrieval | Atlas `$vectorSearch` with pre-filters | Exact cosine scan in the app |
| Live updates | Change streams | Events emitted from app code |
| Speech-to-text | Web Speech API (Chrome) | "Type instead" box |
| Sign-in | Firebase (project `uncharted-d58fe`) | Dev auth mode with no password check, for local development only |

Groq free-tier notes: 8,000 tokens per minute, and `max_completion_tokens` counts against that before the call runs,
so it is capped at `GROQ_MAX_TOKENS=4000`. A rate-limited request waits and retries up to 3 times (when the wait is
30 s or less). Measured on real prompts: extract about 5 s, reconcile about 1 s, brief about 1 s.

---

## 7. Track requirements checklist

| Requirement | Where |
|---|---|
| Persistent memory as documents with owner, type, content, source, timestamps, lifecycle | `memories` (section 5) |
| Decide what to remember, retrieve, update, forget | Agent write path (4.5), brief (4.6), TTL and forget |
| Vector Search with filters | `memory_vec` pre-filters on owner, contact, status, visibility, model |
| TTL index for expiry | `memories.expires_at` and five other TTL indexes |
| Change streams for live multi-user updates | Dashboard feed, mid-call overlay refresh |
| Memory visibly changes behaviour | Brief text, open-loop boost, repeat-call guard, feedback re-weighting (4.7) |
| Inspectable, explainable memory | Memory inspector, stored briefs with per-item scores |
| A difficult case | Five: conflicting sources (trust guard), caregiver correction, private information, expiry, and **someone the patient asks for has died** (archive: hidden, refused gently, family note to relatives, restorable) |

---

## 8. Testing so far

- **Headless demo check:** `backend/scripts/smoke.py` (dev-auth mode). It plays the whole demo through the API and
  asserts supersede, conflict, TTL fields, never-surface, the guard, the later brief and the score breakdowns.
- **Groq agent on real prompts:** extract found the sensitive item and event dates. Reconcile returned CONFLICT when
  a lower-trust source tried to override the caregiver. The brief left out the private surgery note.
- **Patient sign-in API flow** (dev auth, local MongoDB): linking a code, refusing code reuse and family accounts,
  self sign-up, a patient inviting family as owner, unlinking, and the access log.
- **Screens checked in a headless browser over HTTPS:** sign-in, email sign-up, profile step, and an empty patient home.
- **Frontend build:** clean.
- **spec-1 features** (`smoke.py`, dev auth, local MongoDB, run offline + local embeddings and with Groq + Voyage):
  the patient card (name tag from the contact, one memory, no talking points), relative view, voice "who is this?"
  counted and reinforcing identity, cancelled calls, home recap line and no tokens on the tablet, contact link page,
  written update through the pipeline, ringing + decline + unanswered over real WebSockets, link regeneration,
  archive / refusal / hints / unarchive restore, Hindi trigger and language, speech status, read-aloud dropped from both
  sides, recall card, "also here" and overlay logging. Offline: all pass. Groq + Voyage: all pass on two runs in a row;
  the recall card and the "recent topic" check are skipped when the free Voyage tier rate-limits a query.
- **Relatives and the family call** (`smoke.py` section 11, offline): nobody online is told at once; account linking and
  conflict (409); a relative's account denied caregiver and tablet routes and the demo clock; no private data on the
  relative page; the tablet never sees accounts; a caregiver who is also a relative keeps both views; quiet hours skip
  a relative; second tap is the same call; all-decline message; two relatives accepting at once gives exactly one
  winner and the other's ring stops; the patient's card and brief follow the claimed contact; the Calls list and
  insights cope with unclaimed calls; unanswered family call after the timeout; unlink. Also walked through in a
  headless browser: sign in from a contact link keeps it, `/family`, the patient's one button, ringing on two relatives'
  pages, Accept, consent, connected call with the right card.
- **Browser walk-through** (headless Chromium, fake camera/mic, dev auth): home tiles with recap lines, contact link
  waiting room, ringing, accept, consent, live call on both sides, card expand → name tag after 20 s, recall card,
  "also here", "Updated just now" after a caregiver edit, dark mode, end of call and the tile refreshing live,
  long-press family menu, Contacts (links, archive dialog), Calls (overlay counts, recall cards), Overview, Family
  (language).

---

## 9. Known gaps and next steps

**Before the demo**
1. Commit the patient sign-in work and the spec-1 work, push, and have Pratham pull. After pulling, Pratham needs to:
   - run `npm install` in `frontend` (SSL plugin) and `pip install -r requirements.txt` in `backend` (httpx);
   - add their own `GROQ_API_KEY` if they host the backend.
2. Test a **real two-laptop call** over `npm run dev:https`, with `PUBLIC_BASE_URL=https://<host-ip>:5173` and the
   self-signed certificate accepted on laptop B.
3. Test **real Firebase patient sign-in** (Google and email), the claim link, and "Invite my family".
4. Rehearse the demo script in the README three times, and record a backup video.

**Family call and relatives' accounts**
- Ringing needs the relative's page open. There is no push notification, SMS or email, so a relative who isn't on
  the page isn't rung. Adding push or SMS is a new external service (needs a decision).
- Relatives can't start a call to the patient; only the patient places calls.
- The relative's page leaves out the "Recently believed" notes (they come from `caregiver_only` memories). The per-call
  join page still shows them (the open question below).
- A contact can be linked to one account at a time; a caregiver unlinks it to move it.

**Known gaps (spec-1)**
- Receiver notes show patient beliefs ("Recently believed: …"), which are `caregiver_only` memories, to the relative.
  This was an existing feature the spec asks to keep on the relative's side, but it conflicts with the rule that
  `caregiver_only` never reaches a relative. Needs a decision.
- Voice calling by name in Hindi/Marathi/Gujarati only matches if the name is written in that script: add nicknames
  in Devanagari/Gujarati (e.g. "रिया") under Contacts.
- Before the brief arrives, the relation on a non-English card is English ("your granddaughter"); it switches to the
  composer's wording when the brief is ready.
- The ring tone needs one tap ("I'm ready") per page load because browsers block audio without a gesture.
- Live recall with a free Voyage key: 3 requests a minute, so many ticks fall back to local query vectors, which never
  match Voyage-embedded memories; the loop then shows nothing (by design).
- Ringing and timers live in the backend process: a restart forgets ringing calls (they stay "ringing" in the list).
- A demo database seeded before these changes gets contact links lazily, but needs **Reset demo** for the archived
  husband and the seeded "who is this?" history.

**Not built (from the design doc's stretch list)**
- "My Day" journal for the patient.
- A "forget what I said about…" voice command for the relative.
- Group calls, weekly caregiver digest, kiosk auto-answer, face recognition of visitors.

---

## 10. Ethics and safety (design decisions)

- Everyone sees a consent notice, and relatives can go off the record.
- Audio and video are never stored. Transcripts expire after 30 days.
- The patient's statements are never treated as fact, so the app doesn't reinforce confusion.
- Distressing or private information never reaches the patient.
- Unknown people need caregiver approval before they can be called (scam protection).
- Forgetting is a hard delete, and the audit log keeps no content.
- Pehchaan is a communication aid, not a medical device.
