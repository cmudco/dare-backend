# Configuration Reference

Every environment variable consumed by the DARE backend, with type, default, and description. Variables are loaded from `.env` at startup via `config/env.py`.

A working template lives at `.example.env` — copy it to `.env` and edit.

---

## Django Core

| Variable | Type | Default | Description |
|---|---|---|---|
| `DJANGO_SETTINGS_MODULE` | string | `config.settings.local` | Settings module to load. Use `config.settings.production` in prod. |
| `DJANGO_SECRET_KEY` | string | *(required)* | Django secret key. **Rotate this** from the example value before any non-local use. |
| `DJANGO_DEBUG` | bool | `False` | Enables debug responses and the Django debug toolbar. Must be `False` in production. |
| `ENVIRONMENT` | string | `local` | Free-form environment label (`local`, `staging`, `production`). Used in logs and Sentry tags. |
| `SITE_ID` | int | `1` | Django sites framework site ID. |
| `ALLOWED_HOSTS` | csv | `127.0.0.1,localhost` | Comma-separated hostnames Django will serve. |
| `CORS_ALLOWED_ORIGINS` | csv | *(none)* | Comma-separated origins permitted to make CORS requests. |
| `CSRF_TRUSTED_ORIGINS` | csv | *(none)* | Comma-separated origins trusted for CSRF-protected POSTs. |

## Database

| Variable | Type | Default | Description |
|---|---|---|---|
| `USE_POSTGRES` | bool | `False` | When `True`, connect to Postgres using `DB_*` below. When `False`, use SQLite at `dare_local.db`. |
| `DB_NAME` | string | *(empty)* | Postgres database name. |
| `DB_USER` | string | `postgres` | Postgres user. |
| `DB_PASSWORD` | string | *(empty)* | Postgres password. |
| `DB_HOST` | string | `localhost` | Postgres host. |
| `DB_PORT` | string | `5432` | Postgres port. |

## Redis & Background Jobs

| Variable | Type | Default | Description |
|---|---|---|---|
| `REDIS_HOST` | string | `localhost` | Redis server host. Used by Django RQ and Channels. |
| `REDIS_PORT` | int | `6379` | Redis server port. |
| `REDIS_DB` | int | `0` | Redis logical database number. |
| `REDIS_PASSWORD` | string | *(none)* | Redis password, if AUTH is enabled. |

## LLM Provider Keys

At least one provider key should be set. Missing keys disable the corresponding provider but do not block startup.

| Variable | Type | Default | Description |
|---|---|---|---|
| `OPENAI_API_KEY` | string | *(none)* | OpenAI API key (`sk-...`). Required for GPT models. |
| `CLAUDE_API_KEY` | string | *(none)* | Anthropic API key (`sk-ant-...`). Required for Claude models. |
| `GEMINI_API_KEY` | string | *(none)* | Google AI API key. Required for Gemini models. |
| `OLLAMA_HOST` | url | `http://localhost:11434` | Ollama server URL for self-hosted LLaMA models. |
| `ELEVENLABS_API_KEY` | string | *(none)* | ElevenLabs API key for text-to-speech features. Optional. |

## Vector Stores

DARE supports both Pinecone (managed) and Weaviate (self-hosted). Either or both can be configured; users choose at runtime via preferences.

### Pinecone

| Variable | Type | Default | Description |
|---|---|---|---|
| `PINECONE_API_KEY` | string | *(none)* | Pinecone API key. |
| `PINECONE_INDEX_NAME` | string | *(none)* | Pinecone index to read/write document embeddings. |

### Weaviate

| Variable | Type | Default | Description |
|---|---|---|---|
| `WEAVIATE_HOST` | string | `localhost` | Weaviate server host. |
| `WEAVIATE_PORT` | int | `8080` | Weaviate REST port. |
| `WEAVIATE_COLLECTION_NAME` | string | `Document` | Collection (class) name for stored embeddings. |
| `WEAVIATE_SKIP_INIT_CHECKS` | bool | `True` | Skip startup connectivity checks. |
| `WEAVIATE_AUTOSCHEMA_ENABLED` | bool | `False` | Allow Weaviate to auto-create schema (not recommended for prod). |

