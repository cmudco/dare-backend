# Socket.IO Event Contract

Complete reference for all real-time events in the DARE platform. The backend uses **python-socketio** with Redis pub/sub. All payload keys are **camelCase** (backend applies `camelize()` before emission). Event names are **snake_case**.

## Authentication

Both namespaces authenticate via the `auth` handshake option:

```typescript
// Standard JWT auth
socket = io('/chat', { auth: { token: 'jwt_access_token' } })

// Session-based auth (public bots only, /chat namespace)
socket = io('/chat', { auth: { sessionId: 'uuid', conversationId: 'uuid' } })
```

---

## /chat Namespace

### Client → Server Events

#### `subscribe_conversation`

Subscribe to real-time updates for a conversation. Returns conversation history via callback.

| Field | Type | Required | Description |
|---|---|---|---|
| `conversationId` | `string` | Yes | Conversation UUID |
| `platform` | `string` | No | Platform identifier |

**Callback response:** `{ success: boolean, error?: string, conversationId?: string }`

After subscribing, the server emits a `message` event with `type: 'conversation_history'` containing full message history.

---

#### `unsubscribe_conversation`

Leave a conversation room. Stops receiving updates.

| Field | Type | Required | Description |
|---|---|---|---|
| `conversationId` | `string` | Yes | Conversation UUID |

**Callback response:** `{ success: boolean, error?: string }`

---

#### `send_message`

Send a chat message. Triggers LLM streaming response.

| Field | Type | Required | Description |
|---|---|---|---|
| `conversationId` | `string` | Yes | Conversation UUID |
| `message` | `string` | Yes | User message text |
| `active_artifact_id` | `number` | No | Currently open artifact (for intent detection) |
| *(additional fields)* | `any` | No | Model config, file IDs, etc. |

**Callback response:** `{ success: boolean, error?: string }`

---

#### `send_voice_message`

Send an audio recording for transcription + LLM response.

| Field | Type | Required | Description |
|---|---|---|---|
| `conversationId` | `string` | Yes | Conversation UUID |
| `audio` | `string` | Yes | Base64-encoded audio data |
| `audioFormat` | `string` | Yes | Audio format (e.g., `'webm'`) |
| `language` | `string` | No | Language code (default: `'auto'`) |

**Callback response:** `{ success: boolean, error?: string, text?: string }`

---

#### `edit_message`

Edit a previously sent message. Triggers re-generation of AI response.

| Field | Type | Required | Description |
|---|---|---|---|
| `conversationId` | `string` | Yes | Conversation UUID |
| `messageId` | `string` | Yes | Message ID to edit |
| `message` | `string` | Yes | New message content |

**Callback response:** `{ success: boolean, error?: string }`

---

#### `regenerate_response`

Regenerate an AI response for a specific message.

| Field | Type | Required | Description |
|---|---|---|---|
| `conversationId` | `string` | Yes | Conversation UUID |
| `messageId` | `string` | Yes | Message ID to regenerate |
| `message_id` | `string` | Yes | Same as messageId (legacy compat) |

**Callback response:** `{ success: boolean, error?: string }`

---

#### `continue_artifact`

Resume generation of a paused artifact.

| Field | Type | Required | Description |
|---|---|---|---|
| `conversationId` | `string` | Yes | Conversation UUID |
| `artifactId` | `number` | Yes | Artifact ID to continue |

**Callback response:** `{ success: boolean, error?: string }`

---

#### `pause_artifact`

Pause an in-progress artifact generation.

| Field | Type | Required | Description |
|---|---|---|---|
| `conversationId` | `string` | Yes | Conversation UUID |
| `artifactId` | `number` | Yes | Artifact ID to pause |

**Callback response:** `{ success: boolean, error?: string }`

---

### Server → Client Events

All server-to-client events in the `/chat` namespace are sent as a single `message` event with a `type` field that discriminates the payload shape.

#### `message` (type: `conversation_history`)

Sent immediately after `subscribe_conversation`. Contains full message history.

```typescript
{
  type: 'conversation_history',
  conversationHistory: Message[]
}
```

---

#### `message` (type: `message`)

A complete message (user or assistant). Sent when a message is fully processed.

```typescript
{
  type: 'message',
  id: number,
  message: string,
  senderType: string,        // 'human' | 'ai'
  senderName: string,
  artifactId?: string,
  streaming: boolean,
  regenerate: boolean,
  createdAt: string,          // ISO 8601
  llm?: number,              // LLM model ID
  files: File[],
  tags: Tag[],
  snippets: Snippet[],
  webSearchSources: WebSearchSource[],
  feedbackType?: string,
  feedbackText?: string,
  isEdited: boolean,
  isRegenerated: boolean,
  originalMessage?: string,
  cost?: string,
  inputTokens?: number,
  outputTokens?: number,
  energyWh?: number,
  carbonG?: number,
  waterMl?: number,
  generatedImage?: object,
  generatedTranscription?: object,
  learningProgressData?: object
}
```

