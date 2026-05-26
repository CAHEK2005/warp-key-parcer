# WARP+ Key Orchestrator

Web system for collecting authorized WARP+ keys from Telegram sources and applying them to Ubuntu/Debian hosts over SSH with Ansible.

## Stack

- FastAPI backend with SQLAlchemy models and JWT auth.
- React/Vite operator UI.
- Postgres for state, Redis/Celery for work queues, APScheduler for recurring host updates.
- Ansible Runner plus `backend/scripts/warp_native_noninteractive.sh` for deterministic remote execution.

## Local development

```powershell
cd backend
python -m pip install -e .[test]
python -m pytest

cd ..\frontend
npm install
npm test
npm run build
```

## Docker Compose

Create `.env` from `.env.example`, change `WARP_SECRET_KEY` and `WARP_ADMIN_PASSWORD`, then run:

```powershell
docker compose up --build
```

The UI is exposed on `http://localhost:5173`, and the API is exposed on `http://localhost:8000`.

Default credentials when running without your own `.env` are:

- username: `admin`
- password: `change-me`

For production, create `.env` and set `WARP_ADMIN_PASSWORD` before starting Compose. If the Postgres volume already exists and you change `WARP_ADMIN_PASSWORD`, restart the backend; the stored admin password hash is updated from the env value on startup.

## Security notes

Only add Telegram channels where you have authorization to use the published keys. SSH private keys, Telegram credentials, and WARP key values are encrypted at rest using `WARP_SECRET_KEY`; changing that key makes existing encrypted values unreadable.
