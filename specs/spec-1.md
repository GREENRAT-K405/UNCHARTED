# Pehchaan: overlay, call flow and role features

You are working on Pehchaan (repo: GREENRAT-K405/UNCHARTED), a video-calling companion for people living with
Alzheimer's, built for the MongoDB Student Hackathon (Track 1: Agentic Memory). Three kinds of users:
- Patient: taps a photo or says "Call my son". Should never have to make decisions or manage anything.
- Caregiver: sets everything up and controls the memory (dashboard).
- Relative: joins calls, gets briefed on what to talk about, and contributes updates about their own life.

The demo is soon. Focus on working functionality. Security hardening is out of scope for now.

## Before you write any code
1. Read `progress.md` fully, then `docs/DESIGN.md`, `README.md` and `setup.md`.
2. Read the files you will touch: `backend/app/auth.py`, `routes_auth.py`, `main.py`, `realtime.py`,
   `agent/pipeline.py`, `agent/brief.py`, `agent/guard.py`, `agent/offline.py`, `backend/scripts/smoke.py`,
   the seed script, and `frontend/src/pages/PatientGate.jsx`, `CallPage.jsx`, `JoinPage.jsx`, `Caregiver.jsx`
   and `frontend/src/pages/caregiver/`.
3. Run `git status`. There is uncommitted patient sign-in work. Check `npm run build` passes and `smoke.py` passes
   in dev-auth mode, then commit it on its own as
   "Patient sign-in: own account, family sign-in code, invite family, unlink".
4. Tell me briefly how the overlay currently gets its data, when it shows, and what is read aloud.
   Then give me a short plan per task (files, any schema changes) and implement the tasks in order.

## Ground rules
- Don't break anything marked "Done" in progress.md. The seeded demo family and the README demo script must work
  after every task.
- Anything that calls the LLM must also work in offline mode (`agent/offline.py`); anything that embeds must work
  with the local embedding fallback.
- `never_surface` memories never appear on the patient's screen or the relative's screen. `caregiver_only`
  memories never appear on the patient's or relative's screen.
- Reuse existing patterns: the memory pipeline and trust guard, `memory_events`, change streams with the
  app-emitted fallback, existing auth helpers.
- Don't touch `.env` files or keys. Don't push. Commit after each task with a clear message.
- Add a few `smoke.py` assertions per task for the backend parts. Keep the frontend build clean.
- Ask me before deleting or migrating existing data or adding a new external service.

## Task 1. The "Who is this?" overlay (the core feature)

Principles for everything in this task:
- Always give the answer, never test the patient. No "Do you remember…?" anywhere.
- A wrong name is worse than no name. If identity is unclear, show nothing.
- The patient's card is short: who, relation, one memory. Everything else goes to the relative's side.

1a. Identity
- The caller's identity comes from the call's contact (per-call token now, contact link from Task 2).
- Show the name tag only once the remote video is connected, not over a black or loading screen.
- Use the name the patient uses (nickname if set), the relation from the patient's point of view ("your grandson"),
  and the contact's stored photo as a small thumbnail.
- No resolvable contact means no overlay.

1b. "Also here" (instead of face recognition)
- On the relative's call screen, add "Who else is with you?" to pick people from the patient's active contacts.
  The patient's overlay then shows "Also with Rahul: Priya, his wife". The relative can remove them.

1c. What the patient's card shows
- Name, relation, and at most one memory: the top-ranked non-relationship item from the stored brief.
  Read-aloud says the same thing, nothing more.
- Talking points, receiver notes and "avoid this topic" hints go only to the relative's side.
- The stored brief and its score breakdown stay as they are; only presentation changes.

1d. If the brief isn't ready
- Show the name tag immediately from the contact and relationship memory (no LLM needed). Add the memory line when
  the brief arrives. If the LLM is slow or offline, use the template brief. The patient never sees a spinner,
  error or empty card.

1e. Timing and display
- On connect: show the full card large for about 20 seconds, then collapse to a small persistent name tag at the
  bottom edge of the remote video. Never cover the caller's face.
- Read aloud once, about 2 seconds after connecting, so it doesn't talk over the first "hello".
- Big text, high contrast, readable over any video, works in light and dark mode.

1f. Bringing it back
- A large "Who is this?" button on the patient's call screen re-expands the card and reads it again.
- The patient saying "who is this", "who are you", "who am I talking to", "what is your name" or "do I know you"
  does the same. Detect with simple rules on the patient's own transcript chunks, no LLM.

1g. Keep the read-aloud out of the transcript
- While speech synthesis is speaking, pause the patient's speech recognition, or drop chunks recognised during that
  window plus about 1 second. Nothing the app reads aloud may reach the transcript or memories.

1h. Caregiver edits mid-call (already built; adjust)
- When a change stream rebuilds the brief mid-call, update the card text silently with "Updated just now".
  Don't re-read or re-expand unless the patient asks. Update the name tag if the person on screen was changed.

