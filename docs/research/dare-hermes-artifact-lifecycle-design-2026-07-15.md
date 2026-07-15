# DARE–Hermes research/artifact run lifecycle

Status: recommended architecture with the first execution-control boundary now
implemented. This design is grounded in
`dare-hermes-artifact-lifecycle-evidence-2026-07-15.md`,
`dare_hermes_artifact_replay_v1.json`, DARE revision
`7c97a617c1f9697aed39699d2fc56f04e3ffc9e3`, and the inspected local Hermes
checkout at `06268f11cc6c9d9c140cab9d669e9136acc8fbd0` (including its existing
uncommitted local changes). The lifecycle evidence below is deliberately kept
as the pre-change snapshot that motivated the design; current deviations are
listed immediately before it.

## Decision summary

DARE must not write `failed` merely because its wall-clock deadline expired.
It should atomically move the active attempt to
`timed_out_pending_reconciliation`, record the deadline observation, request
cancellation, and reconcile against Hermes.  If Hermes cannot provide durable
terminal evidence, the attempt becomes `outcome_unknown`; that is an honest
state, not a failure.

The architecture separates three things currently collapsed into one row:

1. A **logical run** is the user's durable product intent and result lineage.
2. An **execution attempt** is one fenced claim to execute that intent.  Retry
   and continuation create a new attempt number and fencing token.
3. A **Hermes session** is the transcript/context namespace for one attempt.
   One attempt may contain multiple recorded Hermes executions (primary,
   format repair, or finalization), each with its own Hermes `run_id`.

Hermes owns execution truth: what actually ran, emitted, consumed, completed,
failed, or was cancelled.  DARE owns product truth: whether an execution met
the acceptance deadline and policy, whether its output validates, which attempt
is current, and which artifacts are published.  Redis and workers own no truth;
they transport and lease work.

## Implemented deviations from the snapshot

As of the first execution-control slice:

- DARE's 18-tool/eight-minute counter, automatic stop, and budget-finalization
  execution have been removed.
- Ambiguous SSE termination is verified with `GET /v1/runs/{id}`. Confirmed
  completion recovers terminal output; confirmed failure is failed; confirmed
  cancellation is preserved; active states remain active; unavailable truth is
  persisted as `outcome_unknown`.
- Automatic JSON repair re-asks have been removed. Malformed output is not
  silently converted into a second, untracked Hermes execution.

Full scheduled reconciliation, restart recovery, attempt identity/fencing, and
frontend cancellation remain future stages.

## Verified pre-change lifecycle snapshot

Everything in this section describes the captured DARE revision before the
first execution-control slice. It is historical evidence, not current behavior.

### Exact DARE request

`POST /api/research/projects/{project_id}/artifact/` accepts `prompt` and either
`artifactType` or `artifact_type`.  DARE creates a `ResearchAgentRun` immediately
with `running / Queued…`, `role=presenter`, `mode=artifact`,
`allowed_tools=["skills"]`, `selected_context={"artifactType": ...}`, and
`started_at=now`, then enqueues `run_artifact_job(run.id)`.

The worker appends the project question and at most twelve approved knowledge
items, truncating each body to 300 characters.  It composes Presentation
Assistant instructions from the current soul plus the artifact JSON contract.
It calls Hermes with:

```http
POST /v1/runs
Authorization: Bearer <redacted>
Content-Type: application/json
X-Hermes-Session-Key: dare-proj{project_id}

{
  "input": "{task + project question + truncated approved knowledge}",
  "instructions": "{soul + Presentation Assistant artifact contract}",
  "session_id": "{artifact_session.hermes_session_id}-r{dare_run_id}"
}
```

No idempotency key, DARE attempt identifier, deadline, turn limit, tool limit,
response schema, or enforceable tool policy is sent.  The stored
`allowed_tools=["skills"]` is metadata only.  The local Hermes API-server
profile actually exposes `skills`, `todo`, `vision`, and `web`.

### Hermes creation and execution

