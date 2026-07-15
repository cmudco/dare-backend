# DARE → Hermes artifact lifecycle evidence

Captured 2026-07-15 (Asia/Karachi), against DARE revision `7c97a617c1f9697aed39699d2fc56f04e3ffc9e3`. This is an evidence and replay contract only; it intentionally makes no lifecycle, timeout, reconciliation, continuation, artifact-format, tool-policy, or frontend behavior changes.

The reusable redacted input/protocol fixture is [`research/fixtures/dare_hermes_artifact_replay_v1.json`](../../research/fixtures/dare_hermes_artifact_replay_v1.json).

## Conclusions

1. `ResearchAgentRun.allowed_tools` is DARE metadata, not execution-time enforcement through the current adapter. Artifact runs store `["skills"]`, the serializer exposes it as `tools`, `_start_run()` does not pass it to `HermesService.start_run()`, and the DARE Hermes POST body contains no tool-policy field.
2. The effective tool surface comes from the Hermes API-server profile at runtime. In the controlled run, enabled built-in toolsets were `web`, `vision`, `skills`, and `todo`; the tool schema contained 19 functions / 41,044 JSON bytes. The configured DARE MCP entry registered zero tools after the local gateway restart because the local DARE MCP endpoint was unavailable.
3. The direct SVG probe invoked `vision_analyze` because it bypassed DARE and ran against that API-server profile, where vision is enabled. The full DARE SVG run independently reproduced the same fact: although DARE recorded only `["skills"]`, Hermes called `skill_view`, `vision_analyze`, and `todo`. The vision call used `placeholder` as its image source and failed validation. This is a Hermes tool-selection result, not a DARE MCP `web_search`/`fetch_page` call.
4. DARE's artifact worker concatenates `message.delta` text only in worker memory. It does not durably persist the complete raw SSE stream or model envelope. After a successful parse, it persists the accepted artifact fields/content; after parse failure, the accumulated raw response is generally lost. `tool.started` updates an in-memory preview, while `tool.completed` increments the in-memory tool-call budget. Artifact jobs supply no `on_tool` callback, so neither event creates artifact-run tool-call rows.
5. Historical runs 34–37 show terminal-truth loss: DARE marked them failed after its local budget, its stop requests returned 404, and Hermes completed later. DARE did not reconcile the later terminal payload.
6. The controlled runs used an existing local AI-coding-assistant project, not Eira's dev project. Their exact context is separately captured so local output is never mistaken for historical Eira output.
7. The first execution-control slice now removes DARE's 18-tool/eight-minute
   counter and its stop-then-finalize branch. The configured Hermes
   `agent.max_turns: 40` ceiling is the sole automatic agent-loop bound. This
   records the new code state; it does not rewrite the historical evidence in
   conclusions 4–5.
8. Ambiguous SSE termination now crosses a synchronous terminal-verification
   boundary: DARE GETs the Hermes run, recovers confirmed completed output,
   records confirmed failure/cancellation, preserves active status, and uses
   `outcome_unknown` when terminal truth is unavailable. Scheduled
   reconciliation is still absent.
9. Automatic `_reask_json` repair is removed. Reusing `session_id` did not load
   the preceding response into `/v1/runs`; the repair sent no explicit history
   or previous-response pointer, created a separate Hermes run ID, and left its
   usage outside the primary DARE record.

## Exact historical scope

The July 10 evidence exists on the dev server. Local database IDs 31–37 refer to unrelated June data, so every fixture must include the environment/project identity and must never treat a bare numeric run ID as globally meaningful.

Project 4 was titled “The Effectiveness and Risks of Generative AI in Higher Education.” The exact question, soul v1, and the one approved knowledge item that existed when artifact runs 34–37 started are captured verbatim in the JSON fixture. A second knowledge item was created at 12:57 UTC, after those runs, and is excluded.

