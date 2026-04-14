# DARE Backend API Reference

The DARE backend API is auto-documented using **drf-spectacular** (OpenAPI 3.0).

## Interactive Documentation

When the backend is running locally:

- **Swagger UI**: [http://localhost:8000/api/docs/](http://localhost:8000/api/docs/) — Interactive API explorer with "Try it out" functionality
- **ReDoc**: [http://localhost:8000/api/redoc/](http://localhost:8000/api/redoc/) — Clean, readable API reference
- **OpenAPI Schema**: [http://localhost:8000/api/schema/](http://localhost:8000/api/schema/) — Raw JSON/YAML schema for code generation

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
