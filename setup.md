# Pehchaan setup and run

Works on Linux, macOS and Windows. Commands that differ are given per OS.

## 0. Prerequisites

- Python 3.11+
- Node 18+ and npm
- Google Chrome (needed for in-browser speech recognition)
- Docker, or a MongoDB Atlas cluster (Vector Search, change streams and TTL are required)

Check versions:

```bash
python3 --version   # Windows: python --version
node --version
docker --version
```

## 1. Get the code

```bash
git clone <your-repo-url> UNCHARTED
cd UNCHARTED
```

## 2. Start MongoDB

### Option A: Docker (Atlas-local)

Linux / macOS / Windows (same command):

```bash
docker compose up -d
docker ps
```

`docker ps` should list a `mongodb/mongodb-atlas-local` container on port 27017. Leave `MONGODB_URI` at its default.

### Option B: MongoDB Atlas

Create a cluster, add your IP to the access list, and copy the connection string. You will put it in `backend/.env` in the next step.

## 3. Backend

### Linux / macOS

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

### Windows (PowerShell)

```powershell
cd backend
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
```

### Edit `backend/.env`

| Variable | Required | Notes |
|---|---|---|
| `MONGODB_URI` | Only for Atlas | Default targets the Docker container |
| `GROQ_API_KEY` | No, but recommended | The default LLM for the memory agent and briefs. Without it (and without Claude) the rule-based offline agent is used |
| `ANTHROPIC_API_KEY` | No | Only with `LLM_MODE=claude` |
| `RING_TIMEOUT_SECONDS` | No | How long a call rings before it counts as unanswered (default 30) |
| `VOYAGE_API_KEY` | No, but recommended | Without it a local hash embedding is used, so semantic recall is much weaker |
| `VOYAGE_BASE_URL` | Only for Atlas-issued keys | `https://ai.mongodb.com/v1` if the key came from Atlas Model API Keys |
| `FIREBASE_PROJECT_ID` | For real sign-in | e.g. `uncharted-d58fe`. Unset = local dev sign-in (no passwords checked). The backend needs only this id, never a service-account key |

### Run the backend

Linux / macOS:

```bash
.venv/bin/python -m uvicorn app.main:app --reload --port 8000
```

Windows (PowerShell):

```powershell
.venv\Scripts\python -m uvicorn app.main:app --reload --port 8000
```

In the startup logs, look for `vector index memory_vec is queryable`. If you see `Atlas Vector Search unavailable, using in-app cosine fallback`, MongoDB Vector Search is not active. Demo data (Aaji, Rahul, Riya) is seeded on first start.

## 4. Frontend

Open a second terminal at the repo root.

