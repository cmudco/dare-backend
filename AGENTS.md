# AGENTS.md

Django ASGI backend for DARE: REST and Socket.IO on port 8000, backed by Postgres (SQLite
locally), Redis and RQ workers. The frontend is the separate `dare-frontend` repo.

Read and follow `docs/reference/rules.md` before changing code. Procedures live in `.agents/skills/`:
`run-backend`, `run-workers`, `check`, `release-note`.

## Commands

```bash
uvicorn dare.asgi:application --host 0.0.0.0 --port 8000 --reload   # not runserver
python manage.py rqworker default --worker-class rq.SimpleWorker -v 2   # SimpleWorker on macOS
python manage.py test                                                  # Django runner, no pytest
black . && isort .                                                     # check: --check / -c
```

## Traps

- **Never `manage.py runserver`.** It is WSGI-only, so Socket.IO chat silently fails to connect.
  The Socket.IO app is mounted in `dare/asgi.py`.
- **Most models have no `objects` manager.** `Message`, `Conversation`, `File` and other
  `BaseModel` subclasses declare only `active_objects`, which hides soft-deleted and inactive
  rows. Reverse relations (`conversation.messages`) filter the same way. Use `_base_manager`
  when you need every row.
- **CI does not run tests or linters.** `.github/workflows/` only deploys. Run the `check` skill
  yourself.
- **Tests use Django's runner.** They live in `<app>/tests/` or as `test_*.py` beside the module,
  and subclass `TestCase` or `SimpleTestCase`. pytest is not installed.
- **RQ workers don't reload code.** Restart them after editing anything a job imports.
- **Run exactly one `memory` worker.** Memory writes rely on one global FIFO worker for per-user
  ordering; a second worker silently breaks it. See the `run-workers` skill.
- **pdfium is not thread-safe.** Every pdfium call must hold `_PDFIUM_LOCK` in
  `core/services/document_crop_service.py`; concurrent rendering kills the ASGI worker.
- **Gemini 3.x rejects replayed tool calls without a `thought_signature`.**
  `core/services/llm_utils/provider_message_converters.py` adds it; route any new Gemini
  history path through it.
- **The env example is `.example.env`**, not `.env.example`. Variables are declared in
  `config/env.py` and documented in `docs/reference/configuration.md`.
- **The frontend's docs site copies files from this repo's `docs/`.** Renaming or moving one
  breaks `dare-frontend/docs-site/scripts/sync-content.mjs`; update it in a paired PR.
