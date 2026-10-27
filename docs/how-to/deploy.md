# DARE Backend — Deployment Guide

> **Just want to run it locally?** See [docs/tutorials/quickstart.md](../tutorials/quickstart.md) — the **Docker** path stands up
> the whole stack (API, worker, Postgres, Redis, Weaviate) in one command, or the **Local Python**
> path runs Django directly in a venv. **This guide is for deploying to a server** and is the fuller
> reference behind that quick start.

This guide covers two deployment paths:

1. **Docker path** — the simplest way to stand up the full stack; recommended for most deployments
2. **Bare-metal path** — for production hosts or environments where Docker is not available

For environment variable reference, see [docs/reference/configuration.md](../reference/configuration.md).

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

## Production Topology

The cloud provider is flexible. What matters is a long-running ASGI process, a background
worker, Redis, Postgres, media storage, and a reverse proxy that supports WebSocket upgrades.

| Component | Role | Notes |
|---|---|---|
| Reverse proxy | TLS termination and HTTP/WebSocket routing | Nginx, ALB, Caddy, or equivalent. Must pass Socket.IO upgrade headers. |
| DARE web | Django ASGI API and Socket.IO server | Run with Uvicorn or Gunicorn + Uvicorn workers. |
| DARE worker | Django RQ worker | Processes file ingestion, embeddings, email, and other async jobs. |
| Postgres + pgvector | Primary relational database | Use managed Postgres where possible. Back up regularly. |
| Redis | RQ queues, cache, Socket.IO pub/sub | Enable authentication and private networking in production. |
| Vector store | RAG embeddings | Weaviate for self-hosted deployments, Pinecone for managed deployments. |
| Object/media storage | Uploaded files and generated media | Local disk is acceptable for small installs; use durable object storage for production. |

Network:

- Terminate public traffic at the reverse proxy over HTTPS; never expose Uvicorn directly.
- The proxy must support long-lived Socket.IO connections.
- Postgres, Redis, and Weaviate should be reachable only from trusted hosts or private networks.
- List frontend origins in `CORS_ALLOWED_ORIGINS` and `CSRF_TRUSTED_ORIGINS`.

Secrets: use `.example.env` as the template, but never deploy its example values. At minimum,
set fresh `DJANGO_SECRET_KEY`, `DARE_INTERNAL_KEY`, `SSO_JWT_KEY`, database credentials, the Redis
password (if AUTH is on), and provider API keys.

