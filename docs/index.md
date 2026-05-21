# DARE Backend Documentation

Primary backend for the DARE platform. Handles AI/LLM services, real-time chat and workflow streaming via Socket.IO, file processing with RAG, and authentication.

## Quick Links

- [Getting Started](getting-started.md) — Set up and run the full platform locally
- [Architecture Overview](architecture/overview.md) — System diagrams and component descriptions
- [OpenAPI Schema](http://localhost:8000/api/schema/) — Raw API schema (requires running server)
- [Swagger UI](http://localhost:8000/api/docs/) — Interactive API explorer when CDN assets are reachable

## Contents

### Architecture
- [System Overview](architecture/overview.md) — Full platform architecture, component responsibilities, service layer patterns
- [Socket.IO Events](architecture/socketio-events.md) — Complete real-time event reference (`/chat` and `/workflow` namespaces)
- [Data Flows](architecture/data-flows.md) — Chat, file upload, workflow execution, and auth flow diagrams

### API Reference
- [DARE Backend API](api/dare-backend.md) — REST API (auto-generated via drf-spectacular)

### Development
- [Code Standards](code-standards.md) — Backend and frontend coding conventions
- [Serialization Contract](serialization.md) — camelCase/snake_case conversion rules

### Deployment
- [Infrastructure](deployment/infrastructure.md) — Production server setup
- [Procedures](deployment/procedures.md) — Deployment steps

## Related Repos

| Repo | Role |
|---|---|
| dare-frontend | React 18 / Redux frontend for DARE |
| socraticbooks-backend | Educational platform (proxies auth + AI to this backend) |
| socraticbooks-react | React 19 / Zustand frontend for SocraticBooks |
