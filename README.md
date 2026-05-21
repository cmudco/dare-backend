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
# Edit .env — at minimum, set OPENAI_API_KEY (or another provider)

# 3. Start dependencies (Weaviate + Ollama)
docker-compose up -d

# 4. Install Python deps and run migrations
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements/local.txt
python manage.py migrate

# 5. Run the API server
uvicorn dare.asgi:application --host 0.0.0.0 --port 8000 --reload

# 6. In a second terminal, run the background worker
OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES python -Wd manage.py rqworker default -v 3
```

The API will be available at `http://localhost:8000/`. Interactive Swagger docs are at `http://localhost:8000/api/docs/`.

> **Note:** Docker Compose currently provisions Weaviate and Ollama only. Postgres and Redis run on the host (see [INSTALL.md](INSTALL.md) for full containerization).

## Quick Start (Bare Metal)

See [INSTALL.md](INSTALL.md) for the full bare-metal deployment guide, including Postgres and Redis setup.

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
| [docs/architecture/socketio-events.md](docs/architecture/socketio-events.md) | Socket.IO event reference |
| [docs/api/dare-backend.md](docs/api/dare-backend.md) | REST API reference |
| [docs/code-standards.md](docs/code-standards.md) | Coding conventions |

## Tech Stack

- **Python 3.11**, Django 4.x, Django REST Framework
- **Django Channels** + python-socketio for real-time streaming
- **Django RQ** + Redis for background jobs
- **PostgreSQL** (production) / SQLite (local dev)
- **Weaviate** and **Pinecone** for vector storage
- **Ollama** for self-hosted LLaMA models

## License

See [LICENSE](LICENSE) if present, or contact the maintainers.