Hermes validates the body, creates a random `run_<uuid>` and an in-memory queue,
sets an in-memory status `queued`, and returns HTTP 202 with `status=started`.
The canonical controlled fixture recorded a 202 body with `status=queued`; the
inspected local handler now returns `started` while its internal pollable state
begins as `queued`.  DARE reads only `run_id`, so this response-label drift does
not currently affect its behavior but belongs in the contract tests.
An asyncio task changes the status to `running`, creates a fresh `AIAgent`, and
runs `run_conversation` in an executor thread.  `session_id` scopes the persisted
SessionDB transcript; `X-Hermes-Session-Key` scopes longer-term memory and
approvals.  The local config resolves `agent.max_turns=40` into
`HERMES_MAX_ITERATIONS=40`.  Output-length truncation can cause up to two
automatic continuation turns using Hermes' system continuation message.

The `/v1/runs` registry, event queues, active agent/task references, status,
output, usage, and cancellation control are process-memory data.  Terminal
statuses are pollable for up to one hour only while the same process remains
alive.  A gateway restart loses them, although SessionDB messages remain.

### Events, messages, tools, usage, and terminal responses

Hermes emits `message.delta`, `tool.started`, `tool.completed`,
`reasoning.available`, `approval.request/responded`, and terminal
`run.completed`, `run.failed`, or `run.cancelled` events.  The event queue has no
durable sequence number or replay cursor.  A single SSE consumer drains it; on
disconnect the server removes the queue even if execution continues.

DARE accumulates only `message.delta` text in worker memory, remembers one last
tool preview, increments an in-memory counter on `tool.completed`, ignores
reasoning and unknown events, and stops reading on `run.completed`.  It does not
handle `run.failed` or `run.cancelled` as terminal events.  Artifact jobs do not
pass `on_tool`, so artifact tool calls are not persisted.  On successful parse,
DARE stores accepted artifacts; the raw response and SSE envelope are not stored.
It then GETs `/v1/runs/{id}` but copies only `usage`, ignoring status, output,
model, timestamps, and `last_event`.

### Pre-change enforcement and terminal handling

DARE starts a worker-local monotonic timer when SSE consumption begins.  It
checks the eight-minute and eighteen-tool budgets only when a parsed SSE event
arrives.  The tool test is effectively late: the nineteenth completion is
counted and the stop occurs on the next event.  A long silent tool or stalled
stream is governed by the HTTP client's 300-second read timeout, not the stated
eight-minute product deadline.  RQ itself allows 3,600 seconds.

On budget expiry DARE best-effort POSTs `/stop`, immediately records `failed`,
and may then perform a separate untracked Hermes finalization for Scout.  On
stream/network exceptions it immediately records `failed`.  Hermes `/stop`
returns 404 once active references have already been cleaned up.  For an active
run it sets `stopping`, calls `agent.interrupt`, cancels the asyncio wrapper, and
normally publishes `run.cancelled`; the executor thread cannot be preempted and
may still finish/persist session work.  The response `stopping` is an
acknowledgement of intent, not a terminal cancellation result.

### Pre-change database and frontend states

The DARE row knows only `started`, `queued`, `running`, `completed`, and `failed`
as conventional values; the database does not enforce them.  It has one
`hermes_run_id` even though repair/finalization can start additional Hermes
runs.  There is no attempt number, lease, deadline, cancellation timestamp,
reconciliation status, event cursor, terminal source, or fencing token.  The
frontend polls after 1.5 seconds and then every three seconds, stopping only on
`completed` or `failed`; polling errors are treated as settled.

## Observed races and failure behavior

