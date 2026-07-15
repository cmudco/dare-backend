# Research mode remediation tracker

Status: living coordination document. This records proven behavior, agreed
destinations, and task boundaries. It is not evidence by itself and does not
replace the linked evidence and contract documents.

## Why this exists

The investigation began with recent Eira research-mode runs that appeared to
hit DARE limits or finish without a usable Scout result or artifact. The work
revealed that DARE could record a budget failure while Hermes continued and
later completed, because DARE was independently counting streamed tool events,
enforcing an event-driven eight-minute deadline, and treating its stop request
as execution truth.

The current direction is now explicit:

- DARE owns authorization, request assembly, product state, validation, and UX.
- Hermes owns agent execution and enforcement of its configured loop ceiling.
- Hermes terminal state is execution truth; DARE records and interprets it.
- Transport loss is not a fabricated execution failure.
- Missing or invalid product output is not called limit exhaustion unless
  Hermes reports that reason.
- DARE must retain what it actually sent and what Hermes actually returned;
  it must not reconstruct missing facts.

## Canonical references

- `dare-hermes-artifact-lifecycle-evidence-2026-07-15.md` — captured evidence.
- `dare-hermes-execution-control-contract.md` — chosen execution boundary.
- `dare-hermes-artifact-lifecycle-design-2026-07-15.md` — lifecycle design and
  pre-change behavior.
- `research/fixtures/dare_hermes_artifact_replay_v1.json` — deterministic
  evidence and replay fixture.

## Current position

| Area | State | Current conclusion |
|---|---|---|
| Evidence package | Complete | Canonical evidence and replay fixture are available. |
| DARE execution counters | Implemented locally | The 18-tool counter, event-driven eight-minute deadline, automatic stop, budget finalizer, and automatic JSON repair run were removed. |
| Terminal verification | Implemented locally | Ambiguous SSE endings are verified with terminal GET; active, cancelled, failed, completed, and `outcome_unknown` are not conflated. |
| Hermes execution ceiling | Proven with limitation | Global `agent.max_turns=40` controls loop iterations, but Hermes currently hides `max_iterations_reached` behind ordinary `completed`. |
| Observability destination | Locked conceptually | Preserve the exact safe outbound request, complete Hermes terminal response, real emitted events, and DARE parsing/validation result. |
| Product landing | Review-ready locally | R1 scope and verification are complete; the slice still requires normal commit and review handling. |

## Locked observability destination

For each logical DARE run, the audit view should be able to show four distinct
layers.

### 1. Request sent to Hermes

Persist the exact post-assembly request body:

- `input`
- `instructions`
- `session_id`
- safe endpoint and session-scope metadata
- request hash and prompt-builder version
- a deterministic context manifest explaining which task, question, soul
  version, knowledge, memory, and tool metadata were included, truncated, or
  omitted

Never persist bearer tokens, API keys, credentials, or unsafe headers.

### 2. Execution events emitted by Hermes

- Tool lifecycle and errors remain durable audit events.
- Lifecycle terminal events remain durable.
- `message.delta` is live transport data and an emergency partial-output
  fallback, not the canonical final response.
- `reasoning.available` may be retained as an optional, access-controlled,
  finite-retention "Hermes reasoning trace." It is not execution truth and is
  never used to infer success, failure, or limit exhaustion.

### 3. Terminal Hermes record

Persist the complete safe terminal GET envelope and exact raw output. The
canonical output order is:

1. terminal GET `output`;
2. `run.completed.output` when terminal GET is unavailable;
3. concatenated deltas explicitly labeled incomplete when neither terminal
   source is available.

Today Hermes reports status, output, token usage, session, model, timestamps,
and last event. It does not report agent iterations, provider-call count, or
the distinction between normal completion and loop exhaustion.

### 4. DARE interpretation

Store parsing and validation separately from Hermes execution truth:

- accepted output;
- valid empty result;
- invalid/unparseable output;
- partial output;
- validation errors;
- findings or artifacts created/rejected and why.

A valid empty Scout inbox or artifact envelope is not automatically a limit.
Only an authoritative Hermes termination reason may establish exhaustion.

## Remaining work, in dependency order

### R1 — Land the completed execution-control slice

State: scoped verification complete; ready for normal commit/review.

Done means:

- Review the complete scoped diff.
- Run the relevant DARE and Hermes tests plus formatting/diff checks.
- Ensure all evidence, contract, fixture, deployment, task, adapter, constant,
  and test files intended for the slice are included.
- Commit and submit through the normal review path.

Verification summary (2026-07-15):

- Reviewed the complete DARE slice. Its intended file set is limited to the
  task, Hermes adapter, status constants, deployment guidance, three canonical
  research documents, replay fixture, and two focused test modules listed
  below.
- DARE: all 16 `research` tests pass; Django system checks pass; the
  `research` app has no pending migrations; Black and isort (Black profile)
  checks pass for all five touched Python modules.
