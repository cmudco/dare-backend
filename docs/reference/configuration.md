# Configuration

The backend reads every variable from `.env` at startup in `config/env.py`, and the settings
modules in `config/settings/` (`common`, `local`, `production`) use those values. Copy
`.example.env` to `.env` and edit it.

Defaults below are the code defaults, used when a variable is unset. `.example.env` sets some of
them differently for local development.

## Values that must match across services

| Variable | Must equal |
| --- | --- |
| `SSO_JWT_KEY` | The same variable in socraticbots-backend, which verifies DARE's JWTs |
| `DARE_INTERNAL_KEY` | The same variable in socraticbots-backend and voice-agent-runtime |

## Django core

| Variable | Default | Description |
| --- | --- | --- |
| `DJANGO_SETTINGS_MODULE` | `config.settings.production` | Settings module. `manage.py`, `dare/asgi.py` and `dare/wsgi.py` fall back to production when unset; `.example.env` sets `config.settings.local`. |
| `DJANGO_SECRET_KEY` | **Required** | Django secret key. Replace the example value before any non-local use. |
| `SSO_JWT_KEY` | *(empty)* | Signs the JWTs DARE issues (SimpleJWT `SIGNING_KEY`). Set it in every deployment. |
| `DJANGO_DEBUG` | `False` | Debug responses when exactly `True`. Must be `False` in production. |
| `ENVIRONMENT` | `local` | One of `local`, `dare-staging`, `dare-production`, `gt-production` (`config/environment.py`). Any other value logs a warning and falls back to `local`. |
| `SITE_ID` | `1` | Django sites framework site ID. |
| `ALLOWED_HOSTS` | `127.0.0.1,localhost` | Comma-separated hostnames Django serves. |
| `CORS_ALLOWED_ORIGINS` | `https://dare-front.hss.cmu.edu` | Comma-separated origins allowed to make CORS requests. Production settings only; local settings allow all origins. |
| `CSRF_TRUSTED_ORIGINS` | `https://dare-front.hss.cmu.edu` | Comma-separated origins trusted for CSRF-protected POSTs. Production settings only. |

## Database

| Variable | Default | Description |
| --- | --- | --- |
| `USE_POSTGRES` | `False` | Local settings only: `True` connects to Postgres with the `DB_*` values; otherwise SQLite at `db.sqlite3`. Production settings always use Postgres. |
| `DB_NAME` | *(none)* | Postgres database name. |
| `DB_USER` | *(none)* | Postgres user. |
| `DB_PASSWORD` | *(none)* | Postgres password. Docker Compose refuses to start without it. |
| `DB_HOST` | *(none)* | Postgres host. |
| `DB_PORT` | *(none)* | Postgres port. |

## Redis and background jobs

| Variable | Default | Description |
| --- | --- | --- |
| `REDIS_HOST` | `127.0.0.1` | Redis host for RQ, the cache and the channel layer. |
| `REDIS_PORT` | `6379` | Redis port. |
| `REDIS_DB` | `1` | Redis logical database. |
| `REDIS_PASSWORD` | *(none)* | Redis password when AUTH is on. |
| `INGESTION_RECONCILE_INTERVAL_SECONDS` | `300` | How often the scheduler sweeps files stranded in Processing by a dead worker. `0` disables the recurring sweep; workers still sweep at startup. |
| `CONVERSATION_SUMMARY_JOBS_ENABLED` | `False` | Enable rolling conversation summaries. Off by default to keep ingestion workers free. |

## LLM providers

`OPENAI_API_KEY`, `CLAUDE_API_KEY`, `GEMINI_API_KEY`, `PINECONE_API_KEY` and
`PINECONE_INDEX_NAME` must be present in the environment, or startup fails with
`ImproperlyConfigured`. An empty value is allowed and disables that provider.

