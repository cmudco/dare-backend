# DARE–Hermes execution-control contract

Status: DARE boundary implemented and tested; one upstream reporting ambiguity
remains before the broader execution-control architecture is complete.

## Decision

DARE uses Hermes's supported global `agent.max_turns` ceiling. The current local
configuration is `40`. DARE does not maintain a second turn counter, infer turns
from tool events, or impose a hidden wall-clock execution deadline.

The earlier local prototype that added `limits.max_turns` to individual
`POST /v1/runs` requests is not a deployment dependency. It must not be shipped
as a Hermes runtime patch. A per-run limit can be reconsidered only if Hermes
releases it as a supported upstream contract.

## Ownership

- Hermes enforces its configured agent-loop ceiling and emits execution events.
- DARE submits work, records the Hermes run ID, consumes events, and persists
  product artifacts and audit data.
- The user may explicitly request cancellation through Hermes's supported
  `POST /v1/runs/{run_id}/stop` endpoint.
- Transport connection/read timeouts detect communication problems; they are
  not execution budgets and must not be translated into a fabricated Hermes
  terminal outcome.

## Terminal verification boundary

DARE verifies Hermes with `GET /v1/runs/{run_id}` whenever SSE raises, closes
without a terminal event, or contains no message deltas.

| Hermes GET result | DARE behavior |
|---|---|
| `completed` | Use the terminal GET `output` for parsing/persistence |
| `failed` | Record a confirmed Hermes failure |
| `cancelled` | Persist `cancelled`, including reported usage |
| `queued`, `running`, `stopping` | Preserve the active status; do not set `completed_at` or fabricate failure |
| unavailable, 404, empty/unknown status | Persist `outcome_unknown` without `completed_at` |

This is synchronous verification at the stream boundary only. It is not the
future scheduled reconciler.

## Supported run API used by DARE

| Call | Purpose | Data used by DARE |
|---|---|---|
| `POST /v1/runs` | Start execution | request: `input`, `instructions`, `session_id`; response: `run_id`, `status` |
| `GET /v1/runs/{run_id}/events` | Consume SSE | deltas, reasoning, tool lifecycle, and terminal run events |
| `GET /v1/runs/{run_id}` | Poll/reconcile status | status, output, usage, session, model, timestamps, last event |
| `POST /v1/runs/{run_id}/stop` | Explicit cancellation | cancellation acknowledgement; not used as an automatic budget kill-switch |

DARE also sends `X-Hermes-Session-Key` on run creation to scope Hermes memory to
the research project.

## Implemented DARE slice

- Removed `MAX_RUN_TOOL_CALLS`, `MAX_RUN_SECONDS`, and `_RunBudget`.
- Removed automatic budget-triggered calls to the Hermes stop endpoint.
- Removed the separate budget-finalization Hermes run.
- Removed automatic JSON repair re-asks. Invalid output now remains a single
  execution result until attempts/continuations have durable identity and usage.
- Continued to collect every streamed tool completion for audit purposes.
- Added terminal GET verification and durable `cancelled` / `outcome_unknown`
  backend states.

## Hermes global-limit proof

- Active config and gateway logs both resolve `agent.max_turns` to
  `max_iterations=40`.
- The API server reads `HERMES_MAX_ITERATIONS` when constructing `AIAgent`.
- The conversation loop increments `api_call_count` at the start of every loop
  iteration and stops when it reaches `agent.max_iterations` or exhausts the
  shared iteration budget.
- Output-length continuation returns to that same loop and therefore consumes
  another iteration. The Hermes regression test confirms one truncated response
  plus its continuation reports `api_calls == 2`.
- On exhaustion Hermes may make one additional tool-free summarization provider
  call after the capped loop. Therefore `max_turns=40` is a loop/tool ceiling,
  not a mathematically strict maximum of 40 provider requests.
- The current supported `/v1/runs` adapter discards
  `turn_exit_reason=max_iterations_reached(...)` and emits ordinary
  `run.completed` / GET `status=completed`. DARE cannot distinguish this from
  healthy completion without a future upstream reporting field.

## Still deliberately deferred

- Per-request turn, tool-call, cost, or wall-clock limits.
- User-facing cancellation and cancellation-request workflow.
- Durable reconciliation after worker/Hermes restarts or ambiguous network loss.
- Distinguishing upstream turn-limit exhaustion from ordinary completion; the
  inspected current and latest stable run adapters do not expose the internal
  stop reason.

The removed repair path did not have a proven continuity contract. It reused a
`session_id`, but sent neither `conversation_history` nor `previous_response_id`
and did not reuse the project session-key header. Hermes's `/v1/runs` handler
does not hydrate conversation history merely from `session_id`; the canonical
run-138 evidence also observed that the repair lacked preceding response
context. It was therefore a separate, untracked execution with separate usage,
not a safe continuation.

## Contract tests

The focused tests cover terminal completion recovery, confirmed failure,
confirmed cancellation, still-running status after interruption, unavailable
terminal truth, empty streams, streams ending without a terminal event, removal
of automatic repair, and the absence of DARE tool-count enforcement.