| Scenario | Observed pre-change behavior | Required behavior |
|---|---|---|
| Duplicate API submission | Creates independent rows and Hermes work | Client request idempotency returns the same logical run |
| Redis unavailable after row insert | Enqueue raises; a `running / Queued…` row may remain | Transactional outbox remains pending and dispatch retries |
| Worker dies before dispatch | RQ may requeue/fail independently; DARE row stays running | Lease expiry reclaims the same attempt |
| Worker dies after Hermes 202 but before saving run ID | Duplicate dispatch is possible on retry | Hermes idempotency key recovers the same Hermes run |
| Worker dies/disconnects during SSE | DARE marks failed or remains running; Hermes may continue | Resume from event cursor or GET terminal; otherwise `outcome_unknown` |
| DARE deadline crosses between Hermes completion and cancel | DARE can record failed while Hermes completed | Reconcile committed terminal time/revision against `deadline_at` |
| Stop reaches a completed Hermes run | Hermes returns 404, indistinguishable from lost run | GET durable terminal by run/idempotency key; completed-before-cutoff wins |
| Stop accepted while executor thread finishes | In-memory status may be cancelled while session output appears later | Hermes exposes one monotonic durable terminal decision and cancellation ack |
| Hermes gateway restarts | All `/v1/runs` GET/event/stop data becomes 404 | Durable run/event registry survives restart |
| DARE restarts | No startup scan; rows can remain running forever | Reconciler claims all expired leases/nonterminal attempts |
| Redis restarts/loses jobs | Database and queue diverge | DB outbox is authoritative; Redis is rebuildable transport |
| DB write fails after terminal event | Hermes completed but DARE loses product result | Terminal replay and idempotent artifact insertion repair it |
| Old event arrives after retry | Nothing fences writes by attempt | Compare-and-swap active attempt + fencing token rejects stale writes |
| Repair/finalize starts another Hermes run | Child run ID, usage, output, and context boundary are untracked | Record every Hermes execution under its attempt |
| Historical runs 34–37 | DARE `failed`; Hermes completed later; no reconciliation | Preserve both observed truths and settle by the new policy |

## Proposed state model

The logical run exposes a product state.  Each attempt has a separate execution
state; DARE derives the product state only from the active fenced attempt.

### Product state-transition table

| State | Meaning | Allowed transitions | Writer / guard |
|---|---|---|---|
| `queued` | Intent and attempt exist; outbox not yet claimed | `dispatching`, `cancel_requested`, `failed` | Dispatcher lease; `failed` only for permanent pre-execution validation/config error |
| `dispatching` | Worker owns a renewable lease; Hermes identity not yet durably bound | `running`, `cancel_requested`, `outcome_unknown`, `failed` | Active attempt and fence must match |
| `running` | Hermes accepted the attempt and may execute | `cancel_requested`, `timed_out_pending_reconciliation`, `outcome_unknown`, `completed`, `failed` | Terminal evidence or policy event; never a bare network exception |
| `cancel_requested` | User/system cancellation intent recorded; result unsettled | `cancelled`, `completed`, `failed`, `outcome_unknown` | Reconciler uses Hermes terminal revision/time |
| `timed_out_pending_reconciliation` | Absolute DARE deadline expired; cancel requested; result unsettled | `timed_out`, `completed`, `failed`, `outcome_unknown` | Reconciler; completion is accepted only if Hermes committed it by the deadline |
| `outcome_unknown` | Execution may have happened but durable terminal evidence is unavailable | `completed`, `failed`, `cancelled`, `timed_out` | Reconciler with same-attempt evidence; manual resolution may append an audit decision |
| `completed` | Hermes completed by acceptance policy and DARE validated/persisted product output | none | Immutable terminal |
| `failed` | Hermes durably failed, or DARE permanently rejected the output/config after execution settled | none | Immutable terminal with `terminal_source` and reason |
| `cancelled` | Hermes durably committed cancellation before completion | none | Immutable terminal |
| `timed_out` | Deadline policy rejected the attempt after reconciliation | none | Immutable terminal; may coexist with `execution_outcome=completed_late` |

`superseded` is an attempt terminal state, not a logical-run state.  Creating a
retry or continuation increments `active_attempt_no` and `state_version`; every
write from the predecessor then fails its fence check and can only append audit
evidence to that predecessor.

### Attempt execution states

| State | Allowed transitions |
|---|---|
| `created` | `leased`, `superseded` |
| `leased` | `dispatching`, `created` (lease expired), `superseded` |
| `dispatching` | `accepted`, `outcome_unknown`, `failed`, `superseded` |
| `accepted` | `streaming`, `cancel_requested`, `outcome_unknown`, `completed`, `failed`, `superseded` |
| `streaming` | `cancel_requested`, `outcome_unknown`, `completed`, `failed`, `superseded` |
| `cancel_requested` | `cancelled`, `completed`, `failed`, `outcome_unknown`, `superseded` |
| `outcome_unknown` | `completed`, `failed`, `cancelled`, `timed_out`, `superseded` |
| `completed`, `failed`, `cancelled`, `timed_out`, `superseded` | none |