| Variable | Default | Description |
| --- | --- | --- |
| `OPENAI_API_KEY` | **Required** (may be empty) | OpenAI API key, for GPT models. |
| `CLAUDE_API_KEY` | **Required** (may be empty) | Anthropic API key, for Claude models. |
| `GEMINI_API_KEY` | **Required** (may be empty) | Google AI API key, for Gemini models. |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama server URL for self-hosted LLaMA models. |
| `BACKGROUND_MODEL` | `gpt-5.6-luna` | Model for titles, summaries, memory extraction and Advanced RAG query analysis. |
| `LLM_STREAM_IDLE_TIMEOUT_SECONDS` | `120` | How long a provider stream may stay silent before the tool loop fails the round. |
| `LLM_SANDBOX_IDLE_TIMEOUT_SECONDS` | `300` | The same limit while sandbox code runs or files upload. |
| `BOT_WALLET_ENFORCEMENT_ENABLED` | `0` | `1` applies bot wallet routing (the chatter pays; anonymous chats fall back to the bot owner). |

## Vector stores

You can configure Pinecone (managed), Weaviate (self-hosted) or both; users choose in their
preferences.

| Variable | Default | Description |
| --- | --- | --- |
| `PINECONE_API_KEY` | **Required** (may be empty) | Pinecone API key. |
| `PINECONE_INDEX_NAME` | **Required** (may be empty) | Pinecone index for document embeddings. |
| `WEAVIATE_HOST` | `localhost` | Weaviate host. |
| `WEAVIATE_PORT` | `8080` | Weaviate HTTP port. |
| `WEAVIATE_GRPC_PORT` | `50051` | Weaviate gRPC port. Searches use gRPC, so remap it whenever you remap the HTTP port. |
| `WEAVIATE_COLLECTION_NAME` | `Document` | Collection for stored embeddings. |
| `WEAVIATE_SKIP_INIT_CHECKS` | `True` | Skip startup connectivity checks. |
| `WEAVIATE_AUTOSCHEMA_ENABLED` | `False` | Let Weaviate auto-create the schema. Not recommended in production. |
| `SOURCE_WEAVIATE_URL` | *(none)* | Source Weaviate for `manage.py import_library`, separate from DARE's own. |
| `SOURCE_WEAVIATE_CLASS` | *(none)* | Source class for `import_library`. |
| `SOURCE_WEAVIATE_API_KEY` | *(none)* | Source API key for `import_library`. |

## Document processing

| Variable | Default | Description |
| --- | --- | --- |
| `DOCUMENT_ENRICHMENT_ENABLED` | `True` | Docling figure descriptions and scanned-page transcription. |
| `DOCUMENT_ENRICHMENT_MODEL` | `gemini-3.1-flash-lite` | Vision model, resolved from DARE's LLM catalog so billing stays consistent. |
| `DOCUMENT_ENRICHMENT_CONCURRENCY` | `2` | Simultaneous vision operations per document, clamped to 1–4. |
| `DOCUMENT_ENRICHMENT_MAX_FIGURES` | `40` | Most figures described per document. |
| `DOCUMENT_OCR_AUTO_PAGE_LIMIT` | `10` | Pages transcribed automatically. |
| `DOCUMENT_OCR_MAX_PAGE_LIMIT` | `100` | Most pages a user can choose to transcribe. |
| `DOCUMENT_OCR_ESTIMATED_INPUT_TOKENS_PER_PAGE` | `5000` | Input tokens per page for the cost estimate. |
| `DOCUMENT_OCR_ESTIMATED_OUTPUT_TOKENS_PER_PAGE` | `2000` | Output tokens per page for the cost estimate. |
| `DOCUMENT_OCR_JOB_TIMEOUT_SECONDS` | `3600` | RQ timeout for a transcription job. |

## Advanced RAG

These are read from Django settings first, then the environment. Boolean flags treat `0`,
`false`, `no` and `off` as off.