---

#### `message` (type: `ai_stream`)

Streaming chunks during AI response generation.

```typescript
{
  type: 'ai_stream',
  id: number,
  message: string,           // Accumulated text so far
  isComplete: boolean,
  senderName: string,
  senderType: string
}
```

---

#### `message` (type: `artifact_init`)

Artifact generation has started.

```typescript
{
  type: 'artifact_init',
  artifactId: string,
  title: string,
  outline: string,
  estimatedSections: number
}
```

---

#### `message` (type: `artifact_stream`)

Streaming artifact content.

```typescript
{
  type: 'artifact_stream',
  artifactId: string,
  chunk: string,
  section: number,
  progress: number           // 0.0 to 1.0
}
```

---

#### `message` (type: `artifact_pause`)

Artifact generation paused (user-initiated or section boundary).

```typescript
{
  type: 'artifact_pause',
  artifactId: string,
  currentSection: number,
  sectionsRemaining: number
}
```

---

#### `message` (type: `artifact_complete`)

Artifact generation finished.

```typescript
{
  type: 'artifact_complete',
  artifactId: string,
  totalWords: number,
  estimatedSections: number
}
```

---

#### `message` (type: `voice_processing`)

Voice message is being transcribed.

```typescript
{ type: 'voice_processing', status: 'transcribing' }
```

---

#### `message` (type: `voice_transcription`)

Voice transcription result.

```typescript
{
  type: 'voice_transcription',
  status: 'complete' | 'error',
  text?: string,
  error?: string
}
```

---

#### `message` (type: `conversation_title`)

Auto-generated conversation title.

```typescript
{ type: 'conversation_title', title: string }
```

---

#### `message` (type: `edit_message`)

Confirmation that a message was edited.

```typescript
{
  type: 'edit_message',
  id: number,
  message: string,
  isEdited: boolean
}
```

---

#### `message` (type: `progress_stream` / `progress_complete` / `progress_error`)

Learning progress assessment streaming (Socratic Books feature).

```typescript
// Streaming
{ type: 'progress_stream', conversationId: string, messageId: string, chunk: string }

// Complete
{ type: 'progress_complete', conversationId: string, messageId: string, inputTokens?: number, outputTokens?: number }

// Error
{ type: 'progress_error', message: string }
```

---

#### `message` (type: `error`)

Error during message processing.

```typescript
{
  type: 'error',
  errorCode: string,
  errorMessage: string,
  details?: object
}
```

---

## /workflow Namespace

### Client → Server Events

#### `subscribe_workflow`

Subscribe to a workflow's execution state. Returns latest run and batch status.

| Field | Type | Required | Description |
|---|---|---|---|
| `workflowId` | `number` | Yes | Workflow ID |

**Callback response:**
```typescript
{
  success: boolean,
  workflowId?: number,
  latestRun?: WorkflowRunState | null,
  latestBatchRun?: BatchRunState | null,
  error?: string
}
```

---

#### `subscribe_workflow_run`

Subscribe to a specific workflow run.

| Field | Type | Required | Description |
|---|---|---|---|
| `workflowRunId` | `number` | Yes | Workflow run ID |

**Callback response:** `{ success: boolean, error?: string, workflowRunId?: number }`

---

#### `unsubscribe_workflow_run`

Leave a workflow run room.

| Field | Type | Required | Description |
|---|---|---|---|
| `workflowRunId` | `number` | Yes | Workflow run ID |

**Callback response:** `{ success: boolean, error?: string }`

---

#### `start_execution`

Start or resume a workflow execution.

| Field | Type | Required | Description |
|---|---|---|---|
| `workflowRunId` | `number` | No | Resume existing run |
| `workflowId` | `number` | No | Start new run for workflow |
| `userInput` | `string` | No | User input for start node |

**Callback response:** `{ success: boolean, error?: string, workflowRunId?: number }`

---

#### `execute_single_step`

Execute a single node in manual mode.

| Field | Type | Required | Description |
|---|---|---|---|
| `workflowId` | `number` | Yes | Workflow ID |
| `stepNodeId` | `string` | Yes | Node ID to execute |
| `workflowRunId` | `number` | No | Existing run to continue |

**Callback response:** `{ success: boolean, error?: string, workflowRunId?: number }`

---

#### `submit_validation`

Submit a human validation decision at a routing node.

| Field | Type | Required | Description |
|---|---|---|---|
| `workflowRunId` | `number` | Yes | Workflow run ID |
| `nodeId` | `string` | Yes | Routing node ID |
| `selectedRoute` | `string` | Yes | Chosen route name |
| `continueExecution` | `boolean` | No | Continue after validation (default: `true`) |