- Hermes: all 28 run API/config bridge tests pass; the three focused
  iteration-exhaustion and output-continuation tests pass (364 unrelated tests
  deselected). The Hermes suite reports 22 existing `aiohttp` warnings.
- The replay fixture passes strict JSON decoding with duplicate-key detection,
  redaction assertions, and both Python and `jq` parsing. `git diff --check`
  passes in DARE and Hermes.
- Unrelated working-tree changes are explicitly excluded: the local Hermes
  audit patches in `agent/tool_executor.py`, `gateway/platforms/api_server.py`,
  and `tools/mcp_tool.py`, plus the frontend `.claude/launch.json` file. No
  running service was changed or restarted.

Exact R1 include set:

- `docs/deployment/research-mode-hermes.md`
- `docs/research/dare-hermes-artifact-lifecycle-design-2026-07-15.md`
- `docs/research/dare-hermes-artifact-lifecycle-evidence-2026-07-15.md`
- `docs/research/dare-hermes-execution-control-contract.md`
- `docs/research/research-mode-remediation-tracker-2026-07-15.md`
- `research/constants.py`
- `research/fixtures/dare_hermes_artifact_replay_v1.json`
- `research/services/hermes_service.py`
- `research/tasks.py`
- `research/test_hermes_service.py`
- `research/test_tasks.py`

Known limitations intentionally remain for later tracker items: no scheduled
reconciliation or restart recovery; active and `outcome_unknown` records are
not revisited automatically; the frontend does not yet represent the new
nonterminal/unknown states; Hermes run records remain process-memory/TTL
bounded; and Hermes still reports iteration exhaustion as ordinary completion
without an authoritative termination reason. The remaining R1 landing gate is
committing this exact include set and submitting it through the normal review
path.

### R2 — Explicit user cancellation

State: implemented and locally verified; ready for normal commit/review.

Target:

- Persist cancellation intent separately from confirmed cancellation.
- Treat Hermes `stopping` as acknowledgement, never terminal truth.
- Make repeated cancellation requests idempotent and authorization-safe.
- Let confirmed Hermes completion win a completion/cancellation race.
- Preserve honest ambiguity when Hermes cannot be reached.

Out of scope: scheduled restart reconciliation.

Implementation summary (2026-07-15):

- Added durable, separate fields for the first cancellation request and actor,
  claimed stop-attempt count/time, stop acknowledgement, terminal cancellation
  confirmation, safe stop HTTP status, and bounded error code/detail.
- Added owner-scoped `POST /api/research/agent-runs/{run_id}/cancel/`. Feature
  access remains governed by the existing research API permission class.
- Commits cancellation intent before any Hermes I/O, then claims an attempt in
  a short row-locked transaction. A 90-second database lease prevents repeated
  or overlapping requests from producing uncontrolled parallel stop calls.
- Maps Hermes stop responses into safe structured results for acknowledgement,
  404, timeout, connection/transport failure, invalid JSON, non-2xx, and 5xx;
  raw upstream bodies and credentials are neither stored nor serialized.
- Immediately verifies terminal Hermes state after every claimed stop attempt.
  `stopping` is only acknowledgement; only terminal `cancelled` writes
  `cancellation_confirmed_at`; terminal `completed` wins the race; terminal
  `failed` remains failed; active states remain nonterminal; unavailable or
  unrecognized truth becomes `outcome_unknown` without `completed_at`.
- Queued DARE jobs honor durable intent before starting Hermes. A request that
  races Hermes run creation is rechecked immediately after the Hermes run ID is
  stored. Cancelled/unconfirmed work exits before parsing or creating findings,
  verdicts, artifacts, or successful assistant messages.
- Corrected research chat stream handling so `run.failed` and `run.cancelled`
  are verified and cannot be persisted as successful completed assistant
  responses.

Verification: all 35 focused adapter/task/cancellation tests pass; all 37
`research` tests pass; Django system checks pass; `makemigrations --check`
reports no model drift; the migration plan contains the nine intended fields;
Black, isort, and `git diff --check` pass.

R3 remains required for attempts left active, acknowledged, or
`outcome_unknown` after the immediate verification; retry after a crashed or
expired attempt lease; Hermes restart/unavailability; and terminal truth that
appears only after the request/worker has returned.

### R3 — Durable reconciliation and restart recovery

State: deferred by product decision; reframed as frontend display honesty.

Decision (2026-07-15):

- Reconciliation is a reliability net for crash / network-loss / cancel-timeout
  cases only. The normal path already self-terminates: the worker blocks on the
  SSE stream to a terminal event, and an ambiguous stream ending already does
  one terminal GET before the job exits. It is not a normal-path requirement.
- Do not build the scheduler / sweep / backoff subsystem now. Design to the
  observed input/output contract; defer cases with no real solution rather than
  gold-plating hypothetical failure modes.
- The actual user-visible harm — a run stuck non-terminal makes the frontend
  spin forever — is a frontend display-honesty gap that exists regardless of
  reconciliation: `usePolledRun` settles only on `completed`/`failed`, so
  `cancelled`, `outcome_unknown`, and `stopping` already need handling.
