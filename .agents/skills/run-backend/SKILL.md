---
name: run-backend
description: Start the DARE backend locally — Django ASGI on port 8000, plus the Redis and .env prerequisites it needs. Use when the user wants to run, start, boot, or serve the DARE backend, or asks why the API on :8000 is not responding.
---

# Run the DARE backend

The backend is a Django ASGI service (Django + Channels + python-socketio). It serves REST on
`http://localhost:8000/` and Socket.IO on the same port.

Two paths — Docker starts everything, local Python runs just Django on your machine. Pick Docker
unless the user is actively editing backend code and wants a fast reload loop.

## Prerequisites

- **Python 3.13.x** — the dependency set requires it; 3.12 and 3.14 will fail to install.
- **Redis** on `:6379` — required for Socket.IO pub/sub and the RQ job queue. The backend starts
  without it but chat streaming will not work.
- **`.env`** — copy from the checked-in example. Note the filename is `.example.env`, not
  `.env.example`.

```bash
cp .example.env .env
```

At minimum set `DJANGO_SECRET_KEY` and one provider key (`OPENAI_API_KEY`, `CLAUDE_API_KEY`, or
`GEMINI_API_KEY`). Every variable is documented in `docs/configuration.md`.

## Path 1 — Docker (brings up the whole stack)

```bash
docker compose up --build -d
docker compose exec web python manage.py createsuperuser
```

This starts the API, an RQ worker, Postgres + pgvector, Redis, and Weaviate. The web container
waits for Postgres and Redis, then runs migrations before starting Uvicorn — you do not need to
migrate by hand.

## Path 2 — local Python

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements/local.txt
python manage.py migrate
uvicorn dare.asgi:application --host 0.0.0.0 --port 8000 --reload
```

Use `uvicorn dare.asgi:application`, **not** `python manage.py runserver`. The backend serves
WebSocket consumers through Channels and Socket.IO; `runserver` is WSGI-only and chat streaming
will silently fail to connect.

Redis must be running separately on this path (`redis-server`), and background jobs need a worker —
see the `run-workers` skill.

## Verify it came up

```bash
curl http://localhost:8000/api/health/
curl http://localhost:8000/api/ready/
```

`/api/health/` answers as soon as Django is serving. `/api/ready/` additionally checks the
dependencies, so use it to tell "Django is up" apart from "Django is up and Postgres/Redis are
reachable".

The OpenAPI schema is at `http://localhost:8000/api/schema/`. Swagger UI at `/api/docs/` loads its
assets from a CDN, so on a restricted or offline network read the raw schema instead.

## Running it in the background

When starting the server from an agent session, detach it so it outlives the session:

```bash
nohup uvicorn dare.asgi:application --host 0.0.0.0 --port 8000 --reload \
  > /tmp/dare-backend.log 2>&1 & disown
```

Then confirm and read the log:

```bash
lsof -ti tcp:8000        # should print a PID
tail -n 40 /tmp/dare-backend.log
```

## Stop it

```bash
lsof -ti tcp:8000 | xargs kill     # local Python
docker compose down                # Docker
```

If port 8000 is already taken, a previous run is still alive — check with `lsof -ti tcp:8000`
before starting a second one.

## Related

- `run-workers` — background jobs; needed before uploaded files will process.
- The frontend lives in a separate repo, [dare-frontend](https://github.com/cmudco/dare-frontend),
  and defaults to `http://localhost:5173`.
- `README.md` for the project overview, `INSTALL.md` for full deployment guidance.
