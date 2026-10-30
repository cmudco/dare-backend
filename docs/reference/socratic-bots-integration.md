# Socratic Bots integration

Read this before you change a DARE endpoint: it lists the rules Socratic Bots relies on. Socratic Bots depends on DARE for authentication, files, LLM access, billing, and selected internal owner-facing views.

## Direction of calls

| Caller | Callee | Purpose |
|---|---|---|
| `dare-frontend` | `dare-backend` | Direct DARE product UI calls. |
| `socraticbots-backend` | `dare-backend` | Auth, files, model lists, conversations, billing, and internal sync. |
| `socraticbots-frontend` | `socraticbots-backend` | Every Socratic Bots UI call except chat. |
| `socraticbots-frontend` | `dare-backend` | Chat only, over DARE's Socket.IO `/chat` namespace (`VITE_DARE_SOCKET_URL`). |
| `dare-backend` | `socraticbots-backend` | Internal callbacks such as bot billing and transcript upload flows. |

## Authentication rules

- End-user requests use the DARE-issued JWT in `Authorization: Bearer <token>`.
- Backend-to-backend requests use `X-Internal-Key: $DARE_INTERNAL_KEY`.
- `DARE_INTERNAL_KEY` must match on both services in local and deployed environments.
- Socratic Bots decodes DARE JWTs using the shared signing secret configured for the environment.

## Wire format

- HTTP request and response bodies are camelCase on the wire.
- Python code remains snake_case internally.
- Both Django backends use `djangorestframework-camel-case` at the API boundary.
- Do not add union-shaped payloads for frontend consumers. Use separate named fields for different result shapes.

## Change rules

Before changing a DARE endpoint consumed by Socratic Bots:

1. Search `socraticbots-backend/backend/utils/dare_api/` for callers.
2. Update the relevant Socratic Bots client code in the same change set or coordinate a paired PR.
3. Update the API reference or contract docs for the changed endpoint.
4. Include migration and rollout notes when the change is breaking.

Breaking changes need a coordinated DARE backend and Socratic Bots backend deployment. Prefer additive fields.

## Local development defaults

| Service | Default URL |
|---|---|
| DARE backend | `http://localhost:8000` |
| DARE frontend | `http://localhost:5173` |
| Socratic Bots backend | `http://localhost:8001` |
| Socratic Bots frontend | `http://localhost:5174` |

## Related docs

- [Getting started](../tutorials/getting-started.md)
- [Socket.IO events](socketio-events.md)
- [Serialization](serialization.md)
- [REST API](rest-api.md)