- Immediate work is therefore frontend display honesty: represent active /
  cancellation-requested / stopping / outcome-unknown / final outcomes truthfully
  and end forever-spin on non-terminal states.
- If crash recovery later proves a real, recurring pain (measured, not assumed),
  add the minimal single delayed re-check then — not the full sweep.
- `outcome_unknown` remains the honest terminal for truth that cannot be
  confirmed; a Hermes 404 is never read as an outcome.

The full reconciliation discovery (architecture, eligibility/state matrices,
backoff options, smallest implementation slice) is retained out-of-tracker for
reference if this is ever revived.

### R4 — Hermes termination and iteration reporting

State: deferred by product decision; accepted upstream limitation.

Decision:

- Do not maintain a private Hermes patch or make DARE depend on an unaccepted
  upstream addition.
- Treat Hermes `completed` as the execution result Hermes publicly reports,
  even though the current API does not distinguish normal completion from its
  internal iteration ceiling.
- Keep DARE's product validation separate: accepted, valid empty,
  invalid/unparseable, or partial output.
- Never infer limit exhaustion from tool events, elapsed time, token usage,
  output wording, or missing products.
- Preserve the exact request, terminal response, usage, raw output, and DARE
  validation through R5 so users can audit the run using real available data.
- If a future supported Hermes release exposes authoritative termination or
  iteration fields, DARE may adopt them as optional additive metadata.

Continuation eligibility must not depend on suspected iteration exhaustion
while the supported Hermes contract does not report it.

### R5 — Durable run observability

State: destination locked; storage and UX not implemented.

Target: implement the four-layer observability record defined above, including
redaction, retention, size bounds, permissions, and exact provenance.

### R6 — Continuation and execution-attempt lineage

State: not started.

Target:

- A continuation is a new, durable execution attempt under one logical run.
- Persist every Hermes run ID, request, output, usage, terminal reason, and
  parent attempt.
- Use fencing/idempotency so late events cannot overwrite the current attempt.
- Do not resurrect a terminal execution in place.

### R7 — Run-details and artifact UX

State: not started.

Target:

- Clicking a failed/in-progress artifact opens its run details.
- Show request provenance, current/terminal status, tools, errors, usage,
  terminal raw response, DARE validation, cancellation, and continuation when
  genuinely eligible.
- Clearly distinguish execution failure, invalid output, valid empty output,
  cancellation, limit exhaustion, and unknown outcome.

### R8 — Token-conscious artifact generation

State: investigated; redesign not started.

Target: bound SVG and Excalidraw generation by representation, validation, and
token cost. Temporarily disabling expensive formats remains an explicit product
option, not an implicit failure mode.

### R9 — Tool policy and availability

State: known gap.

Target:

- Separate DARE-connected MCP tools from Hermes built-in tools in policy and
  UI.
- Replace metadata-only `allowed_tools` with an enforceable upstream contract
  if tool restriction is required.
- Report configured, registered, available, invoked, and failed tools as
  different facts.

## Decision gates for every task

Each task should stop for review after producing:

1. Current behavior with code/evidence references.
2. Exact data crossing each boundary.
3. Proposed states, contract, and failure/race behavior.
4. Explicit decisions that require product-owner approval.
5. The smallest independently testable implementation slice.

After implementation, the task must update this tracker with its state,
verification, remaining limitations, and canonical supporting document. A task
must not silently absorb a later item from this list.

## Local QA reproduction (2026-07-16)

The original Eira "timeout" scenario was reproduced locally and confirmed fixed.
Firing Eira's exact publication-quality SVG artifact prompt against project 16
with Hermes `agent.max_turns` capped low, DARE waited for Hermes and recorded
the honest terminal outcome (`completed`, "Generated 1 artifact.", real 29.5k
usage) after ~188s — no fabricated wall-clock failure, no local deadline. A
code sweep confirms no residual eight-minute/budget/auto-stop logic remains.

Separately, enabling Sonnet 5 thinking (`reasoning_effort`) fails on the current
Hermes with HTTP 400 (`thinking.type.enabled` deprecated for this model; needs
`thinking.type.adaptive` + `output_config.effort`). Real chain-of-thought
therefore requires the deferred Hermes upstream update, which also covers
`X-DARE-Run-Session` run-id forwarding for exact tool attribution.

## Next action

R1 and R2 are implemented and locally verified but intentionally uncommitted on
branch `farhat/fix/research-mode-remediation-r1-r2`, kept off `dev` until
validated against the frontend. Immediate next step: drive a real cancellation
end-to-end against the frontend to validate R2 and observe how often runs
actually land in non-terminal/unknown states. Then implement frontend display
honesty for those states (the reframed, minimal replacement for R3). R5
observability and R7 run-details follow. R3's full reconciliation is deferred.
R6 must use explicit, observable eligibility rules and must not infer iteration
exhaustion from the current Hermes response.