## Terminal ownership and race rules

1. Hermes is authoritative for **execution outcome**.  Its terminal record must
   be durable, monotonic, versioned, and queryable after restart.
2. DARE is authoritative for **product outcome**.  It compares Hermes'
   committed terminal timestamp with the persisted `deadline_at`, validates the
   artifact, and gates publication to the active attempt.
3. A cancellation request is not terminal.  `stopping` is not terminal.  Only a
   durable Hermes `cancelled` record (or an explicit operator resolution when
   Hermes truth is irretrievable) settles cancellation.
4. If Hermes committed `completed` immediately before the cancel request, the
   attempt completes.  A stop 404 is then harmless because reconciliation GET
   returns the completed record.
5. If Hermes commits `completed` after a cancellation request but before Hermes
   commits cancellation, completion wins as execution truth.  For a manual
   cancel, DARE accepts that completion.  For a deadline cancel, DARE accepts it
   only when `hermes_terminal_at <= deadline_at`; otherwise product outcome is
   `timed_out` and execution outcome is `completed_late`.
6. If Hermes commits `cancelled` first, a later completion event is stale or a
   Hermes invariant violation.  DARE retains it as audit evidence and does not
   change product truth without a newer durable Hermes terminal revision.
7. Reconciliation may settle only `cancel_requested`,
   `timed_out_pending_reconciliation`, or `outcome_unknown`.  It must never
   rewrite a confirmed `completed`, `failed`, `cancelled`, or `timed_out`
   terminal into another terminal.  Legacy local-timeout failures are migrated
   to an explicitly uncertain classification before reconciliation, or receive
   a side-car `observed_execution_outcome`; they are not silently rewritten.

## Idempotency and concurrency requirements

- The create endpoint accepts a client idempotency key, unique within user and
  project, and returns the existing logical run on replay.
- Dispatch uses a stable `attempt_id` as the Hermes idempotency key.  Repeating
  POST after a lost response must return the same Hermes run, request hash, and
  session identity; a different request hash is HTTP 409.
- The database transaction creates the logical run, attempt, and outbox row.
  Redis enqueue happens from the outbox after commit and is safely repeatable.
- Workers claim attempts with `SELECT ... FOR UPDATE SKIP LOCKED` or a compare-
  and-swap lease.  Every mutation includes `(logical_run_id, attempt_id,
  fence_token, expected_state_version)`.
- A retry/continuation transaction supersedes the predecessor, increments the
  attempt number and fence, and updates `active_attempt_id`.  Old workers and
  late events may append to the old attempt but cannot alter the logical run or
  publish artifacts.
- Events deduplicate by `(attempt_id, hermes_run_id, event_seq)`.  Tool calls
  additionally deduplicate by Hermes `tool_call_id`.  Terminal payloads
  deduplicate by `(hermes_run_id, terminal_revision)`.
- Artifact writes are transactional and unique by `(attempt_id, artifact_index)`
  or `(attempt_id, artifact_content_hash)`.  Product terminal publication and
  active-attempt verification occur in the same transaction.
- Reconciliation jobs are themselves leased/idempotent; repeated GET/replay
  produces no new artifact or terminal mutation.

## Durable DARE fields

### Logical run

- Globally unique `logical_run_id` plus legacy integer ID and environment ID.
- Project, user, mode, role, immutable user task, artifact type, create
  idempotency key, `retry_of` / `continuation_of`, and lineage root.
- Exact assembled-input snapshot and SHA-256; instruction snapshot and SHA-256;
  selected knowledge IDs/versions, soul version/content hash, and project
  question version.  Do not rely on re-reading mutable project rows later.
- Requested/effective tool-policy snapshot and hash.
- Budget-policy snapshot: absolute duration, tool completions, model turns,
  token/cost ceilings, and policy version.
- `accepted_at`, absolute UTC `deadline_at`, active attempt ID/number,
  `state_version`, product status, terminal reason/source/time, and publication
  state.
- Cancellation actor/reason/request time and continuation eligibility/reason.

### Execution attempt and Hermes executions

