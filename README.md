# DARE Backend

Django REST + Socket.IO backend for the **DARE (Distributed AI Research Engine)** platform — a multi-LLM research and conversation platform with file processing, vector RAG, workflow automation, and real-time streaming.

## Purpose

DARE provides a unified backend for working with multiple large language models (OpenAI, Anthropic Claude, Google Gemini, and self-hosted LLaMA via Ollama). It handles:

- Real-time streaming chat across providers
- Document upload, processing, and RAG over vector stores (Pinecone, Weaviate)
- Multi-step AI workflow execution via a visual DAG builder
- Token usage tracking and per-user billing
- Access-code based registration for institutional deployments
- Inter-service authentication for partner platforms

## Architecture Overview

```
┌──────────────────────────────────────────────────────────┐
│  Clients (DARE Frontend, Partner Frontends, Mobile)      │
└─────────────────┬───────────────────────┬────────────────┘
                  │ REST / Socket.IO      │
┌─────────────────▼───────────────────────▼────────────────┐
│                    DARE Backend (Django)                  │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐ │
│  │ Auth /   │  │ Convers. │  │ Files /  │  │ Workflow │ │
│  │ Users    │  │ + Chat   │  │ RAG      │  │ Engine   │ │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘ │
│  ┌──────────────────────────────────────────────────────┐│
│  │       Service Layer (LLM, Vector, MCP, Email)        ││
│  └──────────────────────────────────────────────────────┘│
└──┬──────────┬──────────┬──────────┬──────────┬──────────┘
   │          │          │          │          │
┌──▼───┐  ┌───▼───┐  ┌───▼───┐  ┌──▼───┐  ┌───▼────┐
│ Post │  │ Redis │  │ Vector│  │ LLM  │  │ Ollama │
│ gres │  │ + RQ  │  │ Stores│  │ APIs │  │ (local)│
└──────┘  └───────┘  └───────┘  └──────┘  └────────┘
```

See [docs/architecture.md](docs/architecture.md) for the full diagram and [docs/architecture/overview.md](docs/architecture/overview.md) for component-level detail.

## Quick Start (Docker)

```bash
# 1. Clone
git clone <repo-url> dare-backend && cd dare-backend

# 2. Configure
cp .example.env .env
# Edit .env. At minimum, set DJANGO_SECRET_KEY and one provider key
# such as OPENAI_API_KEY, CLAUDE_API_KEY, or GEMINI_API_KEY.

# 3. Build and start the backend stack
docker compose up --build -d

# 4. Create an admin user
docker compose exec web python manage.py createsuperuser

# 5. Check health
docker compose ps
curl http://localhost:8000/api/health/
curl http://localhost:8000/api/ready/
```

The API will be available at `http://localhost:8000/`. The OpenAPI schema is served at `http://localhost:8000/api/schema/`. Swagger UI is routed at `http://localhost:8000/api/docs/`, but it loads Swagger assets from a CDN, so use the raw schema if the UI does not render in an offline or restricted network.

Docker Compose starts the API server, RQ worker, Postgres + pgvector, Redis, and Weaviate. Optional Ollama and Weaviate console services are available through Compose profiles. See [INSTALL.md](INSTALL.md) for details.

## Quick Start (Local Python)

Use this path when you want the Django process running directly on your machine.

```bash
cp .example.env .env
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements/local.txt
python manage.py migrate
uvicorn dare.asgi:application --host 0.0.0.0 --port 8000 --reload
```

In a second terminal, start a worker:

```bash
source .venv/bin/activate
OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES python -Wd manage.py rqworker default -v 3
```

Redis must be running for Socket.IO pub/sub and background jobs. See [INSTALL.md](INSTALL.md) for complete Docker, local, and production guidance.

## Documentation

| Doc | What's in it |
|---|---|
| [INSTALL.md](INSTALL.md) | Full deployment guide — Docker and bare metal |
| [docs/configuration.md](docs/configuration.md) | Every environment variable, with type, default, and description |
| [docs/architecture.md](docs/architecture.md) | Component diagram and request flows |
| [docs/admin-guide.md](docs/admin-guide.md) | User/role management, access codes, analytics |
| [docs/contributing.md](docs/contributing.md) | Issues, pull requests, coding standards |
| [CHANGELOG.md](CHANGELOG.md) | Release notes |
| [SECURITY.md](SECURITY.md) | Vulnerability disclosure process |
| [docs/integration/socraticbooks-dare-proxy.md](docs/integration/socraticbooks-dare-proxy.md) | DARE/SocraticBooks integration contract and update rules |
| [docs/architecture/socketio-events.md](docs/architecture/socketio-events.md) | Socket.IO event reference |
| [docs/api/dare-backend.md](docs/api/dare-backend.md) | REST API reference |
| [docs/code-standards.md](docs/code-standards.md) | Coding conventions |

## Tech Stack

- **Python 3.13**, Django 5.1, Django REST Framework
- **Django Channels** + python-socketio for real-time streaming
- **Django RQ** + Redis for background jobs
- **PostgreSQL** (production) / SQLite (local dev)
- **Weaviate** and **Pinecone** for vector storage
- **Ollama** for self-hosted LLaMA models

## License

No open-source license has been selected in this repository yet. Add a `LICENSE` file before public release.
