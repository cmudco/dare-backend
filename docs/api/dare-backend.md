# DARE Backend API Reference

The DARE backend API is auto-documented using **drf-spectacular** (OpenAPI 3.0).

## Interactive Documentation

When the backend is running locally:

- **OpenAPI Schema**: [http://localhost:8000/api/schema/](http://localhost:8000/api/schema/) — Raw JSON/YAML schema for code generation and the most reliable local API reference
- **Swagger UI**: [http://localhost:8000/api/docs/](http://localhost:8000/api/docs/) — Interactive API explorer. This page loads Swagger UI assets from a CDN, so it may not render on offline or restricted networks.
- **ReDoc**: [http://localhost:8000/api/redoc/](http://localhost:8000/api/redoc/) — Clean, readable API reference

## Authentication

All endpoints require JWT authentication unless noted otherwise. Include the access token in the `Authorization` header:

```
Authorization: Bearer <access_token>
```

Tokens are obtained via `POST /users/api/dj-rest-auth/login/` and refreshed via `POST /users/api/dj-rest-auth/token/refresh/`.

## API Overview

| Module | Base Path | Description |
|---|---|---|
| Auth | `/users/api/dj-rest-auth/` | Login, registration, password management |
| Conversations | `/api/conversations/` | Chat CRUD, messages, cloning, sharing |
| Ensemble presets | `/api/ensemble-presets/` | Private saved role briefs and per-seat angles |
| Files | `/api/files/` | Upload, processing, folders, tags |
| Workflows | `/api/workflows/` | Workflow CRUD, runs, versions |
| Prompts | `/api/prompts/` | Prompt template management |
| Agents | `/api/agents/` | AI agent configuration |
| LLM Models | `/api/llms/` | Available models and pricing |
| Billing | `/api/billing/` | Token usage and cost tracking |
| Notifications | `/api/notifications/` | User notifications |
| MCP | `/mcp/` | Model Context Protocol servers |
| DARE Tools | `/dare/` | Tool plugins |
| Memory | `/api/items/` | Context memory management |
| Sharing | `/api/sharing/` | Conversation and workflow sharing |
| API Keys | `/api/api-keys/` | External API key management |

## Ensemble briefs and presets

`GET /api/ensemble-presets/defaults/` returns the authenticated user's effective
`responder`, `evaluator`, and `chairman` role instructions. The preset collection
supports paginated listing and creation; detail endpoints support retrieval,
updates, and soft deletion. Presets are private to their owner.

Preset names accept up to 80 characters, role briefs up to 4,000 characters, and
each positional seat angle up to 300 characters. Empty role text uses the default
instructions. Multiple presets may share a name; clients identify them by ID.

Apply migration `conversations.0100_ensemble_preset` before deploying the briefs
frontend. It adds a table without changing existing conversations. Panel/council
execution remains controlled by the existing `enable_ensemble` feature flag.

## LiteLLM background models

LiteLLM wallets expose one optional `backgroundModel` setting through
`/api/billing/wallets/litellm/`. DARE uses it for invisible text work such as
conversation titles, summaries, memory extraction, and advanced RAG query
analysis. An empty value falls back to DARE's configured background model.

Both LiteLLM connection-test endpoints return `models` plus a nullable
`suggestedModel`. The recommendation prefers GPT-5.6 Luna, then the newest
Gemini Flash model, then another lightweight text model. Clients may highlight
the recommendation, but the user remains free to select any returned model.

## Document reprocessing and vision model selection

`POST /api/files/{id}/reprocess/` accepts these camelCase JSON fields:

| Field | Behavior |
|---|---|
| `action` | `reparse` processes the stored original; `retry_images` retries failed figure descriptions only. |
| `processingMode` | Required for `reparse`: `basic` or `advanced`. Omit for `retry_images`, which retains the existing mode. |
| `modelIdentifier` | Optional vision model for this run. Used for Advanced reprocessing or failed-image retries without changing the upload preference. |

The selected model must be available for vision in the authenticated user's active wallet. An unavailable selection returns `409` without queuing work; the worker also checks availability before making model calls. Requests without `modelIdentifier` retain existing model-resolution behavior for older clients.

Failed-image retries preserve successful descriptions. Full Advanced reprocessing uses the selected model for applicable scanned-page transcription and figure descriptions; scanned-page approval limits still apply. Cached outputs are scoped by user, image content, context, model, and prompt version, so changing models cannot reuse a different model's cached description.

Deploy the API and restart background workers before releasing a frontend that sends `modelIdentifier`. This is an additive request field and requires no database migration.

## Improving Documentation

To add descriptions to endpoints, use `@extend_schema` decorators on custom `@action` methods:

```python
from drf_spectacular.utils import extend_schema, OpenApiParameter

@extend_schema(
    summary="Export conversation as PDF",
    parameters=[OpenApiParameter(name="id", type=str, location="path")],
    responses={200: OpenApiTypes.BINARY},
)
@action(detail=True, methods=["get"], url_path="export-pdf")
def export_pdf(self, request, pk=None):
    ...
```

## Document upload processing

`POST /api/files/` accepts multipart `files` and matching `names`, plus optional
`processing_mode`: `basic` or `advanced` (default). Multipart field names use
underscores; the JSON response exposes the saved choice as `processingMode`.
Invalid processing modes return HTTP 400 before any file is created.

- **Basic (text only):** uses flat-text extraction and skips Docling layout and
  visual extraction. Scanned documents without embedded text need Advanced.
- **Advanced (Docling):** preserves document structure, tables, and images for
  supported document formats and uses the existing visual enrichment workflow.
  Plain-text formats use text extraction; existing fallback diagnostics remain
  visible if Docling cannot parse a document. Standalone media handling is unchanged.

The mode is stored per file and reused when reprocessing. Existing files and
callers that omit the option retain Advanced behavior. The mode cannot be changed
with a file metadata PATCH; upload another copy to compare both approaches.
Apply `files.0024_file_processing_mode` before deploying the new upload UI and
restart API/worker processes. The stored `parser_name` remains parser provenance
(`basic`, `docling`, or another parser), separate from the requested processing mode.

The authenticated `GET /api/files/{id}/viewer-capabilities/` endpoint returns
`structure` and `map` booleans. Both require a persisted structured parse from
Docling or the notebook parser, including document elements. Basic uploads,
text-only fallbacks, and files awaiting parsing expose neither view. The frontend
uses these capabilities rather than inferring them from the requested mode.
Migration 0025 normalizes historical `legacy` parser identifiers to `basic` in
file metadata and the stored document model; processing history remains an audit record.

### Reprocess a stored document

`POST /api/files/{id}/reprocess/` accepts JSON `{ "action": "reparse",
"processingMode": "basic" | "advanced" }` or `{ "action": "retry_images" }`.
It returns the updated file with HTTP 202 and a job ID. Only the owner can start
work. Media, missing originals, and already-running work are rejected with 409;
queue failures return 503 and restore the previous file status. Poll the existing
job-status endpoint, then refresh file metadata when processing finishes.

Reparse retains the file ID and original bytes, honors the newly selected mode,
and renews scanned-page approval if needed. Image retry uses the saved lossless
Docling parse, retries only failed figure descriptions, and preserves completed
figures and page transcriptions. Older incomplete document models must first be
reprocessed with Advanced. `failedImageCount` in file metadata identifies retryable
figure failures. Existing wallet selection, billing, caches, and processing traces
apply; image retries also rebuild embeddings to make recovered descriptions searchable.

The active index stays searchable while replacement is built. New vectors use a
separate generation; chunks and the generation pointer publish transactionally,
then old vectors are retired. Empty or failed replacements retain the previous
parse and index. Failed staged-vector cleanup is logged; unpublished generations
are excluded from retrieval. Duplicate delivery of a completed job does not rerun it.
A failed reprocess keeps its failure trace and reports that the previous document
was retained. Scanned documents may pause for the existing OCR approval workflow.

The existing conversation tool-call activity and retrieval trace expose actual
retrieval steps and sources. This feature adds no agentic whole-file-context tool.
