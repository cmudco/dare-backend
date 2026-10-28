# Deploy

Use this to run the DARE backend on a server, with Docker or on bare metal, and to upgrade or
roll it back. To run it on your own machine, use [docs/tutorials/quickstart.md](../tutorials/quickstart.md).
Every environment variable is in [docs/reference/configuration.md](../reference/configuration.md).

## Prerequisites

| Requirement | Version | Notes |
| --- | --- | --- |
| Python | 3.13.x | Required by the current dependency set |
| PostgreSQL | 16+ | Required in production; Docker bundles `pgvector/pgvector:pg16` |
| Redis | 6+ | Background jobs and Socket.IO pub/sub |
| Docker + Compose | 24+ | Recommended for a single-server deployment |
| Node.js | 18+ | Only if the frontend runs on the same host |

### Single-server resource minimums

| Profile | CPU | RAM | Storage | Notes |
| --- | ---: | ---: | ---: | --- |
| Backend + Postgres + Redis + Weaviate | 2 vCPU | 4 GB | 30 GB SSD | Low traffic, small uploads, external LLM APIs |
| Recommended production baseline | 4 vCPU | 8 GB | 80 GB SSD | Headroom for file processing, RQ jobs, Weaviate indexes, logs and backups |
| With local Ollama models | 8 vCPU | 24 GB+ | 150 GB SSD | Depends on model size; the compose profile reserves 8 GB and caps Ollama at 16 GB |

Plan extra disk for uploaded media, Postgres backups, Weaviate data and model files. Watch volume
growth and keep database and media backups off the host.

## Production topology

Any cloud provider works. You need a long-running ASGI process, a background worker, Redis,
Postgres, media storage, and a reverse proxy that supports WebSocket upgrades.

| Component | Role | Notes |
| --- | --- | --- |
| Reverse proxy | TLS termination, HTTP and WebSocket routing | Nginx, ALB, Caddy or equivalent; must pass Socket.IO upgrade headers |
| DARE web | Django ASGI API and Socket.IO server | Uvicorn, or Gunicorn with Uvicorn workers |
| DARE worker | Django RQ worker | File ingestion, embeddings, email and other async jobs |
| Postgres + pgvector | Primary database | Prefer managed Postgres; back it up regularly |
| Redis | RQ queues, cache, Socket.IO pub/sub | Enable AUTH and private networking |
| Vector store | RAG embeddings | Weaviate when self-hosted, Pinecone when managed |
| Object or media storage | Uploads and generated media | Local disk suits small installs; use durable object storage in production |

## Production checklist

- **Secrets.** Start from `.example.env` but never deploy its example values. Set fresh
  `DJANGO_SECRET_KEY`, `DARE_INTERNAL_KEY`, `SSO_JWT_KEY`, database credentials, the Redis
  password (if AUTH is on) and provider API keys.
- **Debug off.** `DJANGO_DEBUG=False` and `ALLOWED_HOSTS` limited to your hostnames.
- **TLS.** Terminate public HTTPS at the reverse proxy; never expose Uvicorn directly. The proxy
  must hold long-lived Socket.IO connections.
