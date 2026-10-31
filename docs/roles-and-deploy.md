# Roles, face matching and deployment

Pehchaan has three kinds of people. Everyone signs in at `/login` (Google or email) and lands on their own screen.

| Role | Lands on | What they can do |
|---|---|---|
| **Caretaker** (admin) | `/caregiver` | Add and remove relatives (name, email, phone, relation, photo), schedule meets, read every past meet with who/notes/important discussion, edit rules and memories, pair the patient's tablet or set the patient's Google email |
| **Patient** | `/patient` | Sees only upcoming meets and a big **Join meet** button. Join meet asks: *Invite all relatives* or *New meet*. Signs in with Google (email set by the caretaker) or a tablet pairing code |
| **Relative** | `/relative` | Sees live invites from the patient (Join now), scheduled meets, past meets, and can join with a 6-character code. A relative is recognised by the **verified** email the caretaker entered |

Calls are strictly 1-to-1. A second person who tries to join a meet that is already full gets a "meet is full" screen.

## The call screen

* **Top**: a popup about the person on the other end: name, photo, relation, last meet date, what they talked about last time, and the caretaker's note. It is read aloud to the patient.
* **Bottom**: the meeting code, **Share invite link** (native share sheet / WhatsApp), **Copy link** and **Invite all relatives**.
* A guest (not signed in) can join with the code or link: they enter a name and tick a consent box.

## Face matching

The browser computes a 128-number face descriptor from the other person's video (`@vladmandic/face-api`, models served from `frontend/public/models`, ~13 MB, loaded only on call and photo screens). The server decides who it is (`backend/app/faces.py`).

1. The caretaker adds a relative with 1 to 8 photos. Each photo must contain exactly one clear face; the app refuses blurry, tiny or multi-face photos and refuses a photo that looks too much like another relative (the caretaker can override if they really look alike).
2. During a call the patient's device gathers 4 good frames, then asks `POST /api/calls/{id}/identify`.
3. The server compares the mean descriptor with every stored face of that patient's relatives:
   * distance ≤ 0.45, at least 60% of frames agree and the runner-up is at least 0.06 further away → **matched**: the popup shows the person and what happened last time.
   * distance between 0.45 and 0.60 → **unsure**: the popup says so and shows the closest candidate; it never claims a name.
   * otherwise → **new person**: the popup says "first meeting" and the server saves them as an **unverified guest** with their snapshot. The caretaker approves or deletes them from Relatives.
4. Faces are rechecked every 30 s, so if the person on the camera changes the popup changes.

Accuracy test on six sample photos (same person under blur/brightness/crop stress vs different people):

| Threshold | Strangers wrongly named (family of 8) | (gallery of 19) | Real matches kept |
|---|---|---|---|
| 0.50 | 7.2% | 19.3% | 100% |
| **0.45 (default)** | 1.8% | 6.5% | 100% |
| 0.42 | 0% | 0% | 100% |

Set `FACE_MATCH_THRESHOLD` (default 0.45) lower for stricter matching. Distances from real webcams are higher than from the same source photo, so enrol 3 to 5 photos per person in different light. The caretaker's **Test a face** panel on the Relatives tab runs the matcher on their own webcam.

## Deploying on Render (single service)

The `Dockerfile` builds the React app and serves it from FastAPI, so there is one service and no CORS.

1. **MongoDB Atlas**: create a cluster, a database user, and under Network Access allow `0.0.0.0/0` (Render has no fixed IP). Copy the connection string.
2. **Firebase** (Authentication → Sign-in method): enable Google and Email/Password, and Anonymous (tablet pairing). Under Settings → Authorized domains add your `*.onrender.com` host.
3. Push the repo to GitHub. In Render choose **New → Blueprint** and select it; `render.yaml` defines the service.
4. Fill in the environment variables Render asks for:

| Variable | Value |
|---|---|
| `MONGODB_URI`, `MONGODB_DB` | Atlas connection string, database name |
| `FIREBASE_PROJECT_ID` | project id (the backend verifies ID tokens against Google's public certs; no service-account key is needed) |
| `FIREBASE_WEB_API_KEY`, `FIREBASE_APP_ID` | from the Firebase web app config; injected into the page at runtime, no rebuild needed |
| `PUBLIC_BASE_URL` | `https://<your-service>.onrender.com` (used in invite links) |
| `VOYAGE_API_KEY`, `VOYAGE_BASE_URL` | embeddings (optional; a local fallback is used otherwise) |
| `ANTHROPIC_API_KEY` | memory agent (optional; a rule-based fallback is used otherwise) |
| `TURN_URLS`, `TURN_USERNAME`, `TURN_CREDENTIAL` | optional TURN server (see below) |

5. Open the URL, sign in as the caretaker, open **Family → demo** (or create a circle), and press **Reset demo** once to load the sample data.

Notes:

* Run **one** uvicorn worker (the Dockerfile does): the signalling hub is in memory.
* Render's free plan sleeps after inactivity; the first request wakes it in ~30 s. Use a paid instance for a live demo.
* Camera access needs HTTPS, which Render provides.
* **Video through strict networks** needs a TURN relay. Without `TURN_URLS` the app falls back to the public openrelay test server, which is fine for a demo but not for production; set your own (Metered, Twilio, coturn).
* Never commit `.env`, a Firebase service-account JSON, or the Atlas password. They are git-ignored; put them only in Render's environment settings.

## Testing it yourself

```bash
python backend/scripts/smoke.py http://localhost:8000   # memory loop, guard, briefs (dev auth mode)
```

The face matcher is exercised in the browser: add a relative with a photo, open the patient screen in one window and join the meet by code in another window, and watch the popup on the patient's side. Chrome's `--use-file-for-fake-video-capture` flag lets you play a photo as the webcam.