**Callback response:** `{ success: boolean, error?: string }`

---

#### `start_batch_execution`

Execute a workflow against multiple files.

| Field | Type | Required | Description |
|---|---|---|---|
| `workflowId` | `number` | Yes | Workflow ID |
| `fileIds` | `number[]` | Yes | File IDs to process |

**Callback response:** `{ success: boolean, error?: string, batchId?: number }`

---

### Server → Client Events

#### `workflow_event` (type: `step_started`)

A node has begun execution.

```typescript
{
  type: 'step_started',
  nodeId: string,
  label?: string,
  nodeType: string,
  startedAt?: string,
  workflowRunId?: number
}
```

---

#### `workflow_event` (type: `step_streaming`)

Streaming LLM output from a step node.

```typescript
{
  type: 'step_streaming',
  nodeId: string,
  chunk: string,
  accumulatedTokens?: number,
  workflowRunId?: number
}
```

---

#### `workflow_event` (type: `step_completed`)

A node has finished execution.

```typescript
{
  type: 'step_completed',
  nodeId: string,
  response: string,
  status: 'completed' | 'failed' | 'skipped',
  tokens?: { input: number, output: number },
  metadata?: {
    snippets?: Snippet[],
    webSearchSources?: WebSearchSource[]
  },
  workflowRunId?: number
}
```

---

#### `workflow_event` (type: `step_error`)

A node encountered an error.

```typescript
{
  type: 'step_error',
  error: string,
  nodeId?: string,
  errorType?: string,
  workflowRunId?: number
}
```

---

#### `workflow_event` (type: `execution_complete`)

The entire workflow run has finished.

```typescript
{
  type: 'execution_complete',
  workflowRunId: number,
  status: 'completed' | 'failed' | 'pending_human_input',
  totalCost?: number,
  totalTokens?: { input: number, output: number },
  endedAt?: string
}
```

---

#### `workflow_event` (type: `validation_required`)

A routing node requires human input.

```typescript
{
  type: 'validation_required',
  nodeId: string,
  routes: Array<{ name: string, description?: string }>,
  context?: { aiAnalysis?: string },
  aiRecommendation?: string,
  workflowRunId?: number
}
```

---

#### `workflow_event` (type: `batch_started`)

Batch execution has begun.

```typescript
{
  type: 'batch_started',
  batchId: number,
  totalFiles: number,
  workflowId: number
}
```

---

#### `workflow_event` (type: `batch_progress`)

Progress update for a file in the batch.

```typescript
{
  type: 'batch_progress',
  batchId: number,
  index: number,
  total: number,
  fileId: number,
  fileName: string,
  status: 'running' | 'completed' | 'failed',
  workflowRunId?: number
}
```

---

#### `workflow_event` (type: `batch_complete`)

Batch execution finished.

```typescript
{
  type: 'batch_complete',
  batchId: number,
  completedCount: number,
  failedCount: number,
  totalFiles: number
}
```

---

#### `workflow_status`

Full snapshot of a workflow run state. Sent on subscribe and after significant state changes.

```typescript
{
  type: 'workflow_status',
  id: number,
  status: string,
  startedAt?: string,
  endedAt?: string,
  workflowTitle?: string,
  workflowDescription?: string,
  isPartial?: boolean,
  nodeStates?: Record<string, NodeState>,
  pendingValidation?: {
    nodeId: string,
    routes: Array<{ name: string, description?: string }>,
    aiRecommendation?: string,
    context?: { aiAnalysis?: string }
  }
}
```

---

## Shared Data Types

### Snippet

```typescript
{
  id: number,
  file?: { id: number, name: string },
  text: string,
  similarityScore: number,
  chunkIndex: number,
  vectorDbSource?: string
}
```

### WebSearchSource

```typescript
{
  id: number,
  url: string,
  title: string,
  citedText: string,
  pageAge?: string,
  provider: string
}
```

### NodeState

```typescript
{
  stepId?: number,
  startedAt?: string,
  nodeType: string,
  status: string,
  response?: string,
  error?: string,
  validationContext?: unknown,
  metadata?: unknown,
  snippets: Snippet[],
  webSearchSources: WebSearchSource[]
}
```

---

## Implementation Notes

- **Event names** are snake_case; **payload keys** are camelCase
- Backend applies `camelize()` before all emissions
- Frontend validates all `/workflow` events with **Zod schemas** in `src/schemas/workflowSocket.ts`
- All client-to-server events use a **callback pattern**: `socket.emit(event, data, callback)`
- Callbacks always return `{ success: boolean, error?: string }` plus optional extra fields
- Automatic re-subscription on reconnect via stored subscription sets in both middlewares
