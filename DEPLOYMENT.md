# DARE Backend — Deployment Guide

> **Just want to run it locally?** See [QUICKSTART.md](QUICKSTART.md) — the **Docker** path stands up
> the whole stack (API, worker, Postgres, Redis, Weaviate) in one command, or the **Local Python**
> path runs Django directly in a venv. **This guide is for deploying to a server** and is the fuller
> reference behind that quick start.

This guide covers two deployment paths:

1. **Docker path** — the simplest way to stand up the full stack; recommended for most deployments
2. **Bare-metal path** — for production hosts or environments where Docker is not available

For environment variable reference, see [docs/configuration.md](docs/configuration.md).

---

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.13.x | Required by the current backend dependency set |
| PostgreSQL | 16+ | Bundled in Docker via `pgvector/pgvector:pg16`; required for production |
| Redis | 6+ | Required for background jobs and Socket.IO pub/sub |
| Docker + Compose | 24+ | Recommended for single-server backend deployment |
| Node.js | 18+ | Only required if running the frontend on the same host |

---

## Single-Server Resource Minimums

| Profile | CPU | RAM | Storage | Notes |
|---|---:|---:|---:|---|
| Backend + Postgres + Redis + Weaviate | 2 vCPU | 4 GB | 30 GB SSD | Viable for low traffic, small uploads, and managed/external LLM APIs. |
| Recommended production baseline | 4 vCPU | 8 GB | 80 GB SSD | Better headroom for file processing, RQ jobs, Weaviate indexes, logs, and backups. |
| With local Ollama models | 8 vCPU | 24 GB+ | 150 GB SSD | Depends heavily on model size. The compose profile reserves 8 GB and caps Ollama at 16 GB by default. |

Plan extra disk for uploaded media, Postgres backups, Weaviate data, and model files. For production, monitor volume growth and keep database/media backups outside the host.

---

## Path 1: Docker Setup

The bundled `docker-compose.yml` provisions:

- **DARE web** (Django ASGI API + Socket.IO) on `${WEB_PORT:-8000}`
- **DARE worker** (RQ background worker)
- **Postgres + pgvector** on `${POSTGRES_PORT:-5432}`
- **Redis** on `${REDIS_PORT:-6379}`
- **Weaviate** on `${WEAVIATE_PORT:-8080}` and `${WEAVIATE_GRPC_PORT:-50051}`
- **Weaviate console** on `${WEAVIATE_CONSOLE_PORT:-8081}` when the `debug` profile is enabled
- **Ollama** on `${OLLAMA_PORT:-11434}` when the `llama` profile is enabled

### Steps

```bash
# 1. Clone and enter the repo
git clone <repo-url> dare-backend && cd dare-backend

# 2. Copy and edit environment file
cp .example.env .env
# Edit .env — see docs/configuration.md for the full variable reference.
# At minimum:
#   - OPENAI_API_KEY (or CLAUDE_API_KEY / GEMINI_API_KEY)
#   - DJANGO_SECRET_KEY (rotate from the example)

# 3. Build and start the backend stack
docker compose up --build -d

# 4. Create a superuser
docker compose exec web python manage.py createsuperuser

# 5. Check container health
docker compose ps
curl http://localhost:8000/api/health/
curl http://localhost:8000/api/ready/
```

The web container waits for Postgres and Redis, then runs migrations before starting Uvicorn. Static and media files are stored in named Docker volumes.

For local development overrides:

```bash
cp docker-compose.override.yml.example docker-compose.override.yml
docker compose up --build
```

To enable optional services:

```bash
docker compose --profile debug up -d weaviate-console
docker compose --profile llama up -d ollama
docker compose exec ollama ollama pull llama3.1:8b
```

---

## Path 2: Bare-Metal Setup

Use this path for production deployments or constrained environments.

### 1. System dependencies

**Ubuntu / Debian:**

```bash
sudo apt update
sudo apt install -y python3.13 python3.13-venv python3-pip \
    postgresql postgresql-contrib redis-server \
    build-essential libpq-dev
```

**macOS (Homebrew):**

```bash
brew install python@3.13 postgresql@16 redis
brew services start postgresql@16
brew services start redis
```

### 2. PostgreSQL

