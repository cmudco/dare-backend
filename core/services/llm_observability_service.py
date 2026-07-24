"""
LLM Observability Service (PostHog)

Captures one `$ai_generation` event per LLM provider call using PostHog's
manual-capture AI observability schema. Events group into traces in the
PostHog UI: all generations of a chat turn share a `$ai_trace_id` (message
id), and all turns of a conversation share a `$ai_session_id`.

Design constraints:
- Strictly fire-and-forget: observability must NEVER break or slow a chat
  turn. Every public method swallows its own exceptions.
- No-op when ``POSTHOG_API_KEY`` is unset — safe in local dev and CI.
- Prompt/response content capture can be disabled independently via
  ``POSTHOG_LLM_CAPTURE_CONTENT`` for privacy-sensitive deployments while
  keeping cost/latency/error telemetry.
"""

import logging
import time
import uuid
from typing import Any, Dict, List, Optional

from django.conf import settings
from posthog import Posthog

from core.services.dtos import LLMGenerationContext, LLMGenerationRecord

logger = logging.getLogger(__name__)

# Provider services swallow exceptions and yield this sentinel as a chunk
# (see e.g. ClaudeService.stream_chat_completion), so a failed call ends the
# stream with an "Error:" prefixed chunk and no usage dict.
ERROR_SENTINEL_PREFIX = "Error:"

ANONYMOUS_DISTINCT_ID = "anonymous"


class GenerationTracker:
    """Accumulates timing, output, and usage for one in-flight LLM call.

    Created by ``LLMObservabilityService.track_generation`` at the moment the
    provider call starts. The caller feeds it streamed chunks and usage dicts
    as they arrive, then calls ``finish()`` exactly once (from a ``finally``
    block) to emit the `$ai_generation` event.
    """

    def __init__(
        self,
        service: "LLMObservabilityService",
        context: LLMGenerationContext,
    ):
        self._service = service
        self._context = context
        self._started_at = time.monotonic()
        self._first_token_at: Optional[float] = None
        self._output_parts: List[str] = []
        self._usage: Optional[Dict[str, Any]] = None
        self._tool_call_count = 0
        self._error_message: Optional[str] = None
        self._finished = False

    def record_chunk(self, chunk: Optional[str]) -> None:
        """Record a streamed text chunk (no-op for empty chunks)."""
        if not chunk:
            return
        if self._first_token_at is None:
            self._first_token_at = time.monotonic()
        self._output_parts.append(chunk)

    def record_usage(self, usage: Optional[Dict[str, Any]]) -> None:
        """Record the latest usage dict yielded by the provider stream."""
        if not usage:
            return
        self._usage = usage
        if usage.get("tool_calls"):
            # Assignment, not increment: providers may re-yield the same
            # tool_calls list on subsequent usage updates.
            self._tool_call_count = len(usage["tool_calls"])

    def record_error(self, error: Exception) -> None:
        """Record an exception raised by the provider call."""
        self._error_message = str(error)

    def finish(self) -> None:
        """Build the generation record and hand it to the service. Idempotent."""
        if self._finished:
            return
        self._finished = True

        output = "".join(self._output_parts)
        latency = time.monotonic() - self._started_at
        time_to_first_token = (
            self._first_token_at - self._started_at
            if self._first_token_at is not None
            else None
        )

        # Failed provider calls surface as an "Error:" chunk with no usage
        # dict rather than an exception (provider services catch internally).
        is_error = self._error_message is not None or (
            self._usage is None and output.startswith(ERROR_SENTINEL_PREFIX)
        )
        error_message = self._error_message
        if is_error and error_message is None:
            error_message = output

        usage = self._usage or {}
        context = self._context
        record = LLMGenerationRecord(
            provider=context.provider,
            model=context.model,
            input_messages=context.input_messages,
            output=output,
            latency=latency,
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            time_to_first_token=time_to_first_token,
            is_streaming=context.is_streaming,
            is_error=is_error,
            error_message=error_message,
            trace_id=context.trace_id or str(uuid.uuid4()),
            session_id=context.session_id,
            distinct_id=context.distinct_id or ANONYMOUS_DISTINCT_ID,
            tool_call_count=self._tool_call_count,
            extra_properties=context.extra_properties,
        )
        self._service.capture_generation(record)


class LLMObservabilityService:
    """PostHog-backed LLM analytics. No-op when POSTHOG_API_KEY is unset."""

    def __init__(self):
        self._client: Optional[Posthog] = None
        api_key = getattr(settings, "POSTHOG_API_KEY", None)
        if not api_key:
            logger.info(
                "[LLMObservability] POSTHOG_API_KEY not set — LLM analytics disabled"
            )
            return
        try:
            self._client = Posthog(
                api_key,
                host=getattr(settings, "POSTHOG_HOST", "https://us.i.posthog.com"),
            )
            logger.info("[LLMObservability] PostHog LLM analytics enabled")
        except Exception as e:
            logger.error(f"[LLMObservability] Failed to initialize PostHog: {e}")
            self._client = None

    @property
    def enabled(self) -> bool:
        return self._client is not None

    def track_generation(self, context: LLMGenerationContext) -> GenerationTracker:
        """Start tracking one LLM provider call. Always returns a tracker;
        the tracker is inert (capture no-ops) when the service is disabled."""
        return GenerationTracker(service=self, context=context)

    def capture_generation(self, record: LLMGenerationRecord) -> None:
        """Emit a `$ai_generation` event to PostHog. Never raises."""
        if self._client is None:
            return
        try:
            properties: Dict[str, Any] = {
                "$ai_provider": record.provider,
                "$ai_model": record.model,
                "$ai_latency": round(record.latency, 4),
                "$ai_trace_id": record.trace_id,
                "$ai_stream": record.is_streaming,
                "$ai_is_error": record.is_error,
            }
            if self._capture_content():
                properties["$ai_input"] = record.input_messages
                properties["$ai_output_choices"] = [
                    {"role": "assistant", "content": record.output}
                ]
            if record.input_tokens is not None:
                properties["$ai_input_tokens"] = record.input_tokens
            if record.output_tokens is not None:
                properties["$ai_output_tokens"] = record.output_tokens
            if record.time_to_first_token is not None:
                properties["$ai_time_to_first_token"] = round(
                    record.time_to_first_token, 4
                )
            if record.session_id:
                properties["$ai_session_id"] = record.session_id
            if record.is_error and record.error_message:
                properties["$ai_error"] = record.error_message[:2000]
            if record.tool_call_count:
                properties["tool_call_count"] = record.tool_call_count
            properties.update(record.extra_properties)

            self._client.capture(
                distinct_id=record.distinct_id,
                event="$ai_generation",
                properties=properties,
            )
        except Exception as e:
            # Observability must never break the chat path.
            logger.error(f"[LLMObservability] Failed to capture generation: {e}")

    @staticmethod
    def _capture_content() -> bool:
        return bool(getattr(settings, "POSTHOG_LLM_CAPTURE_CONTENT", True))

    def shutdown(self) -> None:
        """Flush pending events (call on process teardown if needed)."""
        if self._client is not None:
            try:
                self._client.shutdown()
            except Exception as e:
                logger.error(f"[LLMObservability] Failed to shut down client: {e}")


# Module-level singleton: the PostHog client owns a background sender thread,
# so one instance per process is both sufficient and desirable.
_service_instance: Optional[LLMObservabilityService] = None


def get_llm_observability_service() -> LLMObservabilityService:
    """Factory returning the process-wide LLM observability service."""
    global _service_instance
    if _service_instance is None:
        _service_instance = LLMObservabilityService()
    return _service_instance
