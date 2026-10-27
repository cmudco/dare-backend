# DARE Backend Documentation

The DARE backend handles accounts, LLM calls, real-time chat and workflow streaming over
Socket.IO, file processing and RAG, billing, and research mode.

Pages are grouped by what you are trying to do.

## Tutorials: learn by doing

- [Getting Started](tutorials/getting-started.md): run backend, worker and frontend locally, and
  send a first chat.
- [Backend Quick Start](../QUICKSTART.md): the backend alone, with Docker or local Python.
- [Memory walkthrough](tutorials/memory-walkthrough.md): a guided tour of chat memory in the
  browser.

## How-to guides: reach a goal

- [Deploy to production](../DEPLOYMENT.md): Docker or bare metal, upgrades, rollback.
- [Administer DARE](how-to/administer-dare.md): users, roles, access codes, model groups,
  analytics.
- [Deploy research mode](how-to/deploy-research-mode.md): run research agents on Hermes.
- [Run the Quillmark renderer](how-to/run-quillmark.md): PDF document generation locally.

## Reference: look things up

- [Configuration](reference/configuration.md): every environment variable.
- [REST API](reference/rest-api.md): endpoints, plus the live OpenAPI schema.
- [Socket.IO events](reference/socketio-events.md): `/chat` and `/workflow` payloads.
- [Serialization](reference/serialization.md): camelCase and snake_case rules.
- [Socratic Bots integration](reference/socratic-bots-integration.md): the contract between
  DARE and Socratic Bots.
- [Non-chat model usage](reference/non-chat-model-usage.md): every model call outside the chat
  reply.
- [Scite data](reference/scite-data.md): what the Scite MCP server returns.
- [Coding rules](../rules.md): the standards every change follows.

## Explanation: understand why

- [Architecture](explanation/architecture.md): the platform and the backend's main parts.
- [Data flows](explanation/data-flows.md): chat, upload, workflow, auth and artifact sequences.
- [Document ingestion](explanation/document-ingestion.md): how an upload becomes a verified
  search index.
- [Tool loop](explanation/tool-loop.md): multi-round tool calling across providers.
- [Advanced RAG](explanation/advanced-rag.md): the retrieval pipeline and its measurements.
- [Shared libraries](explanation/shared-libraries.md): importing an external vector corpus.
- [Quillmark](explanation/quillmark.md): how document generation plugs into chat.

## With a backend running

- OpenAPI schema: http://localhost:8000/api/schema/
- Swagger UI: http://localhost:8000/api/docs/ (loads assets from a CDN)
- ReDoc: http://localhost:8000/api/redoc/