```bash
sudo -u postgres psql <<EOF
CREATE DATABASE dare;
CREATE USER dare WITH PASSWORD 'changeme';
GRANT ALL PRIVILEGES ON DATABASE dare TO dare;
ALTER DATABASE dare OWNER TO dare;
EOF
```

Set the corresponding values in `.env`:

```
USE_POSTGRES=True
DB_NAME=dare
DB_USER=dare
DB_PASSWORD=changeme
DB_HOST=localhost
DB_PORT=5432
```

### 3. Redis

Redis should be running on `localhost:6379` after install. Verify:

```bash
redis-cli ping   # → PONG
```

### 4. Vector store

DARE supports two vector backends — pick one or run both:

- **Weaviate** — self-hostable. Run via Docker (`docker compose up -d weaviate`) or follow upstream install docs.
- **Pinecone** — managed service. Provide `PINECONE_API_KEY` and `PINECONE_INDEX_NAME` in `.env`.

### 5. Application

```bash
# Clone and enter
git clone <repo-url> dare-backend && cd dare-backend

# Create env file
cp .example.env .env
# Edit .env per docs/configuration.md

# Python environment
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements/prod.txt   # local.txt for development

# Migrate, collect static, create admin user
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py createsuperuser
```

### 6. Process management (production)

Run two long-lived processes — the ASGI server and the RQ worker. Below is a sample systemd unit; adapt paths to your environment.

**`/etc/systemd/system/dare-api.service`**

```ini
[Unit]
Description=DARE Backend ASGI server
After=network.target

[Service]
Type=simple
User=dare
WorkingDirectory=/opt/dare-backend
EnvironmentFile=/opt/dare-backend/.env
ExecStart=/opt/dare-backend/.venv/bin/uvicorn dare.asgi:application \
    --host 0.0.0.0 --port 8000 --workers 4
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

**`/etc/systemd/system/dare-worker.service`**

```ini
[Unit]
Description=DARE Backend RQ worker
After=network.target redis.service

[Service]
Type=simple
User=dare
WorkingDirectory=/opt/dare-backend
EnvironmentFile=/opt/dare-backend/.env
ExecStart=/opt/dare-backend/.venv/bin/python manage.py rqworker default -v 1
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now dare-api dare-worker
```

### 7. Reverse proxy (Nginx)

Sample Nginx config terminating TLS in front of Uvicorn:

```nginx
server {
    listen 443 ssl http2;
    server_name api.example.com;

    ssl_certificate     /etc/letsencrypt/live/api.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/api.example.com/privkey.pem;

    client_max_body_size 100M;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # WebSocket / Socket.IO upgrade
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 3600s;
    }
}
```

---

## Verifying the install

```bash
# REST endpoint
curl http://localhost:8000/api/health/
curl http://localhost:8000/api/ready/

# Swagger UI (in browser)
open http://localhost:8000/api/docs/

# Background worker is consuming jobs
python manage.py rqstats
```

If you see migrations applied, Swagger loads, and the worker reports an active default queue, the install is healthy.

---

## Upgrading

```bash
git pull
source .venv/bin/activate
pip install -r requirements/prod.txt
python manage.py migrate
python manage.py collectstatic --noinput
sudo systemctl restart dare-api dare-worker
```

Run migrations *before* restarting the API server when the upgrade includes schema changes.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `psycopg2.OperationalError: could not connect` | Postgres not running, or wrong `DB_*` values | `systemctl status postgresql`; check `.env` |
| `redis.exceptions.ConnectionError` | Redis not running, or wrong `REDIS_HOST/PORT` | `redis-cli ping`; check `.env` |
| `WeaviateConnectionError` | Weaviate container not running | `docker compose ps weaviate` |
| `ollama: connection refused` | Ollama container not running, or `OLLAMA_HOST` mismatch | `docker compose ps ollama` |
| `OBJC_DISABLE_INITIALIZE_FORK_SAFETY` errors on macOS | RQ worker forking issue | Prefix worker command with that env var |
| Background jobs stuck | Worker not running | Start the `rqworker` process |
| 502 from Nginx | Uvicorn bound to wrong host or crashed | `journalctl -u dare-api -f` |

For more, see [docs/admin-guide.md](docs/admin-guide.md).
