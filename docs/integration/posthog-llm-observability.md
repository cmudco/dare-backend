# PostHog LLM Observability

DARE emits one [`$ai_generation`](https://posthog.com/docs/ai-observability/manual-capture) event per LLM provider call to PostHog, giving per-turn trace waterfalls (with prompts/outputs), token cost dashboards, latency breakdowns, and error-rate tracking — across all providers (OpenAI, Claude, Gemini, Llama, custom/LiteLLM).

## Setup

1. Create a (free) PostHog project at [posthog.com](https://posthog.com) — no credit card required, 100K events/month free.
2. Copy the project API key (`phc_...`) from Project Settings.
3. Set the environment variables:

```bash
POSTHOG_API_KEY=phc_your_project_key
POSTHOG_HOST=https://us.i.posthog.com   # or https://eu.i.posthog.com
POSTHOG_LLM_CAPTURE_CONTENT=True        # False = telemetry only, no prompt/response content
```

When `POSTHOG_API_KEY` is unset, the integration is a complete no-op — safe for local dev and CI.

## Architecture

Capture happens at a single choke point: `LLMService._execute_llm_completion` in `core/services/llm_service.py`. Every completion — chat, Socratic, workflow steps, and follow-up calls after DARE/MCP tool execution — flows through it, so no per-caller instrumentation exists anywhere else.

```
MessageCoordinator ─┐
Workflow execution ─┼─▶ LLMService.query ─▶ _execute_llm_completion ─▶ provider service
Tool follow-ups ────┘                              │
                                                   ▼
                                          GenerationTracker ─▶ $ai_generation
```

Components (`core/services/llm_observability_service.py`):

- **`LLMObservabilityService`** — process-wide singleton owning the PostHog client (background sender thread). Fire-and-forget: every method swallows its own exceptions; observability can never break or slow a chat turn.
- **`GenerationTracker`** — per-call accumulator. Records first-token time, streamed chunks, and the provider's usage dict; `finish()` (called from a `finally`, so client disconnects still capture) builds an `LLMGenerationRecord` DTO and captures it.
- **`LLMGenerationRecord`** — frozen DTO in `core/services/dtos/llm_generation_dto.py`.

## Identity mapping

| PostHog concept | DARE value | Groups |
|---|---|---|
| `distinct_id` | user id (`"anonymous"` for public bots) | events by person |
| `$ai_session_id` | conversation id | all turns of a conversation |
| `$ai_trace_id` | AI message id | all generations of one turn, incl. post-tool follow-ups |

## Captured properties

| Property | Source |
|---|---|
| `$ai_provider`, `$ai_model` | resolved `LLM` row |
| `$ai_input`, `$ai_output_choices` | built messages / accumulated stream (omitted when `POSTHOG_LLM_CAPTURE_CONTENT=False`) |
| `$ai_input_tokens`, `$ai_output_tokens` | provider usage dict (PostHog derives cost from model + tokens) |
| `$ai_latency`, `$ai_time_to_first_token` | monotonic clock around the provider call |
| `$ai_is_error`, `$ai_error` | raised exceptions, plus the provider services' `"Error: ..."` sentinel chunks (which never raise) |
| `$ai_stream` | streaming vs structured (non-streaming) call |
| `tool_call_count`, `is_socratic` | custom DARE properties |

## Error semantics

Provider services (`ClaudeService`, `OpenAIService`, …) catch their own exceptions and yield an `"Error: ..."` text chunk with no usage dict instead of raising. The tracker treats a stream that ends with an `Error:`-prefixed output and no usage as a failed generation, so provider failures are visible in PostHog even though no exception ever propagates.

## Privacy

`POSTHOG_LLM_CAPTURE_CONTENT=False` keeps all telemetry (model, tokens, cost, latency, errors) but omits `$ai_input` / `$ai_output_choices`, for deployments where prompt/response content must not leave the infrastructure.
