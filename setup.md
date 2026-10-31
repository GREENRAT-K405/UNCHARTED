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
| `ANTHROPIC_API_KEY` | No | Without it the rule-based offline agent is used |
| `VOYAGE_API_KEY` | No, but recommended | Without it a local hash embedding is used, so semantic recall is much weaker |

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

For a two-person call, open the patient screen in one Chrome window and the contact's join link in a second window or Chrome profile. Use **Reset demo** on the dashboard to restore the seed data.

## 6. Verify

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

## Troubleshooting

- **Port 27017 in use:** stop any local `mongod`, or change the port mapping in `docker-compose.yml` and `MONGODB_URI`.
- **Vector search falls back to cosine:** the index can take a few seconds to build. Restart the backend if it never becomes queryable. Plain `mongod` does not support it; use the Docker image or Atlas.
- **No change streams:** they require a replica set. Atlas and the Atlas-local image provide one.
- **PowerShell blocks scripts:** run `Set-ExecutionPolicy -Scope Process Bypass` if you activate the venv manually.
- **Speech recognition not working:** use Chrome and allow microphone and camera access.