- **Private network.** Postgres, Redis and Weaviate are reachable only from trusted hosts.
- **Origins.** List every frontend origin in `CORS_ALLOWED_ORIGINS` and `CSRF_TRUSTED_ORIGINS`.
- **Workers.** Run `default` workers, exactly one `memory` worker and one `rqscheduler` (see
  [Process management](#6-process-management)).
- **Document runtime.** Every worker host has the OpenCV system libraries (see
  [Document parsing runtime](#document-parsing-runtime)).
- **Backups.** Back up Postgres, uploaded media, Weaviate data (if self-hosted) and the environment
  configuration, stored securely outside the repository. Test a restore before you rely on them.

## Path 1: Docker

The bundled `docker-compose.yml` runs:

- **DARE web** (Django ASGI API and Socket.IO) on `${WEB_PORT:-8000}`
- **DARE worker** (one RQ worker on the `default` queue)
- **Postgres + pgvector** on `${POSTGRES_PORT:-5432}`
- **Redis** on `${REDIS_PORT:-6379}`
- **Weaviate** on `${WEAVIATE_PORT:-8080}` and `${WEAVIATE_GRPC_PORT:-50051}`
- **Weaviate console** on `${WEAVIATE_CONSOLE_PORT:-8081}`, with the `debug` profile
- **Ollama** on `${OLLAMA_PORT:-11434}`, with the `llama` profile
- **[quillmark-mcp](https://github.com/tonguetoquill/quillmark-mcp)** (document generation, built
  from the `quillmark-mcp/` submodule) on `127.0.0.1:${QUILLMARK_DEBUG_PORT:-8090}`, for debugging
  only; DARE reaches it over the compose network

`scripts/dev-setup.sh` automates the steps below for local development; see the
[quick start](../tutorials/quickstart.md).

### 1. Clone with submodules

```bash
git clone --recurse-submodules https://github.com/cmudco/dare-backend.git
cd dare-backend
# Cloned without submodules? Run: git submodule update --init
```

### 2. Configure

Copy the env file and set `DB_PASSWORD` (compose refuses to start without it), `DJANGO_SECRET_KEY`
and a provider key. Put local-only overrides in `docker-compose.override.yml`, copied from
`docker-compose.override.yml.example`; Compose loads it automatically.

```bash
cp .example.env .env
```

### 3. Start the stack

The web container waits for Postgres and Redis, collects static files (hiding errors) and runs
migrations before it starts Uvicorn. Run `collectstatic` yourself to see any error.
Static and media files live in named Docker volumes.

```bash
docker compose up --build -d
docker compose exec web python manage.py createsuperuser
docker compose exec web python manage.py collectstatic --noinput   # admin CSS/JS
```

### 4. Enable optional services

```bash
docker compose --profile debug up -d weaviate-console
docker compose --profile llama up -d ollama
docker compose exec ollama ollama pull llama3.1:8b
```

[run-quillmark.md](run-quillmark.md) covers the document renderer.

## Path 2: Bare metal

Use this for production hosts or where Docker is not available.

### 1. Install system dependencies

Ubuntu or Debian:

```bash
sudo apt update
sudo apt install -y python3.13 python3.13-venv python3-pip \
    postgresql postgresql-contrib redis-server \
    build-essential libpq-dev
```

macOS (Homebrew):

```bash
brew install python@3.13 postgresql@16 redis
brew services start postgresql@16
brew services start redis
```

### 2. Create the database

```bash
sudo -u postgres psql <<EOF
CREATE DATABASE dare;
CREATE USER dare WITH PASSWORD 'changeme';
GRANT ALL PRIVILEGES ON DATABASE dare TO dare;
ALTER DATABASE dare OWNER TO dare;
EOF
```

Set the matching values in `.env`:

```
USE_POSTGRES=True
DB_NAME=dare
DB_USER=dare
DB_PASSWORD=changeme
DB_HOST=localhost
DB_PORT=5432
```

### 3. Check Redis

Redis runs on `localhost:6379` after install.

```bash
redis-cli ping   # PONG
```

### 4. Choose a vector store

Use one or both:

- **Weaviate** is self-hostable. Run it with `docker compose up -d weaviate` or follow the
  upstream install docs.
- **Pinecone** is a managed service. Set `PINECONE_API_KEY` and `PINECONE_INDEX_NAME` in `.env`.

### 5. Install the application

```bash
git clone https://github.com/cmudco/dare-backend.git && cd dare-backend
cp .example.env .env   # edit per docs/reference/configuration.md
python3.13 -m venv .venv
source .venv/bin/activate
bash devops/install_document_runtime.sh   # Debian/Ubuntu: OpenCV system libraries
pip install -r requirements/prod.txt      # local.txt for development
python devops/check_document_runtime.py
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py createsuperuser
```

### 6. Process management

Run these long-lived processes:

- **API.** The ASGI server (`dare-api` below).
- **Default workers.** One or more `rqworker default` processes (`dare-worker` below).
- **Memory worker.** Exactly one `python manage.py rqworker memory` process. Memory writes depend
  on a single FIFO worker for per-user ordering; never run two.
- **Scheduler.** One `python manage.py rqscheduler` process, so scheduled jobs fire. Workers
  register the recurring ingestion sweep at startup (`INGESTION_RECONCILE_INTERVAL_SECONDS`).

Sample systemd units; adapt the paths to your host.

`/etc/systemd/system/dare-api.service`:

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

`/etc/systemd/system/dare-worker.service`:

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

Copy `dare-worker.service` for the memory worker and the scheduler, changing `ExecStart`. Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now dare-api dare-worker
```

## Reverse proxy (Nginx)

This config terminates TLS in front of Uvicorn and passes Socket.IO upgrades:

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

## Document parsing runtime

Docling pulls in `opencv-python` through RapidOCR. Its Linux wheel links to `libGL.so.1` even
though parsing never opens a display.

- **Do not install the headless wheel alongside it.** Both own `cv2`.
- **Docker** installs `libgl1` and GLib and checks the runtime during the build.
- **Native hosts** must install these libraries on every worker host
  (`devops/install_document_runtime.sh`) and run `devops/check_document_runtime.py`. Any external
  deploy script must run the same two steps.
- **A passing API health check proves nothing here.** It does not exercise the worker's native
  libraries. After deploying, reprocess an affected file in Advanced mode and confirm its
  structure and image descriptions.
- **The Basic fallback** can keep the text after an Advanced parser error, but it does not repair
  a missing library.

Image descriptions from OpenAI-compatible models get at most one retry when the response is
empty, malformed JSON or token-limited. A token-limited retry doubles the output allowance. Every
attempt is metered, and wallet credit is checked again before the retry. Refusals and
content-filtered responses are not retried. Persistent failures stay available through the
retry-image-descriptions action, and successful descriptions stay cached.

## Verify

```bash
curl http://localhost:8000/api/health/
curl http://localhost:8000/api/ready/
open http://localhost:8000/api/docs/   # Swagger UI
python manage.py rqstats               # workers are consuming jobs
docker compose ps                      # Docker path: container health
```

The install is healthy when migrations are applied, Swagger loads and `rqstats` shows an active
`default` queue.

## Upgrade

`devops/deploy.sh` automates this sequence for the reference host. Review its paths and service
names (`dare`, `rqworker1`–`rqworker4`, `rqscheduler`, `nginx`) before using it anywhere else.

1. Announce the deployment window if users may be affected.
2. Pull the target commit or release tag and activate the virtual environment.
3. On native Debian/Ubuntu API and RQ hosts, run `bash devops/install_document_runtime.sh`, then
   `pip install -r requirements/prod.txt`.
4. With the workers' Python environment, run `python devops/check_document_runtime.py`, then
   `python manage.py check`.
5. Run `python manage.py migrate`, then `python manage.py collectstatic --noinput`. Migrate before
   restarting the API when the upgrade changes the schema.
6. Restart the API and workers, for example `sudo systemctl restart dare-api dare-worker`.
7. Check `/api/health/` and `/api/ready/`.

### Deploy order across repos

Deploy a backend before any frontend that relies on its new API shape.

1. **dare-backend.** Everything else depends on it.
2. **socraticbots-backend.** Depends on the DARE API.
3. **dare-frontend.** Depends on the DARE API.
4. **socraticbots-frontend.** Depends on the Socratic Bots API.

### Rollback

1. Check out the previous known-good commit or release tag.
2. Reinstall dependencies if the requirements changed.
3. Restart the API and workers.
4. Check the health endpoints and key user flows.

Database rollbacks depend on the migration. Prefer a forward-fix migration unless the migration
was designed to reverse safely.

## Troubleshooting

- **`psycopg2.OperationalError: could not connect`.** Postgres is down or the `DB_*` values are
  wrong. Run `systemctl status postgresql` and check `.env`.
- **`redis.exceptions.ConnectionError`.** Redis is down or `REDIS_HOST`/`REDIS_PORT` is wrong. Run
  `redis-cli ping` and check `.env`.
- **`WeaviateConnectionError`.** The Weaviate container is not running. Run
  `docker compose ps weaviate`.
- **`ollama: connection refused`.** The Ollama container is not running or `OLLAMA_HOST` does not
  match. Run `docker compose ps ollama`.
- **The worker crashes on macOS with `OBJC_DISABLE_INITIALIZE_FORK_SAFETY` errors.** The default
  worker forks. Run `rqworker default --worker-class rq.SimpleWorker`.
- **Background jobs are stuck.** No worker is running. Start the `rqworker` process.
- **Nginx returns 502.** Uvicorn crashed or is bound to the wrong host. Run
  `journalctl -u dare-api -f`.

For user and access management, see [administer-dare.md](administer-dare.md).
