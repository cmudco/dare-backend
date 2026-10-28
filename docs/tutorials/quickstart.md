# Quick start

By the end you will have the DARE backend running locally on port 8000, either as a Docker stack
or as Django in a Python venv; pick one option. To deploy to a server, see
[docs/how-to/deploy.md](../how-to/deploy.md).

## Prerequisites

- **Option A:** Docker with Compose.
- **Option B:** Python 3.13 and a running Redis.
- One LLM provider key: `OPENAI_API_KEY`, `CLAUDE_API_KEY` or `GEMINI_API_KEY`.

## Option A — Docker

This brings up the API, worker, Postgres, Redis, Weaviate and
[quillmark-mcp](https://github.com/tonguetoquill/quillmark-mcp) together.

### 1. Clone with submodules

The compose stack builds quillmark-mcp from the `quillmark-mcp/` submodule.

```bash
git clone --recurse-submodules https://github.com/cmudco/dare-backend.git
cd dare-backend
# Cloned without submodules? Run: git submodule update --init
```

### 2. Run the setup script

`scripts/dev-setup.sh` checks Docker, initialises the submodule, creates `.env` (with a local
`DB_PASSWORD`) and a gitignored `docker-compose.override.yml` (with a generated
`DJANGO_SECRET_KEY`), then builds and starts the stack. Migrations run automatically.
`--demo-user` also creates a verified demo superuser with a $100 wallet and prints its login.

```bash
scripts/dev-setup.sh --demo-user
```

### 3. Add your provider key

Set `CLAUDE_API_KEY` (or another provider key) in `docker-compose.override.yml`, then reload.

```bash
docker compose up -d
```

### Or do it by hand

These are the steps the script runs. Compose refuses to start until `DB_PASSWORD` is set.

```bash
git submodule update --init
cp .example.env .env   # set DB_PASSWORD, DJANGO_SECRET_KEY and one provider key
docker compose up --build -d
docker compose exec web python manage.py createsuperuser
docker compose exec web python manage.py collectstatic --noinput   # admin CSS/JS
```

### Start the frontend and generate a document

```bash
cd ../dare-frontend
npm install && npm run dev   # http://localhost:5173
```

Log in, open `/mcp`, click **Connect** on *CMU Documents*, then ask a chat for a document such
as a memo. The assistant renders a PDF through quillmark-mcp; see
[run-quillmark.md](../how-to/run-quillmark.md).

## Option B — Local Python

This runs Django directly in a venv. Redis must be running for background jobs and Socket.IO.
With `.example.env` as is, Django uses `config.settings.local` and a SQLite database.

### 1. Clone and configure

```bash
git clone https://github.com/cmudco/dare-backend.git
cd dare-backend
cp .example.env .env   # set DJANGO_SECRET_KEY and one provider key
```

### 2. Install and migrate

```bash
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements/local.txt
python manage.py migrate
python manage.py createsuperuser
```

### 3. Collect static files

Uvicorn does not serve static files the way `runserver` does, so the admin looks unstyled until
you run this.

```bash
python manage.py collectstatic --noinput
```

### 4. Start the API

```bash
uvicorn dare.asgi:application --host 0.0.0.0 --port 8000 --reload
```

### 5. Start a worker

In a second terminal:

```bash
source .venv/bin/activate
python manage.py rqworker default -v 2   # macOS: add --worker-class rq.SimpleWorker
```

## Let people create accounts

Registration is gated by an access code. Log in to the admin at `http://localhost:8000/admin/`
with your superuser and create an access code; anyone with that code can then register. A new
user needs a verified email and wallet credit before chat runs.

## Verify

```bash
curl http://localhost:8000/api/health/
curl http://localhost:8000/api/ready/
```

Both return success, Swagger UI loads at `http://localhost:8000/api/docs/`, and
`python manage.py rqstats` shows a worker on the `default` queue. Every variable is in
[configuration.md](../reference/configuration.md).

## Troubleshooting

- **`docker compose` fails with `set_DB_PASSWORD`.** Set `DB_PASSWORD` in `.env`.
- **The quillmark-mcp build fails with a missing context.** Run `git submodule update --init`.
- **The admin has no styling.** Run `collectstatic` (step 3 of Option B, or the `docker compose
  exec` line in Option A).
- **Socket.IO chat never connects.** You started `manage.py runserver`; use the `uvicorn` command.
- **The worker crashes on macOS with `OBJC_DISABLE_INITIALIZE_FORK_SAFETY` errors.** Add
  `--worker-class rq.SimpleWorker`.
- **Uploads stay in Processing.** No worker is running, or it is running old code; start or
  restart it.
