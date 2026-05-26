# SocraticBooks-DARE Proxy Contract

SocraticBooks depends on DARE for authentication, files, LLM access, billing, and selected internal owner-facing views. This document explains the integration rules that backend contributors need before changing API behavior.

## Direction of Calls

| Caller | Callee | Purpose |
|---|---|---|
| `dare-frontend` | `dare-backend` | Direct DARE product UI calls. |
| `socraticbooks-backend` | `dare-backend` | Auth, files, model lists, conversations, billing, and internal sync. |
| `socraticbooks-react` | `socraticbooks-backend` | SocraticBooks UI calls. It must not call DARE directly. |
| `dare-backend` | `socraticbooks-backend` | Internal callbacks such as bot billing and transcript upload flows. |

## Authentication Rules

- End-user requests use the DARE-issued JWT in `Authorization: Bearer <token>`.
- Backend-to-backend requests use `X-Internal-Key: $DARE_INTERNAL_KEY`.
- `DARE_INTERNAL_KEY` must match on both services in local and deployed environments.
- SocraticBooks decodes DARE JWTs using the shared signing secret configured for the environment.

## Wire Format

- HTTP request and response bodies are camelCase on the wire.
- Python code remains snake_case internally.
- Both Django backends use `djangorestframework-camel-case` at the API boundary.
- Do not introduce ambiguous union-shaped payloads for frontend consumers. Prefer separate named fields for different result shapes.

## Change Rules

Before changing a DARE endpoint consumed by SocraticBooks:

1. Search `socraticbooks-backend/backend/utils/dare_api/` for callers.
2. Update the relevant SocraticBooks client code in the same change set or coordinate a paired PR.
3. Update the API reference or contract docs for the changed endpoint.
4. Include migration and rollout notes when the change is breaking.

Breaking changes require a coordinated DARE backend and SocraticBooks backend deployment. Additive fields are preferred whenever possible.

## Local Development Defaults

| Service | Default URL |
|---|---|
| DARE backend | `http://localhost:8000` |
| DARE frontend | `http://localhost:5173` |
| SocraticBooks backend | `http://localhost:8001` |
| SocraticBooks frontend | `http://localhost:5174` |

## Related Docs

- [Getting Started](../getting-started.md)
- [Socket.IO Events](../architecture/socketio-events.md)
- [Serialization Contract](../serialization.md)
- [API Reference](../api/dare-backend.md)