## Email

| Variable | Type | Default | Description |
|---|---|---|---|
| `EMAIL_BACKEND` | string | `django.core.mail.backends.console.EmailBackend` | Django email backend. Use `django.core.mail.backends.smtp.EmailBackend` in prod. |
| `EMAIL_HOST` | string | *(none)* | SMTP host. |
| `EMAIL_PORT` | int | `587` | SMTP port. |
| `EMAIL_HOST_USER` | string | *(none)* | SMTP username. |
| `EMAIL_HOST_PASSWORD` | string | *(none)* | SMTP password. |
| `EMAIL_FROM` | string | *(none)* | Default `From:` address for outbound mail. |
| `EMAIL_USE_TLS` | bool | `False` | Negotiate TLS via STARTTLS (port 587). |
| `EMAIL_USE_SSL` | bool | `False` | Use TLS from connect (port 465). Mutually exclusive with `EMAIL_USE_TLS`. |

## Cross-Platform URLs

Used for unified authentication, password-reset emails, and inter-service callbacks.

| Variable | Type | Default | Description |
|---|---|---|---|
| `FRONTEND_CONFIRM_EMAIL_URL` | url | *(none)* | Base URL for email-confirmation links. |
| `FRONTEND_PASSWORD_RESET_URL` | url | *(none)* | Base URL for password-reset links. |
| `DARE_FRONTEND_URL` | url | *(none)* | Public URL of the DARE frontend. |
| `DARE_BACKEND_URL` | url | *(none)* | Public URL of this backend. |
| `SOCRATIC_BOTS_FRONTEND_URL` | url | *(none)* | Partner platform frontend URL (optional). |
| `SOCRATIC_BOTS_BACKEND_URL` | url | *(none)* | Partner platform backend URL (optional). |
| `DARE_INTERNAL_KEY` | string | `local-dev-internal-key` | Shared secret for partner backends calling internal endpoints. **Rotate** in production. |

## Observability

| Variable | Type | Default | Description |
|---|---|---|---|
| `SENTRY_DSN` | url | *(none)* | Sentry DSN. Empty disables Sentry reporting. |

## MCP (Model Context Protocol)

| Variable | Type | Default | Description |
|---|---|---|---|
| `MCP_USE_DOCKER` | bool | `False` | When `True`, MCP servers are launched in Docker containers; otherwise as subprocesses. |

## SyftBox

[SyftBox](https://syftbox.net) integration for federated data sharing.

| Variable | Type | Default | Description |
|---|---|---|---|
| `SYFTBOX_ENABLED` | bool | `False` | Enable SyftBox sync. |
| `SYFTBOX_DATASITES_ROOT` | path | *(none)* | Root path for local SyftBox datasites. |
| `SYFTBOX_APP_NAME` | string | `dare` | App name registered with SyftBox. |
| `SYFTBOX_BASE_URL` | url | `https://syftbox.net` | SyftBox base URL. |
| `SYFTBOX_SYNC_INTERVAL_SECONDS` | int | `300` | Sync interval in seconds. |

---

## Adding a new variable

1. Add the variable to `.example.env` with a placeholder or default.
2. Add it to your local `.env`.
3. Declare it in `config/env.py`:

   ```python
   MY_VAR = os.getenv("MY_VAR", "default_value")
   ```

4. Import where needed:

   ```python
   from config import env
   value = env.MY_VAR
   ```

5. Document it in this file.

## Validating configuration

A quick check after editing `.env`:

```bash
python manage.py check
python manage.py shell -c "from config import env; print(env.OPENAI_API_KEY[:7])"
```
