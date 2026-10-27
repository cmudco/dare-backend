# DARE Backend — Architecture

How the backend is put together and why. For request-by-request sequence diagrams see
[data-flows.md](data-flows.md); for event payloads see
[the Socket.IO reference](../reference/socketio-events.md).

## The platform

DARE is one backend serving two products: the DARE app and Socratic Bots.

```mermaid
graph TB
    subgraph frontends["Frontends"]
        DF["dare-frontend<br/>React 18 / Redux / Vite<br/>:5173"]
        SF["socraticbots-frontend<br/>React 19 / Zustand / Vite<br/>:5174"]
    end

    subgraph backends["Backends"]
        DB["dare-backend<br/>Django ASGI: REST + Socket.IO<br/>:8000"]
        SB["socraticbots-backend<br/>Django REST<br/>:8001"]
    end

    subgraph infra["Infrastructure"]
        Redis["Redis<br/>Socket.IO pub/sub + RQ queues"]
        PG["PostgreSQL + pgvector"]
        VDB["Vector DB<br/>Weaviate / Pinecone"]
    end

    LLM["LLM providers<br/>OpenAI, Anthropic, Google, Ollama, LiteLLM"]

    DF -- "REST + Socket.IO" --> DB
    SF -- "REST" --> SB
    SF -- "Socket.IO /chat" --> DB
    SB -- "REST proxy<br/>JWT + X-Internal-Key" --> DB

    DB --> Redis
    DB --> PG
    DB --> VDB
    DB --> LLM
    SB --> PG
```

- **dare-backend** owns accounts, conversations, files and RAG, LLM calls, billing, workflows,
  MCP, memory and research mode.
- **socraticbots-backend** stores the teaching layer (tutors, access codes, notes, voice agents)
  and proxies everything else to DARE. It has no LLM or vector code.
- **socraticbots-frontend** talks REST to its own backend, but chats directly on DARE's `/chat`
  socket, sending the tutor's settings as `bot_meta`. See
  [Socratic Bots integration](../reference/socratic-bots-integration.md).

## Inside the backend

```mermaid
flowchart TB
    clients["DARE frontend, Socratic Bots, API clients"]
    proxy["Reverse proxy<br/>Nginx / ALB"]

    subgraph backend["DARE Backend (Django ASGI)"]
        api["REST API (DRF)<br/>users, files, conversations, agents, billing, API keys"]
        socket["Socket.IO<br/>/chat and /workflow namespaces"]
        services["Service layer<br/>core/services/"]
        workers["RQ workers<br/>file ingestion, email, memory, workflow steps"]
    end

    postgres["Postgres"]
    redis["Redis"]
    vectors["Weaviate / Pinecone"]
    providers["LLM provider APIs"]

    clients --> proxy
    proxy --> api
    proxy --> socket
    api --> services
    socket --> services
    services --> workers
    services --> postgres
    workers --> redis
    services --> vectors
    services --> providers
```

| Component | Responsibility | Code path |
|---|---|---|
| **REST API** | CRUD over conversations, files, prompts, users, billing | `*/views.py`, `*/serializers.py`, `*/urls.py` (some apps use `<app>/api/`) |
| **Socket.IO server** | Chat streaming and workflow execution updates | `conversations/socket_server.py` and `conversations/namespaces/`, mounted in `dare/asgi.py`; `workflows/` |
| **Service layer** | Provider integrations and multi-model coordination | `core/services/` |
| **Background workers** | File processing, embeddings, email, memory writes | `*/tasks.py`, run by `python manage.py rqworker` |
| **Models** | Domain data | `*/models.py`; mixins in `common/models.py` |
| **Settings** | `common` → `local` / `staging` / `production` | `config/settings/`, `config/env.py` |

A plain REST request goes: client → reverse proxy → Uvicorn → Django middleware → DRF JWT
authentication → view → serializer → model → Postgres → response.

## Architectural choices

- **python-socketio, not Django Channels consumers.** The Socket.IO app is mounted next to Django
  in `dare/asgi.py` and uses a Redis-backed manager, so events reach a room from any process.
  This is why the backend must run under an ASGI server (Uvicorn); `runserver` cannot serve it.
- **Service layer over fat models.** Models stay thin. Logic that touches a third party or
  coordinates several models lives in `core/services/`.
- **One dispatch point per provider family.** `LLMService` in `core/services/llm_service.py`
  picks the provider class for a model; `get_vector_service()` in
  `core/services/vector_service.py` picks the vector store:

  ```
  LLMService → AIService implementation
      ├── OpenAIService
      ├── ClaudeService
      ├── GeminiService
      ├── LlamaService       (Ollama)
      └── CustomLLMService   (custom endpoints and LiteLLM proxies)

  get_vector_service() → BaseVectorService implementation
      ├── WeaviateVectorService
      └── PineconeVectorService
  ```

- **Chat is a bounded tool loop.** A turn streams, calls tools, reads their results and streams
  again. See [tool-loop.md](tool-loop.md).
- **Soft deletes and filtered managers.** `BaseModel` adds `is_active` and `is_deleted`. Most
  models declare only `active_objects`, which hides retired rows, and there is no `objects`
  manager on them; reverse relations filter the same way. Use `_base_manager` to see every row.
- **Slow work goes to RQ.** File ingestion, embeddings and memory writes run in workers so the
  web process stays responsive. Each job must be safe to retry (`rules.md`, section 7).
- **camelCase at the boundary.** REST and socket payloads are camelCase; Python stays snake_case.
  See [serialization.md](../reference/serialization.md).

## Real-time chat

```mermaid
sequenceDiagram
    participant FE as Frontend
    participant SIO as Socket.IO server
    participant Redis as Redis pub/sub
    participant MC as Message coordinator
    participant LLM as LLM provider

    FE->>SIO: connect (JWT auth)
    FE->>SIO: subscribe_conversation(id)
    SIO->>FE: conversation_history

    FE->>SIO: send_message(data)
    SIO->>MC: process message
    MC->>LLM: stream_chat_completion()
    loop Streaming chunks
        LLM-->>MC: chunk
        MC-->>Redis: publish event
        Redis-->>SIO: forward to room
        SIO-->>FE: message (type: ai_stream)
    end
    MC-->>SIO: message (type: message, complete)
```

Two namespaces:
- `/chat`: conversations, messages, artifacts, voice input.
- `/workflow`: workflow execution, step streaming, batch processing, human validation.

## Key relationships

```mermaid
erDiagram
    User ||--o{ Conversation : owns
    User ||--o{ File : uploads
    User ||--o{ Workflow : creates
    User ||--o{ Agent : creates
    User }o--o| ModelGroup : "via access code group"

    Conversation ||--o{ Message : contains
    Message ||--o{ Snippet : "RAG context"
    Message ||--o{ WebSearchSource : sources

    Workflow ||--o{ WorkflowNode : contains
    Workflow ||--o{ WorkflowRun : executes

    File }o--o{ Tag : tagged_with
    Folder }o--o{ File : contains

    ModelGroup }o--o{ LLM : "grants access"
```

## Further reading

- [data-flows.md](data-flows.md): chat, file upload, workflow, Socratic Bots auth and artifact
  sequences.
- [document-ingestion.md](document-ingestion.md): how an upload becomes a verified search index.
- [tool-loop.md](tool-loop.md): tool calling across providers.
- [REST API](../reference/rest-api.md) and [Socket.IO events](../reference/socketio-events.md).
