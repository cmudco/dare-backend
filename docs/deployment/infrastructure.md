# Infrastructure

This page describes the recommended production shape for a self-hosted DARE backend. The exact cloud provider is flexible; the important constraints are the long-running ASGI process, the background worker, Redis, Postgres, media storage, and a reverse proxy that supports WebSocket upgrades.

## Recommended Topology

| Component | Role | Notes |
|---|---|---|
| Reverse proxy | TLS termination and HTTP/WebSocket routing | Nginx, ALB, Caddy, or equivalent. Must pass Socket.IO upgrade headers. |
| DARE web | Django ASGI API and Socket.IO server | Run with Uvicorn or Gunicorn + Uvicorn workers. |
| DARE worker | Django RQ worker | Processes file ingestion, embeddings, email, and other async jobs. |
| Postgres + pgvector | Primary relational database | Use managed Postgres where possible. Back up regularly. |
| Redis | RQ queues, cache, Socket.IO pub/sub | Enable authentication and private networking in production. |
| Vector store | RAG embeddings | Weaviate for self-hosted deployments, Pinecone for managed deployments. |
| Object/media storage | Uploaded files and generated media | Local disk is acceptable for small installs; use durable object storage for production. |

## Network Requirements

- Public traffic should terminate at the reverse proxy over HTTPS.
- The backend should not expose Uvicorn directly to the internet.
- The reverse proxy must support long-lived Socket.IO connections.
- Postgres, Redis, and Weaviate should be reachable only from trusted hosts or private networks.
- Frontend origins must be listed in `CORS_ALLOWED_ORIGINS` and `CSRF_TRUSTED_ORIGINS`.

## Required Processes

Run at least these two application processes:

```bash
uvicorn dare.asgi:application --host 0.0.0.0 --port 8000
python manage.py rqworker default -v 1
```

For systemd examples, see [INSTALL.md](../../INSTALL.md#6-process-management-production).

## Environment Management

Use `.example.env` as the template, but never deploy with example secrets. At minimum, rotate:

- `DJANGO_SECRET_KEY`
- `DARE_INTERNAL_KEY`
- Database credentials
- Redis password, if Redis AUTH is enabled
- LLM provider API keys

For the full variable reference, see [configuration.md](../configuration.md).

## Backups

Production deployments should back up:

- Postgres database
- Uploaded media files
- Weaviate data, if self-hosted
- Environment configuration, stored securely outside the repository

Test restore procedures before relying on backups.

## Existing Deployment Scripts

- `devops/deploy.sh` — Pulls latest code, installs dependencies, runs migrations, collects static files, and restarts services. Review and adapt paths/service names before using it in a new environment.