| DARE run | Request | DARE interval (UTC) | Hermes run | DARE terminal state | Hermes terminal evidence |
|---|---|---|---|---|---|
| 34 | publication-quality SVG | 12:04:19–12:15:00 | `run_a05bc3a197df4138b04459d22cf89e67` | failed, over 8 minutes | API completion logged 12:16:59; Hermes log fields `input=9360`, `output=58200`; terminal message 30,262 chars, SHA-256 `a6b835…e62137` |
| 35 | same SVG | 12:16:24–12:25:59 | `run_04e4e4582e4849c98849f826906522ed` | failed, over 8 minutes | API completion logged 12:28:24; Hermes log fields `input=9360`, `output=60502`; terminal message 37,342 chars, SHA-256 `4bd6fa…1c26fb` |
| 36 | Excalidraw ecosystem | 12:30:41–12:41:38 | `run_03f79a94534447e69648b42db61ded72` | failed, over 8 minutes | API completion logged 12:44:23; Hermes log fields `input=73421`, `output=2784`; assistant messages 39,071 and 8,292 chars around automatic continuation |
| 37 | same Excalidraw | 12:32:07–12:53:21 | `run_fbc120b6e2e047549889a7bd2a92df41` | failed, over 8 minutes | API completion logged 12:54:50; Hermes log fields `input=9415`, `output=41288`; automatic-continuation reply 26,862 chars, SHA-256 `93ff0f…7dcace` |

For all four, DARE's stop endpoint returned 404 and the DARE usage field remained `{}`. The old Hermes GET endpoints now return 404 after a gateway restart; the gateway session database and service logs are the durable historical evidence. The displayed usage numbers are Hermes log fields, not asserted authoritative aggregate token totals. Run 36 is especially uncertain because automatic continuation and the observed accounting shape make aggregate interpretation unsafe.

The exact historical tasks were:

- Runs 34–35: `Generate a publication-quality SVG infographic summarizing the current evidence on generative AI in higher education. Include key findings, evidence strength, supporting vs. contradicting studies, major risks, and future research gaps using a clean academic design.`
- Runs 36–37: `Generate an Excalidraw whiteboard illustrating the ecosystem of generative AI in higher education, including stakeholders (students, faculty, universities, policymakers), AI tools, learning outcomes, risks, governance mechanisms, and evidence flows.`

Runs 36 and 37 contain this exact 176-character Hermes-generated message: `[System: Your previous response was truncated by the output length limit. Continue exactly where you left off. Do not restart or repeat prior text. Finish the answer directly.]` It is Hermes' automatic output-length continuation, not DARE's `_reask_json` path. DARE had already recorded budget failure before the later Hermes completion, so it could not have parsed that later response and initiated a repair.

The retained Hermes session database proves the historical assembled-input lengths and hashes listed per run in the fixture. It does not preserve a canonical historical POST capture, raw SSE transcript, headers, or terminal GET envelope. Session-key values and historical instruction construction are therefore labeled source-code reconstructions rather than exact captured transport evidence.

## Actual request construction

`POST /api/research/projects/{project_id}/artifact/` creates a `presenter/artifact` run with `status=running`, detail `Queued…`, selected context `{artifactType: …}`, and `allowed_tools=["skills"]`, then enqueues `run_artifact_job(run.id)`.

The worker:

1. Reads the current soul and provisions it to Hermes when soul sync is enabled.
2. Constructs `input` from the exact task, project question, and up to 12 approved knowledge items. Each knowledge body is truncated to 300 characters.
3. Constructs the full Presentation Assistant instructions from the soul plus the type-specific JSON artifact contract.
4. Uses `session_id={artifact_session.hermes_session_id}-r{run.id}` and header `X-Hermes-Session-Key=dare-proj{project_id}`.
5. POSTs only `input`, `instructions`, and `session_id` to `/v1/runs` with bearer authorization and JSON content type.
6. Streams `/events`: `message.delta` is accumulated transiently; `tool.started` updates the last preview; `tool.completed` invokes persistence when a caller supplied `on_tool`; `reasoning.available` is ignored; `run.completed` ends consumption. DARE no longer counts these events as an execution limit.
7. Parses the in-memory JSON envelope. On success it persists accepted `ResearchArtifact` fields/content; it does not persist the complete raw response. It then GETs the original Hermes run only to copy `usage` before saving DARE's terminal state.

The artifact contract is prompt-level. There is no response schema at the gateway boundary.

## Controlled end-to-end runs

All controlled requests used the existing local AI-coding-assistant project, entered through DARE's artifact API, were enqueued, and were executed by a real RQ worker. A transparent local proxy observed DARE's Hermes POST, SSE, terminal GET, timing, redacted headers, and payload shapes. The fixture normalizes that evidence; it is not a byte-for-byte raw SSE archive. The frontend observer polled the same serialized run endpoint on its production cadence. The fixture records the local context and assembled-input hashes separately from Eira's historical context.