- Globally unique `attempt_id`, attempt number, fence token, status, retry class,
  created/leased/lease-expiry/worker-start/dispatch/accepted/terminal times,
  worker and RQ job identifiers.
- Hermes endpoint/profile identity, non-secret session scope identifier,
  `session_id`, dispatch idempotency key, request body hash, response status,
  primary `hermes_run_id`, and every child execution (`primary`, `repair`,
  `finalize`, `continuation`) with parent relation.
- Last event sequence/type/time, stream/reconcile cursors, heartbeat time,
  observed tool/turn counters, raw accumulated output (or encrypted object-store
  reference), output hash, parse/validation errors, model, complete usage and
  cost fields.
- Cancellation request/response/ack times and payloads; Hermes terminal status,
  revision, timestamp, output/hash, usage, last event, and raw terminal payload.
- Reconciliation attempts, next reconcile time, last error, final resolution,
  `superseded_at`, and the active fence observed by every writer.

Raw prompts, outputs, and tool arguments may contain sensitive data.  Apply a
retention policy, encryption, access controls, and redacted operator views; keep
hashes and structural audit metadata longer than bodies when required.

## Hermes reconciliation contract required

The current in-memory API is insufficient.  Hermes must durably provide:

- Idempotent create keyed by DARE `attempt_id`, with request-hash conflict
  detection and lookup by idempotency key.
- A durable run record surviving service restart: run ID, DARE attempt ID,
  session ID, status, monotonic terminal revision, UTC created/started/terminal
  times, model, exit reason, output and hash, full usage, and last event.
- An ordered, replayable event log with per-run monotonically increasing
  `event_seq`, retention guarantees, and `GET events?after=<seq>` semantics.
- Durable cancellation intent and acknowledgement, with a single monotonic
  terminal decision.  Stop on an already-terminal run should return that
  terminal record, not 404.
- Tool-call IDs, names, sanitized arguments or hashes, result/error metadata,
  durations, token contribution, turn number, and cumulative usage.
- A health/epoch identifier so DARE can detect a Hermes restart and reconcile
  instead of treating 404 as proof of failure.

Until that contract exists, DARE can improve honesty and fencing but cannot
guarantee recovery of execution truth after a Hermes restart.  Such attempts
must remain `outcome_unknown`, optionally enriched from retained SessionDB
messages, never guessed terminal.

## Deadline and budget recommendation

Use all limits, but give them distinct jobs:

- **Wall-clock deadline:** a durable absolute UTC `deadline_at`, computed when
  DARE accepts the run.  It is the product acceptance boundary and must be
  enforced by a scheduler/watchdog independent of SSE traffic and worker life.
- **Hermes turn limit:** a per-attempt model/API-call ceiling sent to and enforced
  by Hermes.  It bounds agent-loop amplification and must be returned in terminal
  usage.  The current local implicit value is 40; DARE should snapshot and send
  the chosen value instead of depending on profile config.
- **Tool-call limit:** sent to Hermes and enforced before starting the next tool,
  with tool-start and tool-complete counters.  DARE mirrors it for audit; it does
  not infer enforcement only from completed SSE events.
- **Token/cost limits:** optional hard resource caps at the executor/gateway,
  with explicit terminal reason.

The deadline does not mean the same thing as a failed model run, and a turn/tool
budget does not prove elapsed-time timeout.  Persist and display their reasons
separately.

## Continuation eligibility

A run is eligible for continuation only when its active attempt has a confirmed
terminal execution outcome, no cancellation/reconciliation is pending, the
attempt is still active (not superseded), and DARE has a durable validated
checkpoint or partial output from which to continue.  A continuation receives
a new attempt ID/number, fence, deadline, and budget while referencing the exact
predecessor output/hash.

`cancel_requested`, `timed_out_pending_reconciliation`, and `outcome_unknown`
are not continuation-eligible because the predecessor may still execute.
`timed_out`, `cancelled`, or retryable `failed` may be eligible when a safe
checkpoint exists.  Editing/refining a completed artifact should create a
derivative logical run (or explicit revision lineage), not masquerade as
resuming unfinished execution.

## Migration strategy