| Variable | Default | Description |
| --- | --- | --- |
| `RAG_RERANKER_MODEL` | `BAAI/bge-reranker-v2-m3` | Reranker. BGE gives calibrated 0–1 scores; `cross-encoder/ms-marco-MiniLM-L-6-v2` is the fast local option (raw logits). |
| `RAG_GROUNDING_THRESHOLD` | `0.3` (BGE) or `0.0` (MiniLM / MS MARCO) | Grounding cutoff on the reranker's score scale. |
| `RAG_RERANKER_DEVICE` | auto | Device for the reranker model. |
| `RAG_RERANKER_MAX_LEN` | `512` | Reranker max sequence length. |
| `RAG_CONTEXT_CHAR_BUDGET` | `16000` | Characters of retrieved context per prompt. |
| `RAG_SNIPPET_CHAR_CAP` | `2000` | Characters kept per snippet. |
| `RAG_GRAPH_EXPAND_ENABLED` | `true` | On the document path, follow stored in-document references ("see section 7.2") one hop from each retrieved chunk before reranking. Turning it off leaves ingest unchanged. |
| `RAG_ENTITY_NER_ENABLED` | `true` | Run the GLiNER named-entity lane at ingest alongside the regex identifier lane. Identifiers still run when it is off. |
| `RAG_ENTITY_MODEL` | `urchade/gliner_small-v2.1` | GLiNER checkpoint for the NER lane, loaded on first use. |
| `RAG_ENTITY_HOPS_ENABLED` | `true` | With two or more files selected, follow a hit's rarest shared entity into one chunk of another selected file during the expand stage. |

## Email

Registration sends a verification link, and users can't sign in until they open it. Set
`EMAIL_PROVIDER` to choose how mail is sent. Every provider also writes an `EmailLog` row.