1i. Overlay events feed back into memory
- Record counts and times on the call document: card shown, re-opened by button, re-opened by voice,
  "also here" changes.
- Show them in the Calls tab ("Asked who this was 3 times") and include repeated "who is this?" per contact in the
  Overview's recurring-confusions insight.
- If the patient asks "who is this?" more than once in a call, slightly raise the importance of that contact's
  relationship memory and log it in `memory_events`, so the next brief leads with clearer identity information.

## Task 2. Persistent contact link and incoming-call ring
The join link's per-call token is created when the call is placed, and nothing delivers it to the relative.
- Give each contact a persistent random contact token. Route: `/c/:contactToken`. Caregivers can copy and
  regenerate it in Contacts & rules.
- The link opens a waiting room ("Waiting for Aaji's call") with an "I'm ready" button (so the browser allows ring
  audio) and registers presence over the existing WebSocket.
- When the patient calls that contact, push an incoming-call event to their connected sessions: ringing screen with
  Accept / Decline. Accept goes into the existing join flow (consent, receiver notes, call) automatically.
- No answer in 30 seconds: the patient sees a calm message ("Riya is busy right now. Let's try again later.")
  and a big button home. Never an error-style screen. Record the call as unanswered.
- Keep `/join/:callId` working.

## Task 3. Simplify the patient screen
- On `/patient`, make "Family: set up this screen" (6-digit pairing, and the one-tap option when a caregiver is
  signed in on the same browser) the main path. Keep Google/email sign-in and "New here?" as a smaller secondary
  option.
- Move "Invite family" and "Sign out" off the visible home screen into a "Family menu" opened by a ~3 second
  long-press on a small corner icon. The home screen shows only photos, the mic and the emergency button.

## Task 4. Memory on the patient's home screen
Under each photo tile, show one line from the latest patient-facing recap with that contact, with relative time:
"Talked yesterday · Riya told you about Pune." Large and plain, patient-visible content only, updated live through
the change stream when a call finishes.

## Task 5. Relative page (same contact link, when no call is ringing)
- "Share an update" box ("I got engaged", "My exam is on Friday"). It goes through the existing memory pipeline as
  the contact speaking about themselves (trust 0.85), with extract, reconcile, trust guard, sensitive-flagging and
  event dates. Reuse the pipeline. Source recorded as `contact_update` so the Memory inspector shows it.
- Receiver notes, readable before a call.
- Their own past calls: date and patient-facing recap.

## Task 6. Archive a contact (relative who has died or left)
Removing a contact currently deletes all their memories. Add "Archive (passed away or no longer in contact)" next to
"Delete permanently" in Contacts & rules.
- Archived: tile hidden, calls refused by the guard (tap or voice), memories set to `caregiver_only` with their
  previous visibility stored so unarchive restores it.
- The caregiver writes a note on how the family answers when the patient asks for this person. Briefs for other
  contacts include it as a hint on the relative's side.
- If the patient asks by voice to call an archived person: don't call, don't say they died, show a gentle screen
  offering to call someone else.
- Add an archived late family member to the seeded demo family. Add this to progress.md section 7 as the fifth
  difficult case.

## Task 7. Patient's language
`language` is stored but speech recognition and read-aloud are fixed to `en-IN`.
- Caregivers set it (English, Hindi, Gujarati, Marathi) on the patient profile.
- Use it for Web Speech recognition and speech synthesis; if no voice is installed, fall back to `en-IN` and show
  why in the caregiver sidebar status.
- Pass it to the brief and patient-recap prompts so patient-facing text is in that language. Add the matching
  "who is this?" phrases for Task 1f. Offline mode can stay English.

## Task 8. Small fixes
- README: the offline-fallbacks table and quick start still say Claude is the default LLM; Groq is.
- Use the `type` and/or `created_at` filter fields of `memory_vec` in at least one real vector query
  (e.g. open loops or recent topics in the brief).

## Task 9. Live recall cards (stretch: only if Tasks 1–8 are done and working)
- Every ~10 seconds, embed the last ~30 seconds of the relative's transcript and `$vectorSearch` filtered to this
  patient, this contact, `status: active`, `visibility: patient`.
- Above a similarity threshold, show a small card on the patient's screen for ~10 seconds
  ("Goa trip, 2019: you all went together"). At most one card per 60 seconds, never the same memory twice per call.
- Embeddings only, no LLM; use the embedding cache. Offline: keyword match or skip.
- Log shown cards on the call document so caregivers can see them.

## When you finish
- Update `progress.md`: status table, timeline, changed design sections, data model changes, testing, known gaps.
- Give me a short report per task (what changed, schema changes, anything skipped) and a manual test checklist:
  real two-laptop call over `npm run dev:https`, real Firebase sign-in, ringing on a second device, overlay timing,
  "Who is this?" button and voice trigger, read-aloud not appearing in the transcript, long-press menu on touch,
  voices per language.