Backups: Postgres, uploaded media, Weaviate data (if self-hosted), and the environment
configuration, stored securely outside the repository. Test a restore before relying on them.

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
- **[quillmark-mcp](https://github.com/tonguetoquill/quillmark-mcp)** (document generation, built from the `quillmark-mcp/` git submodule) on `127.0.0.1:${QUILLMARK_DEBUG_PORT:-8090}` (debug only — DARE talks to it over the compose network)

### Quick start (recommended)

```bash
# 1. Clone with submodules
git clone --recurse-submodules https://github.com/cmudco/dare-backend.git
cd dare-backend
# Already cloned without submodules? Run: git submodule update --init

# 2. One-command setup: checks Docker, inits the submodule, creates .env +
#    docker-compose.override.yml, builds and starts the stack (migrations
#    run automatically), and optionally creates a verified demo superuser
#    with a $100 wallet.
scripts/dev-setup.sh --demo-user

# 3. Set your Anthropic key in docker-compose.override.yml (CLAUDE_API_KEY),
#    then reload:
docker compose up -d
```

Then start the frontend:

```bash
cd ../dare-frontend
npm install && npm run dev    # http://localhost:5173
```

**Document-generation quickstart:** log in on the frontend, open `/mcp`, click **Connect** on *CMU Documents*, then start a chat and ask for a document (e.g. a memo) — the assistant renders a PDF via quillmark-mcp.

### Manual steps

If you prefer to do what `scripts/dev-setup.sh` does by hand:

```bash
git submodule update --init                      # quillmark-mcp build context
cp .example.env .env                             # set DB_PASSWORD at minimum
cp docker-compose.override.yml.example docker-compose.override.yml
# Put DJANGO_SECRET_KEY and CLAUDE_API_KEY (or OPENAI/GEMINI keys) in the override
docker compose up --build -d
docker compose exec web python manage.py createsuperuser

# 5. Collect static files so the Django admin renders with its CSS/JS
docker compose exec web python manage.py collectstatic --noinput

# 6. Check container health
docker compose ps
curl http://localhost:8000/api/health/
curl http://localhost:8000/api/ready/
```

The web container waits for Postgres and Redis, then runs migrations before starting Uvicorn. Static and media files are stored in named Docker volumes. New users need a verified email and wallet credit before chat runs; `scripts/dev-setup.sh --demo-user` creates a ready demo account, and [docs/how-to/run-quillmark.md](run-quillmark.md) covers the document renderer.

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
git clone https://github.com/cmudco/dare-backend.git && cd dare-backend

# Create env file
cp .example.env .env
# Edit .env per docs/reference/configuration.md

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

`devops/deploy.sh` automates this sequence; review its paths and service names before using it in
a new environment.

1. Announce the deployment window if users may be affected.
2. Pull the target commit or release tag and activate the virtual environment.
3. On native Debian/Ubuntu API and RQ hosts, run `bash devops/install_document_runtime.sh`, then
   `pip install -r requirements/prod.txt`.
4. With the workers' Python environment, run `python devops/check_document_runtime.py`, then
   `python manage.py check`.
5. `python manage.py migrate`, then `python manage.py collectstatic --noinput`.
6. Restart the API and worker services, e.g. `sudo systemctl restart dare-api dare-worker`.
7. Verify `/api/health/` and `/api/ready/`.

Run migrations *before* restarting the API server when the upgrade includes schema changes.

### Deployment order across repos

1. **dare-backend**: everything else depends on it.
2. **socraticbots-backend**: depends on the DARE API.
3. **dare-frontend**: depends on the DARE API.
4. **socraticbots-frontend**: depends on the Socratic Bots API.

Deploy a backend before any frontend that relies on its new API shape.

### Rollback

1. Revert to the previous known-good commit or release tag.
2. Reinstall dependencies if the lock set changed.
3. Restart the API and worker services.
4. Verify the health endpoints and key user flows.

Database rollbacks are migration-specific. Prefer forward-fix migrations unless the migration was
explicitly designed to reverse safely.

### Document parsing runtime

`opencv-python` is installed transitively by Docling → RapidOCR. Its Linux wheel links to
`libGL.so.1` even though parsing does not open a display. Do not install the headless wheel
alongside it: both own `cv2`. Docker installs `libgl1` and GLib and checks the runtime during the
build. Native deployments must install these libraries on every worker host, and any external
deploy script must run the same install and check steps. A passing API health check does not
exercise the document worker's native libraries.

After deploying, reprocess an affected file in Advanced mode and confirm its structure and image
descriptions. The Basic fallback can preserve text after an Advanced parser error, but does not
repair a missing runtime library.

OpenAI-compatible image-description responses that are empty, malformed JSON, or token-limited get
at most one more attempt. A token-limited response doubles the output allowance for that retry;
every attempt is metered and wallet credit is checked again before retrying. Refusals and
content-filtered responses are not retried. Persistent failures stay available through the
retry-image-descriptions action, and successful descriptions stay cached.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `psycopg2.OperationalError: could not connect` | Postgres not running, or wrong `DB_*` values | `systemctl status postgresql`; check `.env` |
| `redis.exceptions.ConnectionError` | Redis not running, or wrong `REDIS_HOST/PORT` | `redis-cli ping`; check `.env` |
| `WeaviateConnectionError` | Weaviate container not running | `docker compose ps weaviate` |
| `ollama: connection refused` | Ollama container not running, or `OLLAMA_HOST` mismatch | `docker compose ps ollama` |
| RQ worker crashes on macOS (`OBJC_DISABLE_INITIALIZE_FORK_SAFETY` errors) | The default forking worker | Run `rqworker default --worker-class rq.SimpleWorker` |
| Background jobs stuck | Worker not running | Start the `rqworker` process |
| 502 from Nginx | Uvicorn bound to wrong host or crashed | `journalctl -u dare-api -f` |

For more, see [docs/how-to/administer-dare.md](administer-dare.md).
