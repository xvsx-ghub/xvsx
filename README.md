# xvsx — Shelfa

Production stack: **Internet → Nginx → Gunicorn (Uvicorn workers) → FastAPI (Shelfa)**

```
Internet
   |
 Nginx (reverse proxy, port 80)
   |
 Gunicorn + UvicornWorker (port 8000, internal)
   |
 FastAPI app (Shelfa chat API)
```

## Quick start (Docker)

```bash
docker compose up --build
```

- http://localhost/ — Shelfa home
- http://localhost/health — health check
- http://localhost/docs — OpenAPI docs

## Local development (Uvicorn only)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Production (without Docker)

1. Install dependencies: `pip install -r requirements.txt`
2. Run Gunicorn: `gunicorn app.main:app -c gunicorn.conf.py`
3. Point Nginx at `127.0.0.1:8000` (see `nginx/nginx.conf`)

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `GUNICORN_BIND` | `0.0.0.0:8000` | Gunicorn listen address |
| `GUNICORN_WORKERS` | `2 × CPU + 1` | Worker processes |
| `GUNICORN_TIMEOUT` | `120` | Request timeout (seconds) |
| `GUNICORN_LOG_LEVEL` | `info` | Log level |
| `DATA_DIR` | `./data` | SQLite DB and uploads directory |
| `FIREBASE_CREDENTIALS_PATH` | `./shelf-*-firebase-adminsdk-*.json` | Firebase service account for push notifications |

## Project layout

```
.
├── app/
│   ├── main.py              # FastAPI entry point
│   └── shelfa/              # Shelfa chat application
│       ├── config.py
│       ├── database.py
│       ├── routers/
│       ├── services/
│       └── templates/
├── data/                    # Runtime data (DB + uploads)
├── nginx/
│   └── nginx.conf
├── gunicorn.conf.py
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```