For Google sign-in, copy `frontend/.env.example` to `frontend/.env` and fill in the Firebase web app config
(Firebase Console → Project settings → Your apps → Web app). In Firebase Console → Authentication → Sign-in method,
enable **Google**, **Email/Password** and **Anonymous** (the patient's tablet signs in anonymously). Leave the file
empty for local dev sign-in. Backend and frontend must agree: the login page warns if only one side has Firebase.

Linux / macOS:

```bash
cd frontend
npm install
npm run dev
```

Windows (PowerShell):

```powershell
cd frontend
npm install
npm run dev
```

## 5. Open the app

- Landing page: http://localhost:5173
- Patient screen: http://localhost:5173/patient
- Caregiver dashboard: http://localhost:5173/caregiver
- Backend API docs: http://localhost:8000/docs

Sign in on the caregiver dashboard and choose **Explore the demo family**. Then open the patient screen. Either sign
in there with the patient's own account (create a sign-in code for Aaji under **Family & devices**, then type it in),
or pair it with the 6-digit number it shows (or the one-click button when signed in on the same browser).

For a two-person call, open the patient screen in one Chrome window and the contact's link (Contacts & rules → Copy
link, then **I'm ready**) in a second window or Chrome profile. It rings when the patient taps that person. Use **Reset demo** on the dashboard to restore the seed data.

### Two laptops on the same Wi-Fi

Browsers only allow camera and microphone on `https://` or `localhost`. A second laptop opening `http://<host-ip>:5173`
joins the call with no camera or mic, and the call screen shows a red "not a secure page" banner. Serve HTTPS instead.

On the host laptop only:

1. Set `PUBLIC_BASE_URL=https://<host-ip>:5173` in `backend/.env` (find the IP with `hostname -I` on Linux or
   `ipconfig` on Windows), then restart the backend so contact and join links use that address.
2. Run the frontend with `npm run dev:https` instead of `npm run dev`.
3. Open ports 5173 and 8000 in the firewall (Fedora: `sudo firewall-cmd --add-port=5173/tcp --add-port=8000/tcp`).

The other laptop runs nothing. It opens the contact's link (`/c/…`, copied from Contacts & rules) in Chrome, clicks
**Advanced → Proceed** on the certificate warning (the certificate is self-signed), presses **I'm ready**, and allows
camera and microphone when the call is accepted. The per-call join link still works too.

## 6. Verify

`smoke.py` signs in with dev tokens, so run it with `FIREBASE_PROJECT_ID` unset (dev auth mode).

Linux / macOS:

```bash
curl http://localhost:8000/docs
cd backend && .venv/bin/python scripts/smoke.py
```

Windows (PowerShell):

```powershell
curl http://localhost:8000/docs
cd backend; .venv\Scripts\python scripts\smoke.py
```

## 7. Single-port production build (optional)

```bash
cd frontend
npm run build
```

FastAPI then serves `frontend/dist` on http://localhost:8000.

## 8. Stop and clean up

```bash
docker compose down        # stop MongoDB, keep data
docker compose down -v     # stop MongoDB and delete all data
```

## 9. Deploy (Render backend + Vercel frontend)

**Render** (Web Service, repo root dir `backend`):
- Build: `pip install -r requirements.txt`
- Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Health check: `/api/health`
- Env: `PYTHON_VERSION=3.12.7`, `MONGODB_URI`, `MONGODB_DB`, `GROQ_API_KEY`, `VOYAGE_API_KEY`, `VOYAGE_BASE_URL`, `FIREBASE_PROJECT_ID`, `TIMEZONE`, and `PUBLIC_BASE_URL` = the Vercel URL.

**Vercel** (root dir `frontend`, Vite preset): env `VITE_FIREBASE_*` and `VITE_API_URL` = the Render URL (no trailing slash). The browser talks to Render directly, because Vercel rewrites can't carry WebSockets. `frontend/vercel.json` sends every route to the app.

Also: Atlas Network Access must allow `0.0.0.0/0`, and the Vercel domain must be in Firebase → Authentication → Authorized domains.

## Troubleshooting

- **Port 27017 in use:** stop any local `mongod`, or change the port mapping in `docker-compose.yml` and `MONGODB_URI`.
- **Vector search falls back to cosine:** the index can take a few seconds to build. Restart the backend if it never becomes queryable. Plain `mongod` does not support it; use the Docker image or Atlas.
- **No change streams:** they require a replica set. Atlas and the Atlas-local image provide one.
- **PowerShell blocks scripts:** run `Set-ExecutionPolicy -Scope Process Bypass` if you activate the venv manually.
- **Speech recognition not working:** use Chrome and allow microphone and camera access.
- **`voyage embed failed (RateLimitError)` in the logs:** free Voyage keys without a payment method allow 3 requests per minute. The app falls back to local vectors for that batch, never mixes them with Voyage vectors in a search, and re-embeds them within a minute or two. Adding a payment method removes the limit (free tokens still apply).
- **Sign-in fails with 401 / "invalid token":** `FIREBASE_PROJECT_ID` in `backend/.env` and `VITE_FIREBASE_*` in `frontend/.env` must point at the same Firebase project; restart both servers after changing them.
