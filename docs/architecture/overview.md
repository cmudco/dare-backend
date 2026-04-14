# Architecture Overview

## System Architecture

The DARE platform is a monorepo with four projects that work together:

```mermaid
graph TB
    subgraph "Frontends"
        DF["dare-frontend<br/>(React 18 / Redux / Vite)<br/>Port 5173"]
        SR["socraticbooks-react<br/>(React 19 / Zustand / Vite)"]
    end

    subgraph "Backends"
        DB["dare-backend<br/>(Django REST + Socket.IO)<br/>Port 8000"]
        SB["socraticbooks-backend<br/>(Django REST)<br/>Port 8001"]
    end

    subgraph "Infrastructure"
        Redis["Redis<br/>(Socket.IO pub/sub + RQ queues)"]
        PG["PostgreSQL / SQLite"]
        VDB["Vector DB<br/>(Pinecone / Weaviate)"]
    end

    subgraph "External Services"
        LLM["LLM Providers<br/>(OpenAI, Anthropic,<br/>Google, Ollama)"]
        EL["ElevenLabs<br/>(Voice)"]
    end

    DF -- "REST API + Socket.IO" --> DB
    SR -- "REST API only" --> SB
    SB -- "HTTP Proxy<br/>(JWT + X-Internal-Key)" --> DB

    DB --> Redis
    DB --> PG
    DB --> VDB
    DB --> LLM
    SB --> PG
    SB --> EL
```

## Component Responsibilities

### dare-backend (Primary Backend)

The central service that handles all AI, auth, and data operations:

- **Authentication**: JWT-based auth via `dj-rest-auth` + `allauth`
- **Conversations**: Real-time chat streaming via Socket.IO (`/chat` namespace)
- **Workflows**: Visual DAG execution engine via Socket.IO (`/workflow` namespace)
- **File Processing**: Upload, chunk, embed, and index files for RAG
- **LLM Integration**: Multi-provider support (OpenAI, Anthropic, Google, Ollama) via `AIService` ABC
- **Vector Search**: Similarity search against Pinecone or Weaviate
- **Background Tasks**: File processing, embedding generation via Django RQ
- **MCP**: Model Context Protocol server integration
- **Billing**: Token usage tracking and cost calculation

**Key architectural choices:**
- Socket.IO over Django Channels for real-time (python-socketio, not channels consumers)
- ASGI via `uvicorn` (required for Socket.IO)
- Service layer with ABCs and factory functions for extensibility
- Redis-backed Socket.IO manager for multi-process pub/sub

### dare-frontend (Primary Frontend)

React SPA that connects to dare-backend via REST and Socket.IO:

- **Redux Toolkit** for all state management
- **Two Socket.IO middlewares**: one for `/chat`, one for `/workflow`
- **Zod schemas** validate all incoming workflow socket events
- **Shadcn/ui** (Radix UI) component library with Tailwind CSS
- **Formik + Yup** for form validation

### socraticbooks-backend (Educational Proxy)

Thin Django layer focused on educational features, delegates core operations to dare-backend:

- **Proxy pattern**: `DareApiClient` forwards auth, conversations, files, and model requests to dare-backend
- **Two auth modes**: JWT (user-authenticated) and `X-Internal-Key` (service-to-service)
- **Educational models**: Book, Chapter, Note, BotGroup, AccessCode
- **Voice features**: ElevenLabs integration for voice-based learning

### socraticbooks-react (Educational Frontend)

React SPA for the Socratic Books platform:

- **Zustand** for state management (not Redux)
- **TanStack Query** for server state and caching
- **React Hook Form + Zod** for form validation
- Connects exclusively to socraticbooks-backend (no direct DARE connection)

## Real-Time Architecture

The platform uses **python-socketio** (not Django Channels) for all real-time communication:

```mermaid
sequenceDiagram
    participant FE as dare-frontend
    participant SIO as Socket.IO Server
    participant Redis as Redis Pub/Sub
    participant MC as MessageCoordinator
    participant LLM as LLM Provider

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

**Two namespaces:**
- `/chat` — Conversations, messages, artifacts, voice input
- `/workflow` — Workflow execution, step streaming, batch processing, human validation

See [Socket.IO Event Contract](socketio-events.md) for the complete event reference.

## Data Flow: File Upload & RAG

```mermaid
sequenceDiagram
    participant FE as Frontend
    participant API as REST API
    participant RQ as RQ Worker
    participant VDB as Vector DB

    FE->>API: POST /api/files/ (multipart)
    API->>API: Save file, set status=PROCESSING
    API->>RQ: Enqueue processing job
    API-->>FE: 201 Created (file metadata)

    RQ->>RQ: Extract text from file
    RQ->>RQ: Chunk text (DocumentProcessor)
    RQ->>RQ: Generate embeddings (EmbeddingService)
    RQ->>VDB: Store vectors
    RQ->>RQ: Set status=COMPLETED

    FE->>API: GET /api/files/job-statuses/
    API-->>FE: File statuses (poll until COMPLETED)
```

## Data Flow: Workflow Execution

```mermaid
sequenceDiagram
    participant FE as Frontend
    participant WS as /workflow namespace
    participant WC as WorkflowCoordinator
    participant EX as LiveExecutor
    participant NH as NodeHandler
    participant LLM as LLM Provider

    FE->>WS: start_execution(workflowId)
    WS->>WC: execute()
    WC->>EX: run(workflow, run)

    loop For each node (topological order)
        EX->>NH: execute(node)
        EX-->>FE: workflow_event (step_started)
        NH->>LLM: stream_chat_completion()
        loop Streaming
            LLM-->>NH: chunk
            NH-->>FE: workflow_event (step_streaming)
        end
        NH-->>FE: workflow_event (step_completed)
    end

    EX-->>FE: workflow_event (execution_complete)
```

## Service Layer

All LLM calls go through an abstract service layer:

```
get_ai_service(provider) → AIService implementation
    ├── OpenAIService      (GPT-4, GPT-4o, etc.)
    ├── ClaudeService      (Claude 3.5, Claude 3, etc.)
    ├── GeminiService      (Gemini Pro, etc.)
    ├── LlamaService       (Ollama local models)
    └── CustomLLMService   (Custom endpoints)
```

Vector database access follows the same pattern:

```
get_vector_service(provider) → BaseVectorService implementation
    ├── PineconeVectorService
    └── WeaviateVectorService
```

## Database Models (Key Relationships)

```mermaid
erDiagram
    User ||--o{ Conversation : owns
    User ||--o{ UserFile : uploads
    User ||--o{ Workflow : creates
    User ||--o{ Agent : creates
    User }o--o{ ModelGroup : "has access to"

    Conversation ||--o{ Message : contains
    Message ||--o{ Snippet : "has RAG"
    Message ||--o{ WebSearchSource : "has sources"

    Workflow ||--o{ WorkflowNode : contains
    Workflow ||--o{ WorkflowRun : executes

    UserFile ||--o{ Tag : tagged_with
    UserFile }o--o| Folder : "in folder"

    ModelGroup ||--o{ LLMModel : "grants access"
```

## Inter-Service Communication

See [SocraticBooks-DARE Proxy Contract](../integration/socraticbooks-dare-proxy.md) for the complete reference.

```mermaid
graph LR
    SB["socraticbooks-backend"]

    subgraph "DARE Backend Endpoints"
        AUTH["/users/api/dj-rest-auth/*"]
        CONV["/api/conversations/*"]
        FILES["/api/files/*"]
        MODELS["/api/llms/*"]
        NOTIF["/api/notifications/*"]
        INTERNAL["/api/internal/*"]
    end

    SB -- "Bearer JWT" --> AUTH
    SB -- "Bearer JWT" --> CONV
    SB -- "Bearer JWT" --> FILES
    SB -- "Bearer JWT" --> MODELS
    SB -- "Bearer JWT" --> NOTIF
    SB -- "X-Internal-Key" --> INTERNAL
```