1. Add nullable lifecycle/attempt fields or new shadow tables without changing
   existing API responses.  Backfill a UUID logical ID and attempt 1 for every
   `ResearchAgentRun`; preserve integer IDs and artifact foreign keys.
2. Snapshot legacy provenance: `terminal_source=legacy_dare`, current status,
   detail/error/usage, current `hermes_run_id`, reconstructed session ID, and a
   migration timestamp.  Mark evidence quality (`verified`, `inferred`,
   `unavailable`).
3. Map legacy `completed` and non-timeout `failed` to immutable legacy terminals,
   but label Hermes terminal verification separately.  Map live `running` rows
   to `outcome_unknown` unless queue/worker/Hermes evidence proves a safer state.
4. Detect exact legacy budget-failure details such as runs 34–37.  Classify them
   as `legacy_local_deadline_unreconciled`; do not call them confirmed Hermes
   failures.  Where durable Hermes evidence is available, append the observed
   execution outcome.  Where old GET is 404, retain uncertainty rather than
   manufacture completion.
5. Dual-read old rows through a compatibility serializer, then shadow-write the
   new attempt/event model.  Verify counts, IDs, statuses, artifact links, and
   user visibility before making the new model authoritative.
6. Enable fenced transitions and reconciliation per mode behind a feature flag;
   migrate artifact runs first, then Scout/Critic.  Remove legacy writes only
   after parity and recovery drills pass.

## Staged implementation and tests

### Stage 0 — contract lock (no product behavior change)

- Turn the canonical fixture into request-construction, SSE replay, terminal
  race, tool-policy, parse/repair provenance, and serializer visibility tests.
- Add table-driven transition tests for every state/edge and forbidden terminal
  rewrite.

### Stage 1 — lifecycle foundation (first independent slice)

- Add logical/attempt identity, attempt number, fence token, state version,
  absolute deadline, terminal source/reason, and transition audit rows.
- Implement one transactional compare-and-swap transition service and make it
  reject stale attempt/fence writes.
- Backfill legacy rows and shadow-write the fields without changing dispatch or
  frontend behavior.
- Tests: migration reversibility, concurrent transition races, stale-worker
  rejection, terminal immutability, and runs 34–37 classification.

### Stage 2 — reliable dispatch

- Add create idempotency, DB outbox, worker lease/heartbeat, and attempt-aware
  Redis jobs.
- Tests: Redis down, duplicate dispatcher, worker death before/after claim, API
  retry, and lost enqueue acknowledgement.

### Stage 3 — Hermes durable execution contract

- Add Hermes idempotent create, durable run registry, event sequence/replay,
  terminal revisions, and durable cancellation acknowledgement.
- Tests: lost POST response, duplicate POST conflict, SSE disconnect/resume,
  stop-before/after-complete, executor-thread lag, and Hermes restart.

### Stage 4 — reconciliation and deadlines

- Add watchdog/reconciler, deadline states, retry backoff, and product-vs-
  execution terminal projection.
- Tests: completion one tick before/at/after deadline, stop 404 with completed
  GET, network partition, DARE restart, DB failure after terminal, repeated
  reconciliation, and irretrievable outcome.

### Stage 5 — durable output, tools, and usage

- Persist raw envelopes under retention controls, all child Hermes executions,
  event/tool provenance, full usage, parse errors, and idempotent artifacts.
- Tests: malformed output + repair context, child-run accounting, duplicate
  terminal replay, forbidden tool, tool off-by-one, silent long tool, and
  semantic artifact validation.

### Stage 6 — continuation and UI

- Expose pending reconciliation/unknown states, cancellation progress, attempt
  lineage, honest usage, and guarded continuation.
- Tests: late predecessor event after retry/continuation, double continuation,
  continuation eligibility matrix, polling across every state, and frontend
  restart/resume.

## First independently implementable code slice

Implement Stage 1 only: the attempt identity/fence/deadline schema plus a
transactional state-transition service and fixture-backed tests, initially in
shadow mode.  It is independently valuable, does not require the Hermes API to
change, prevents future late-event overwrite by construction, gives migration a
safe landing zone, and leaves existing product behavior untouched until the
reliable-dispatch and reconciliation stages are ready.
