# Changelog

All notable changes to the DARE backend are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- Documentation overhaul: added `INSTALL.md`, `docs/configuration.md`, `docs/architecture.md`, `docs/admin-guide.md`, `docs/contributing.md`, `SECURITY.md`, and `CHANGELOG.md`.

---

## [0.1.0] — Initial Release

First public release of the DARE backend. Establishes the platform for multi-LLM research and conversation.

### Added

#### Core platform
- Django 5.1 REST API scaffold with layered settings (`local`, `staging`, `production`).
- Custom `User` model with email-based authentication via `dj-rest-auth` and JWT.
- Soft-delete and active-objects model mixins via `BaseModel`.
- Sentry integration (optional via `SENTRY_DSN`).

#### Multi-LLM support
- Pluggable `AIService` abstract base with concrete implementations for:
  - OpenAI (GPT-4 / GPT-4o family)
  - Anthropic (Claude 3 / 3.5 family)
  - Google (Gemini)
  - Ollama (self-hosted LLaMA and other open models)
- Per-model pricing records with input/output token rates.
- Model groups for fine-grained user access control.

#### Real-time chat
- Socket.IO server with `/chat` namespace for streaming completions.
- Token-by-token streaming, message persistence, and per-message token usage tracking.
- Like/dislike feedback with optional free-text comment.

#### File processing & RAG
- File upload with background processing pipeline (Django RQ).
- Document chunking and embedding via `DocumentProcessor`.
- Pluggable `VectorService` with backends for Pinecone (managed) and Weaviate (self-hosted).
- Per-user, per-conversation RAG over uploaded documents.
- Tag and folder organization.

#### Workflow automation
- Visual DAG builder backend: workflows, steps, batch runs, run history.
- Socket.IO `/workflow` namespace for streaming step execution.
- Manual and auto execution modes.

#### Authentication & access
- Access-code-gated registration with per-code default model groups.
- Cross-platform auth scopes (`auth_source`) for partner integrations.
- Internal-key-protected endpoints for backend-to-backend calls.

#### Billing
- Token usage tracking per message, per user, per model.
- Configurable per-million-token pricing per model.

#### MCP (Model Context Protocol)
- MCP server bridge for tool-use extensions.
- Optional Docker-based MCP execution via `MCP_USE_DOCKER`.

#### SyftBox
- Optional SyftBox integration for federated data sharing.

#### Operations
- Docker Compose for the API server, worker, Postgres + pgvector, Redis, Weaviate, Weaviate console, and Ollama.
- Example Nginx and systemd configurations in `INSTALL.md`.
- Drf-spectacular auto-generated OpenAPI schema at `/api/schema/`, with Swagger UI at `/api/docs/`.

### Known limitations
- Production Docker images are not yet published.
- Test coverage is partial; integration tests for Socket.IO consumers are minimal.
