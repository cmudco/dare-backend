# Data Flows

Key user flows through the DARE platform with detailed step-by-step breakdowns.

## 1. Chat Message Flow

When a user sends a message in a conversation:

```mermaid
sequenceDiagram
    participant User
    participant FE as dare-frontend
    participant SM as socketMiddleware
    participant SIO as Socket.IO (/chat)
    participant CN as ChatNamespace
    participant MC as MessageCoordinator
    participant AI as AIService
    participant LLM as LLM Provider
    participant DB as Database

    User->>FE: Type message, click Send
    FE->>SM: dispatch(SOCKET_SEND_MESSAGE)
    SM->>SIO: emit('send_message', data)
    SIO->>CN: on_send_message(sid, data)
    CN->>MC: process_message()

    MC->>DB: Save user message
    MC->>DB: Fetch conversation context
    MC->>AI: get_ai_service(provider)
    MC->>AI: stream_chat_completion(messages)

    loop Streaming chunks
        AI->>LLM: API call (streaming)
        LLM-->>AI: chunk
        AI-->>MC: yield chunk
        MC-->>SIO: emit('message', {type: 'ai_stream', chunk})
        SIO-->>SM: receive event
        SM-->>FE: dispatch(updateStreamingMessage)
        FE-->>User: Render streaming text
    end

    MC->>DB: Save assistant message + token usage
    MC-->>SIO: emit('message', {type: 'message', complete})
    SIO-->>FE: Final message with metadata
```

**Key services involved:**
- `conversations/namespaces/chat.py` — Socket.IO event handler
- `conversations/services/message_coordinator.py` — Orchestrates the full flow
- `conversations/services/websocket_response_service.py` — Formats and emits events
- `core/services/llm_service.py` → provider implementation — LLM streaming

## 2. File Upload & RAG Processing

When a user uploads a file for use as conversation context:

```mermaid
sequenceDiagram
    participant User
    participant FE as dare-frontend
    participant API as REST API
    participant DB as Database
    participant RQ as RQ Worker
    participant DP as DocumentProcessor
    participant ES as EmbeddingService
    participant VDB as Vector DB

    User->>FE: Drag & drop file
    FE->>API: POST /api/files/ (multipart)
    API->>DB: Create UserFile (status=PROCESSING)
    API->>RQ: Enqueue process_file_embeddings job
    API-->>FE: 201 Created (file metadata)
    FE-->>User: Show "Processing..." status

    Note over RQ: Background processing begins
    RQ->>DB: Load file content
    RQ->>DP: extract_text(file)
    DP-->>RQ: Raw text
    RQ->>DP: chunk_text(text, chunk_size, overlap)
    DP-->>RQ: Text chunks[]

    loop For each chunk
        RQ->>ES: generate_embedding(chunk)
        ES-->>RQ: Vector embedding
        RQ->>VDB: upsert(embedding, metadata)
    end

    RQ->>DB: Update UserFile (status=COMPLETED)

    FE->>API: POST /api/files/job-statuses/ (polling)
    API-->>FE: {status: COMPLETED}
    FE-->>User: Show "Ready" status
```

**Key services involved:**
- `files/api/views.py` — Upload endpoint
- `core/services/file_processor.py` — Background processing pipeline
- `core/services/document_processor.py` — Text extraction and chunking
- `core/services/embedding_service.py` — Vector embedding generation
- `core/services/vector_service.py` — Pinecone/Weaviate storage

## 3. Workflow Execution

When a user runs a multi-step workflow:

```mermaid
sequenceDiagram
    participant User
    participant FE as dare-frontend
    participant WM as workflowSocketMiddleware
    participant SIO as Socket.IO (/workflow)
    participant WN as WorkflowNamespace
    participant WC as WorkflowCoordinator
    participant EX as LiveExecutor
    participant RO as RunOrdering (DAG)
    participant NH as NodeHandler
    participant LLM as LLM Provider
    participant DB as Database

    User->>FE: Click "Run Workflow"
    FE->>WM: dispatch(startExecution)
    WM->>SIO: emit('start_execution', {workflowId})
    SIO->>WN: on_start_execution(sid, data)
    WN->>WC: execute(workflow)
    WC->>DB: Create WorkflowRun (status=running)
    WC->>EX: run(workflow, run)

    EX->>RO: get_topological_order(nodes, edges)
    RO-->>EX: Ordered node list

    loop For each node in order
        EX->>NH: Registry.get_handler(node_type)
        EX->>NH: execute(node, context)

        NH-->>SIO: emit('workflow_event', {type: 'step_started'})
        SIO-->>FE: Update node status (running)

        alt LLM Step Node
            NH->>LLM: stream_chat_completion()
            loop Streaming
                LLM-->>NH: chunk
                NH-->>SIO: emit('workflow_event', {type: 'step_streaming'})
                SIO-->>FE: Render streaming output
            end
        else Routing Node (Human Validation)
            NH-->>SIO: emit('workflow_event', {type: 'validation_required'})
            SIO-->>FE: Show validation UI
            User->>FE: Select route
            FE->>SIO: emit('submit_validation', {selectedRoute})
            SIO->>NH: Resume with selected route
        end

        NH-->>SIO: emit('workflow_event', {type: 'step_completed'})
        NH->>DB: Save step result + tokens
        SIO-->>FE: Update node status (completed)
    end

    EX->>DB: Update WorkflowRun (status=completed)
    EX-->>SIO: emit('workflow_event', {type: 'execution_complete'})
    SIO-->>FE: Show completion summary
```

**Key services involved:**
- `conversations/namespaces/workflow.py` — Socket.IO event handler
- `workflows/services/workflow_coordinator.py` — Entry point facade
- `workflows/services/live_executor.py` — Full execution engine
- `workflows/services/single_step_executor.py` — Manual mode (one node at a time)
- `workflows/services/batch_executor.py` — Batch file execution
- `workflows/services/run_ordering.py` — DAG topological sort
- `workflows/handlers/registry.py` — Node handler lookup by type

## 4. SocraticBooks Authentication Flow

When a user logs in through the Socratic Books platform:

```mermaid
sequenceDiagram
    participant User
    participant SR as socraticbooks-react
    participant SB as socraticbooks-backend
    participant DAC as DareAuthClient
    participant DB as DARE Backend

    User->>SR: Enter email + password
    SR->>SB: POST /api/auth/login/
    SB->>DAC: authenticate_user(email, password)
    DAC->>DB: POST /users/api/dj-rest-auth/login/<br/>(Headers: Origin=SB, Referer=SB)
    DB-->>DAC: {access, refresh, user}
    DAC-->>SB: DARE JWT tokens + user data

    SB->>SB: Find/create local User<br/>(link via external_user_id)
    SB->>SB: Add platform-specific data
    SB-->>SR: {access, refresh, user, platform: 'SocraticBots'}
    SR->>SR: Store JWT in memory
    SR-->>User: Redirect to dashboard
```

**Two auth modes exist:**
- **Bearer JWT** — For user-authenticated requests (SB passes the DARE-issued JWT)
- **X-Internal-Key** — For service-to-service calls without user context (file uploads from webhooks, role management)

## 5. Artifact Generation Flow

When the AI generates a long-form artifact (document, code, etc.):

```mermaid
sequenceDiagram
    participant User
    participant FE as dare-frontend
    participant SIO as Socket.IO (/chat)
    participant MC as MessageCoordinator
    participant ATE as ArtifactToolExecutor
    participant LLM as LLM Provider

    User->>FE: Send message triggering artifact
    FE->>SIO: send_message(data)
    SIO->>MC: process_message()

    MC->>LLM: stream_chat_completion()
    LLM-->>MC: Tool call: create_artifact
    MC->>ATE: execute(artifact_params)

    ATE-->>SIO: emit({type: 'artifact_init', title, outline})
    SIO-->>FE: Open artifact sidecar panel

    loop For each section
        ATE->>LLM: Generate section content
        loop Streaming
            LLM-->>ATE: chunk
            ATE-->>SIO: emit({type: 'artifact_stream', chunk, progress})
            SIO-->>FE: Render content progressively
        end
    end

    Note over FE: User can pause/continue
    User->>FE: Click Pause
    FE->>SIO: pause_artifact(artifactId)
    SIO-->>FE: emit({type: 'artifact_pause'})

    User->>FE: Click Continue
    FE->>SIO: continue_artifact(artifactId)
    ATE-->>SIO: Resume streaming remaining sections

    ATE-->>SIO: emit({type: 'artifact_complete'})
    SIO-->>FE: Show completed artifact
```
