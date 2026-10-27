# AGENTS.md

Django ASGI backend for DARE: REST and Socket.IO on port 8000. The frontend is the separate
`dare-frontend` repo.

Read and follow `docs/reference/rules.md` before changing code. Procedures live in `.agents/skills/`:
`run-backend`, `run-workers`, `check`, `release-note`.

## Commands

```bash
uvicorn dare.asgi:application --host 0.0.0.0 --port 8000 --reload   # not runserver
python manage.py rqworker default --worker-class rq.SimpleWorker -v 2
python manage.py test
black . && isort .
```

## Traps

- **Never `manage.py runserver`.** It is WSGI-only; Socket.IO chat silently fails to connect.
- **Most models have no `objects` manager.** `Message`, `Conversation`, `File` and other
  `BaseModel` subclasses declare only `active_objects`, which hides soft-deleted and inactive
  rows. Reverse relations (`conversation.messages`) filter the same way. Use `_base_manager`
  when you really need every row.
- **CI does not run tests or linters.** The workflows only deploy. Run the `check` skill yourself.
- **RQ workers don't reload code.** Restart them after editing anything a job imports.
- **pdfium is not thread-safe.** Every pdfium call must hold `_PDFIUM_LOCK` in
  `core/services/document_crop_service.py`; concurrent rendering kills the ASGI worker.
- **Gemini 3.x rejects replayed tool calls without a `thought_signature`.**
  `core/services/llm_utils/provider_message_converters.py` adds it; route any new Gemini
  history path through it.
- **The env example is `.example.env`**, not `.env.example`. Every variable is in
  `docs/reference/configuration.md`.
- **The frontend's docs site copies files from this repo's `docs/`.** Renaming or moving one
  breaks `dare-frontend/docs-site/scripts/sync-content.mjs`; update it in a paired PR.