| Diagnostic ID | DARE / Hermes | Observed events/tools | Terminal result |
|---|---|---|---|
| `dare-hermes-artifact-v1-mermaid` | 137 / `run_3f2e…` | 4 deltas, `reasoning.available`, `run.completed`; no tools | completed, one Mermaid artifact; 6,797 / 132 tokens |
| `dare-hermes-artifact-v1-svg` | 138 / `run_eb744…` | `skill_view` ×3, `vision_analyze`, `todo`, deltas, terminal event | original response malformed; repair had no prior context; completed with no artifact; 61,896 / 10,328 tokens |
| `dare-hermes-artifact-v1-excalidraw` | 139 / `run_32384…` | `skill_view` ×2, deltas, terminal event | completed, one 11-element scene; 35,391 / 7,934 tokens |
| `dare-hermes-artifact-v1-svg-retry` | 140 / `run_e15c8cfaa40f44ba806eb85134812e4e` | deltas then `run.completed`; no tools | completed, one locally topic-aligned SVG with verified `0 0 640 360` viewBox; verified 6,867 input + 6,650 output = 13,517 total tokens |

Run 140's retained Hermes assistant message proves the following, without relying on the deleted temporary DARE artifact row: envelope SHA-256 `53f0b4…4e79f` (2,256 chars); SVG content SHA-256 `f9c7b7…efa57c` (1,906 chars/bytes); valid XML; root `svg`; viewBox `0 0 640 360`; 19 XML elements including the root (`rect` ×4, `line` ×3, `text` ×8, `defs`, `marker`, and `path`); zero `script`, `foreignObject`, or `image` elements; zero href or event-handler attributes. This proves the requested viewBox and those inspected safety/size properties. It is not a general renderer-safety or geometric clipping proof, so the report does not make an unqualified “bounded SVG” claim.

Run 140's aligned observable timeline was:

| Time | Layer | Observable state |
|---:|---|---|
| 20:48:47.686 UTC | DARE API/DB | HTTP 202 `{runId: 140, status: running}`; DB `running / Queued…` |
| 20:49:03.062 UTC | worker/DB | `running / Generating artifact…`; Hermes run ID persisted |
| immediately before SSE | DARE → Hermes | POST `/v1/runs`; bearer redacted; session `dare-proj2-artifact-r140`; session key `dare-proj2`; HTTP 202 queued `run_e15c…` |
| next ~62 s | DARE → Hermes | GET SSE; `message.delta` chunks form the JSON envelope; no tool events |
| SSE terminal | Hermes | `run.completed` contains terminal output and usage |
| immediately after SSE | DARE → Hermes | GET run returns `completed`, `last_event=run.completed`, full output, model, timestamps, and usage |
| 20:50:05.245 UTC | DARE DB | artifact row inserted; run saved `completed / Generated 1 artifact.` with usage |
| observer +56,560 ms | frontend poll | terminal run metadata and usage visible; caller can refetch project artifacts to see the SVG |

Absolute timestamps from independent processes differed because the local Hermes gateway reported a non-host epoch. Ordering and relative capture time are reliable; cross-process absolute epoch values must not be aligned without clock normalization.

## Fresh endpoint probes after the ownership decision

Two direct, tool-free runs were executed against the already-running local
Hermes gateway on 2026-07-15. No service was restarted or reconfigured. The
gateway health endpoints reported `status=ok`, `gateway_state=running`, and zero
active agents before the probes; local Hermes configuration reported
`agent.max_turns: 40`.

| Probe | Start response | SSE sequence | Terminal GET |
|---|---|---|---|
| exact text | `run_7fd8d6e723634aa0acf6a747f970786a`, `started` | two `message.delta`, `reasoning.available`, `run.completed` | `completed`; output `DARE_HERMES_PROBE_OK`; usage 5,852 input / 18 output / 5,870 total |
| exact JSON | `run_359a80bd659c4fbdb251a63b5b5e6631`, `started` | two `message.delta`, `reasoning.available`, `run.completed` | `completed`; output `{"probe":true,"owner":"hermes"}`; usage 5,854 input / 19 output / 5,873 total |

