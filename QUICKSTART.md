# DARE Backend — Quick Start

Get the backend running locally. Pick **one** of the two paths below.
For deploying to a server, see [DEPLOYMENT.md](DEPLOYMENT.md).

## Prerequisites

- **Docker + Compose** (for Option A), or **Python 3.13 + Redis** (for Option B)
- One LLM provider key: `OPENAI_API_KEY`, `CLAUDE_API_KEY`, or `GEMINI_API_KEY`

```bash
git clone https://github.com/cmudco/dare-backend.git
cd dare-backend
```

---

## Option A — Docker (whole stack in one command)

Brings up the API, worker, Postgres, Redis, and Weaviate together. Easiest way to start.

```bash
cp .example.env .env
# Edit .env — at minimum set DJANGO_SECRET_KEY and one provider key.

docker compose up --build -d
docker compose exec web python manage.py createsuperuser

# Collect static files so the Django admin renders with its CSS/JS.
docker compose exec web python manage.py collectstatic --noinput

curl http://localhost:8000/api/health/
```

---

## Option B — Local Python (Django in a venv)

Runs the Django process directly. **Redis must be running** for background jobs and Socket.IO.

```bash
cp .example.env .env
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements/local.txt
python manage.py migrate
python manage.py createsuperuser

# Collect static files. Required for the admin panel's CSS/JS: DARE runs on
# uvicorn/ASGI, which — unlike `runserver` — does not serve static files
# automatically, so the admin looks unstyled until you run this.
python manage.py collectstatic --noinput

uvicorn dare.asgi:application --host 0.0.0.0 --port 8000 --reload
```

In a second terminal, start a background worker:

```bash
source .venv/bin/activate
OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES python -Wd manage.py rqworker default -v 3
```

---

## Let people create accounts

Registration is gated by an **access code**. Log in to the admin panel at
**http://localhost:8000/admin/** with the superuser you just created, and create an
access code — then anyone can use that code to register their own account.

The API is at **http://localhost:8000/**; Swagger UI at `/api/docs/`.
Full variable reference: [docs/configuration.md](docs/configuration.md).