- **`resend`:** sends through the [Resend](https://resend.com) API with `RESEND_API_KEY`.
  `EMAIL_FROM` must use a sender or domain verified in Resend.
- **`smtp`:** sends through your SMTP server, signing in with `EMAIL_HOST_USER` and
  `EMAIL_HOST_PASSWORD` when they are set.
- **`console`:** only writes mail to the log. Nothing reaches an inbox, so the frontend tells
  new users to ask an administrator to verify their account.

When `EMAIL_PROVIDER` is unset, the backend infers it, as older versions did: `resend` when
`EMAIL_HOST_USER` and `EMAIL_HOST_PASSWORD` are both set (the password is then the Resend
key), `smtp` when only `EMAIL_HOST` is set, and `console` otherwise. Set the provider
explicitly for authenticated SMTP, because a username and password alone select Resend.

| Variable | Default | Description |
| --- | --- | --- |
| `EMAIL_PROVIDER` | *(inferred)* | `resend`, `smtp` or `console`. Any other value stops startup. |
| `EMAIL_FROM` | *(none)* | `From:` address for every email. |
| `EMAIL_HOST` | *(none)* | SMTP host. |
| `EMAIL_PORT` | `587` | SMTP port. |
| `EMAIL_HOST_USER` | *(none)* | SMTP username. |
| `EMAIL_HOST_PASSWORD` | *(none)* | SMTP password. Also the Resend key when `RESEND_API_KEY` is unset. |
| `EMAIL_USE_TLS` | `False` | Upgrade the SMTP connection with STARTTLS, usually on port 587. |
| `EMAIL_USE_SSL` | `False` | Connect over implicit TLS, usually on port 465. Don't combine with `EMAIL_USE_TLS`. |
| `RESEND_API_KEY` | `EMAIL_HOST_PASSWORD` | Resend API key. |

The link in the email is `FRONTEND_CONFIRM_EMAIL_URL` followed by `/?key=…`, so set it to the
public address of the frontend's confirmation page, without a trailing slash, for example
`https://dare.example.edu/users/confirm-email`.

A self-hosted deployment using an authenticated SMTP relay:

```bash
EMAIL_PROVIDER=smtp
EMAIL_HOST=smtp.example.edu
EMAIL_PORT=587
EMAIL_HOST_USER=dare-mailer@example.edu
EMAIL_HOST_PASSWORD=your_smtp_password
EMAIL_USE_TLS=True
EMAIL_FROM="DARE <dare-mailer@example.edu>"
FRONTEND_CONFIRM_EMAIL_URL=https://dare.example.edu/users/confirm-email
```

To check what the backend selected:

```bash
python manage.py shell -c "from django.conf import settings; print(settings.EMAIL_PROVIDER, settings.EMAIL_BACKEND)"
```

## Cross-platform URLs and keys

Used for unified authentication, email links and inter-service calls.

| Variable | Default | Description |
| --- | --- | --- |
| `FRONTEND_CONFIRM_EMAIL_URL` | *(none)* | Public URL of the frontend's confirmation page, without a trailing slash. Verification links are built from it. |
| `FRONTEND_PASSWORD_RESET_URL` | *(none)* | Base URL for password-reset links. |
| `DARE_FRONTEND_URL` | *(none)* | Public URL of the DARE frontend. |
| `DARE_BACKEND_URL` | *(none)* | Public URL of this backend. |
| `SOCRATIC_BOTS_FRONTEND_URL` | *(none)* | Socratic Bots frontend URL (optional). |
| `SOCRATIC_BOTS_BACKEND_URL` | *(none)* | Socratic Bots backend URL (optional). |
| `DARE_INTERNAL_KEY` | `local-dev-internal-key` | Shared secret for partner backends calling internal endpoints. Replace it in production. |

## Research mode (Hermes)

| Variable | Default | Description |
| --- | --- | --- |
| `HERMES_GATEWAY_URL` | `http://127.0.0.1:8642` | Hermes agent gateway. |
| `HERMES_PROFILES_ROOT` | `~/.hermes/profiles` | Directory of per-project Hermes profiles (one `HERMES_HOME` each, served under `/p/<profile>/`). |
| `HERMES_PROFILE_PREFIX` | `dare-proj` | Profile name prefix. Names must match `[a-z0-9][a-z0-9_-]{0,63}`. |
| `HERMES_PROFILE_MODEL` | `claude-sonnet-5` | Model pinned into every project profile. |
| `HERMES_PROFILE_MODEL_PROVIDER` | `anthropic` | Provider for that model. |
| `HERMES_PROFILE_MODEL_BASE_URL` | *(empty)* | OpenAI-compatible gateway (such as LiteLLM) that every profile calls instead of a named provider. |
| `HERMES_PROFILE_MODEL_API_KEY` | *(empty)* | Key DARE writes into each profile's `.env`, under the provider's variable when no gateway URL is set. |
| `HERMES_PROFILE_FALLBACK_MODELS` | *(empty)* | Ordered fallback chain on rate limits or outages: comma-separated `provider=model` pairs. |
| `HERMES_PROFILE_MAX_TURNS` | `30` | Agent-loop ceiling written into every profile. |
| `HERMES_PROFILE_TOKEN_DAYS` | `365` | Lifetime of the owner's JWT in a profile's `.env`. |
| `HERMES_STREAM_IDLE_SECONDS` | `180` | How long a run's event stream may stay silent before DARE polls Hermes for the run's state. |
| `HERMES_RUN_WAIT_SECONDS` | `1800` | How long a background run keeps polling once its stream goes quiet. |
| `HERMES_TOOLS_WAIT_SECONDS` | `90` | How long a run waits for Hermes to load a new profile's DARE tools. |
| `RESEARCH_PROJECT_SPEND_CAP_USD` | `0` | Most a research project may spend on agent runs, in USD. `0` means no cap. |
| `OPENALEX_MAILTO` | *(empty)* | Identifies DARE to OpenAlex paper search, which joins its faster polite pool. |
| `DARE_MCP_GATEWAY_URL` | `DARE_BACKEND_URL` + `/mcp/api/gateway/` | Where a profile's MCP client reaches DARE. Must resolve from the gateway host, not the browser. |

## Platform assistant (Ask DARE)

| Variable | Default | Description |
| --- | --- | --- |
| `ASSISTANT_MODEL_IDENTIFIER` | `gpt-6-luna` | Model for the in-app assistant, paid by the platform. Must be an active model. |
| `ASSISTANT_DAILY_MESSAGE_LIMIT` | `50` | Assistant messages per user per day. |
| `ASSISTANT_AGENT_MAX_OUTPUT_RATE` | `10` | Highest output price, in USD per million tokens, of an agent model the assistant recommends. |

## Observability

| Variable | Default | Description |
| --- | --- | --- |
| `SENTRY_DSN` | *(none)* | Sentry DSN. Empty disables Sentry. |
| `SENTRY_ENVIRONMENT` | `development` | Environment tag that separates dev, staging and production in Sentry. |
| `SENTRY_TRACES_SAMPLE_RATE` | `0.1` | Fraction of requests traced for performance monitoring. |

## MCP

| Variable | Default | Description |
| --- | --- | --- |
| `MCP_USE_DOCKER` | `False` | `True` launches MCP servers in Docker containers; otherwise they run as subprocesses. |

## SyftBox

[SyftBox](https://syftbox.net) integration for federated data sharing.

| Variable | Default | Description |
| --- | --- | --- |
| `SYFTBOX_ENABLED` | `False` | Enable SyftBox sync. |
| `SYFTBOX_DATASITES_ROOT` | *(none)* | Root path for local SyftBox datasites. |
| `SYFTBOX_APP_NAME` | `dare` | App name registered with SyftBox. |
| `SYFTBOX_BASE_URL` | `https://syftbox.net` | SyftBox base URL. |
| `SYFTBOX_SYNC_INTERVAL_SECONDS` | `300` | Sync interval in seconds. |

## Docker Compose

`docker-compose.yml` reads these from `.env` and overrides the matching runtime variables inside
the `web` and `worker` containers.

| Variable | Default | Description |
| --- | --- | --- |
| `WEB_PORT` | `8000` | Host port for the API. |
| `POSTGRES_PORT` | `5432` | Host port for Postgres. |
| `WEAVIATE_CONSOLE_PORT` | `8081` | Host port for the Weaviate console (`debug` profile). |
| `OLLAMA_PORT` | `11434` | Host port for Ollama (`llama` profile). |
| `QUILLMARK_DEBUG_PORT` | `8090` | Loopback port for quillmark-mcp, for debugging. |
| `DOCKER_DJANGO_SETTINGS_MODULE` | `config.settings.production` | `DJANGO_SETTINGS_MODULE` inside the containers. |
| `DOCKER_DJANGO_DEBUG` | `False` | `DJANGO_DEBUG` inside the containers. |
| `DOCKER_ENVIRONMENT` | `production` | `ENVIRONMENT` inside the containers. |
| `DOCKER_DB_HOST` | `postgres` | `DB_HOST` inside the containers. |
| `DOCKER_REDIS_HOST` | `redis` | `REDIS_HOST` inside the containers. |
| `DOCKER_WEAVIATE_HOST` | `weaviate` | `WEAVIATE_HOST` inside the containers. |
| `DOCKER_OLLAMA_HOST` | `http://ollama:11434` | `OLLAMA_HOST` inside the containers. |
| `OLLAMA_MEMORY_LIMIT` | `16G` | Memory cap for the Ollama container. |
| `OLLAMA_MEMORY_RESERVATION` | `8G` | Memory reserved for the Ollama container. |
| `UVICORN_WORKERS` | `1` | Uvicorn workers in the `web` container when not in debug mode. |
| `LOG_LEVEL` | `info` | Uvicorn log level in the `web` container. |

## Adding a variable

1. Add it to `.example.env` with a placeholder or default.
2. Declare it in `config/env.py`, for example `MY_VAR = os.getenv("MY_VAR", "default_value")`.
3. Read it with `from config import env` and `env.MY_VAR`, or expose it in `config/settings/common.py`.
4. Document it in this file.

## Validating configuration

```bash
python manage.py check
python manage.py shell -c "from config import env; print(env.OPENAI_API_KEY[:7])"
```