Observed field shapes:

- `POST /v1/runs` returned `run_id` and `status`.
- `message.delta` carried `event`, `run_id`, `timestamp`, and `delta`.
- `reasoning.available` carried `event`, `run_id`, `timestamp`, and `text`.
- terminal SSE carried `event`, `run_id`, `timestamp`, `output`, and `usage`.
- terminal `GET /v1/runs/{id}` carried `object`, `run_id`, `status`,
  `session_id`, `model`, `last_event`, `output`, `usage`, `created_at`, and
  `updated_at`.

## Prompt contribution

`hermes prompt-size --platform api_server --json` reported a base system prompt of 18,278 characters / 18,336 bytes, plus 19 tool schemas totaling 41,044 JSON bytes. The controlled first calls reported about 6.8k input tokens even though DARE's assembled input plus artifact instructions was only about 2.8k characters (roughly 600 `cl100k_base` tokens). The evidence supports the inference that most first-turn input comes from the Hermes runtime prompt/tool surface rather than DARE project context.

The exact controlled context components are in the fixture. Token estimates are diagnostic only because the serving model's tokenizer is not guaranteed to match `cl100k_base`. Run 140's terminal GET verifies 6,867 input + 6,650 output = 13,517 total tokens. Other controlled multi-turn fields (including 61,896 and 35,391 input) are cumulative and must not be mistaken for initial prompt size. Historical runs 34–37 have only the separately labeled Hermes log fields described above.

## Lossy boundaries

| Boundary | Emitted | Retained or exposed | Lost/changed |
|---|---|---|---|
| API request → run row | prompt + artifact type | task, selected context, recorded allowed tools | request headers and exact serialized request are not stored |
| run row → Hermes POST | task + question + truncated knowledge; soul + contract | Hermes session DB retains user/assistant messages | `allowed_tools` omitted; knowledge beyond 12 items/300 chars omitted |
| Hermes SSE → worker | deltas, reasoning, tool events, terminal event | delta text and last preview exist transiently in worker memory | complete raw SSE/model envelope not persisted; reasoning and unknown events ignored; artifact tool events not persisted |
| Hermes terminal GET → DARE | status, output, usage, model, timestamps | usage only | terminal status/output/model/timestamps ignored |
| parse/repair | in-memory raw output and parse errors | successful parses persist accepted artifact fields/content; run detail retains final count | failed raw output/errors generally not persisted; controlled run 138 repair used no session-key header and had no prior context |
| historical budget expiry (removed in first slice) | local deadline plus stop attempt | failed DARE row | later Hermes completion was neither reconciled nor persisted |
| DARE serializer → frontend | run row + tool rows | metadata/status/detail/usage; `allowed_tools` renamed `tools` | no run output/error/artifact body; artifact tool rows are empty |
| frontend polling | serialized run every 3 seconds after 1.5-second delay | latest sampled state | intermediate states/events between polls |

## Minimal later tests

Use the JSON fixture as data, not as a golden model-output snapshot.

1. Request-construction unit test: freeze the project, soul, approved knowledge, run ID, and session; assert the exact Hermes JSON and redacted headers. Assert explicitly whether the future contract includes an enforced tool policy.
2. SSE lifecycle contract test: replay ordered deltas, reasoning, tool start/completion, terminal completion, disconnection, and completion-after-local-deadline. Assert what each layer persists.
3. Terminal reconciliation test: make stop return 404 and GET return a completed output; assert the chosen terminal-truth contract.
4. Repair-context test: return malformed JSON, then assert the corrective run has an explicit continuity contract and that original/repair provenance remains auditable.
5. Serializer/poller visibility test: sample every DARE transition and assert exactly what the existing frontend sees, without designing the future timeline yet.
6. Artifact semantic/shape tests: validate Mermaid syntax, SVG bounds/safety/topic grounding, and Excalidraw bounds/element count separately from transport success.
7. Tool-policy test: advertise a forbidden built-in such as `vision_analyze`, record `["skills"]`, and prove whether runtime enforcement blocks it. Keep DARE MCP tools and Hermes built-ins as separate assertions.

These tests settle the lifecycle and reconciliation contract before any Continue behavior or final frontend timeline is designed.
