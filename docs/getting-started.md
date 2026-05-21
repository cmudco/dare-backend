# Getting Started

This guide walks you through setting up the full local workspace from scratch: two backends and two frontends. If you only need the DARE API, use the shorter Docker or local Python path in the repository [README](../README.md).

## Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Python | 3.13+ | Required by dare-backend (memory module uses 3.13 features) |
| Node.js | 18+ | For both frontends |
| Redis | Latest | Required for Socket.IO pub/sub and background task queues |
| PostgreSQL | 14+ | Optional — SQLite works for local dev |

## Architecture Overview

```
┌─────────────────┐     REST + Socket.IO     ┌──────────────────┐
│  dare-frontend   │ ◄──────────────────────► │  dare-backend     │
│  (port 5173)     │                          │  (port 8000)      │
└─────────────────┘                          └──────────────────┘
                                                      ▲
                                              X-Internal-Key + JWT
                                                      │
┌─────────────────┐         REST              ┌──────────────────┐
│ socraticbooks-   │ ◄──────────────────────► │ socraticbooks-    │
│ react            │                          │ backend (8001)    │
└─────────────────┘                          └──────────────────┘
```

**Startup order matters:** Redis → dare-backend → dare-frontend → socraticbooks-backend → socraticbooks-react

## Step 1: Start Redis

```bash
redis-server
```

Leave this running in its own terminal. Redis is used for:
- Socket.IO pub/sub messaging between server instances
- Background task queue (Django RQ workers)

## Step 2: Set Up dare-backend

```bash
cd dare-backend

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements/local.txt

# Set up environment variables
cp .example.env .env
# Edit .env — minimum required:
#   DJANGO_SETTINGS_MODULE=config.settings.local
#   DJANGO_SECRET_KEY=<generate-a-secret-key>
#   DJANGO_DEBUG=True
#   REDIS_HOST=localhost
#   REDIS_PORT=6379

# Run database migrations
python manage.py migrate

# Create a superuser (for admin access)
python manage.py createsuperuser

# Start the ASGI server (required for Socket.IO)
uvicorn dare.asgi:application --port 8000 --reload --log-level debug
```

**Important:** Use `uvicorn` (ASGI), not `runserver`. Socket.IO requires an ASGI server.

### Start Background Workers (separate terminal)

Background workers process file uploads, generate embeddings, and handle async tasks:

```bash
cd dare-backend
source .venv/bin/activate

# Run a single worker manually
OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES python -Wd manage.py rqworker default -v 3
```

The `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES` flag is required on macOS to prevent fork-safety crashes.

### Verify dare-backend

- Admin panel: http://localhost:8000/admin/
- OpenAPI schema: http://localhost:8000/api/schema/
- Swagger UI: http://localhost:8000/api/docs/ (requires CDN assets)
- API docs (ReDoc): http://localhost:8000/api/redoc/
- RQ dashboard: http://localhost:8000/django-rq/

## Step 3: Set Up dare-frontend

```bash
cd dare-frontend

# Install dependencies
npm install

# Set up environment variables
cp .env.example .env
# Edit .env:
#   VITE_DJANGO_BACKEND_URL=http://localhost:8000
#   VITE_WEBSOCKET_URL=http://localhost:8000

# Start dev server
npm run dev
```

### Verify dare-frontend

- Open http://localhost:5173
- Register a new account or log in
- Create a conversation and send a message (requires LLM API keys in backend .env)

## Step 4: Set Up socraticbooks-backend

The SocraticBooks backend proxies auth and AI calls to dare-backend, so dare-backend must be running first.

```bash
cd socraticbooks-backend

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Set up environment variables
cp env.example .env
# Edit .env — minimum required:
#   DARE_BACKEND_URL=http://localhost:8000
#   SOCRATIC_BOTS_BACKEND_URL=http://localhost:8001
#   DARE_INTERNAL_KEY=<must-match-dare-backend>
#   DJANGO_SECRET_KEY=<must-match-dare-backend-for-local-jwt-decode>

# Run database migrations
python backend/manage.py migrate

# Start the server
python backend/manage.py runserver 0.0.0.0:8001
```

## Step 5: Set Up socraticbooks-react

```bash
cd socraticbooks-react

# Install dependencies
npm install

# Set up environment variables
cp .env.example .env
# Edit .env:
#   VITE_API_URL=http://localhost:8001/api

# Start dev server
npm run dev
```

## Running Tests

```bash
# dare-backend
cd dare-backend && source .venv/bin/activate
python manage.py test                       # All tests
python manage.py test conversations.tests   # Single app

# dare-frontend
cd dare-frontend
npm run lint      # ESLint
npm run build     # Type-check + build

# socraticbooks-backend
cd socraticbooks-backend/backend && source .venv/bin/activate
python manage.py test
```

## Code Formatting

```bash
# dare-backend (run before committing)
cd dare-backend && source .venv/bin/activate
black . && isort .

# dare-frontend
cd dare-frontend
npm run lint && npm run format
```

## LLM API Keys

To use AI features, add provider API keys to dare-backend's `.env`:

```bash
OPENAI_API_KEY=sk-...          # GPT-4, GPT-4o, etc.
ANTHROPIC_API_KEY=sk-ant-...   # Claude models
GOOGLE_API_KEY=...             # Gemini models
# LLaMA/Ollama: runs locally, no key needed
```

Models and access are configured in the admin panel under **Model Groups**.

## Common Issues

| Issue | Solution |
|---|---|
| Socket.IO not connecting | Make sure you're using `uvicorn`, not `runserver` |
| File processing stuck | Check that Redis is running and at least one RQ worker is active |
| macOS worker crashes | Use `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES` before the worker command |
| SocraticBooks auth fails | Ensure `DARE_INTERNAL_KEY` matches in both backend `.env` files |
| Frontend can't reach backend | Check CORS settings and `VITE_DJANGO_BACKEND_URL` |

## Next Steps

- [Architecture Overview](architecture/overview.md) — System diagrams and component descriptions
- [Socket.IO Event Contract](architecture/socketio-events.md) — Real-time event reference
- [OpenAPI Schema](http://localhost:8000/api/schema/) — Raw API schema (when backend is running)
- [Swagger UI](http://localhost:8000/api/docs/) — Interactive API explorer if CDN assets are reachable
- [Inter-Service Proxy](integration/socraticbooks-dare-proxy.md) — How SocraticBooks communicates with DARE
