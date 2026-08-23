"""
Tool Loop Service.

The bounded multi-round tool loop at the heart of an LLM turn:

    stream model → collect tool calls → execute → append native tool turns
    → stream again … until the model answers in text or the round cap hits.

Replaces the old hardcoded two-step flow (one tool round, then one
synthesis call with tools stripped and results flattened into a prose user
message). Here the model keeps its tools across rounds, sees results as
provider-native ``role:"tool"`` turns, and can chain calls — search, read,
search again, then chart. The call after ``MAX_TOOL_ROUNDS`` runs with
tools stripped, forcing a final text answer, so termination is guaranteed.

Text accumulates ACROSS rounds (post-tool text appends after a paragraph
break instead of replacing what the user already read), usage accumulates
across rounds for billing, and every tool-call lifecycle moment streams to
the FE through the unified ``ToolEventEmitter`` vocabulary.

The loop is host-agnostic: everything turn-specific — persistence, chunk
formatting, billing — reaches it through a ``ToolLoopBinding`` (chat's is
``ChatToolLoopBinding``; workflows bring their own over ``WorkflowRunStep``).
"""

import asyncio
import logging
import os
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from conversations.constants import MAX_TOOL_ROUNDS
from conversations.services.message_helpers.usage_helpers import \
    UsageAccumulator
from conversations.services.tool_execution_service import (
    ToolExecutionContext, ToolExecutionService, tool_execution_service)
from core.services.dtos import (LLMQueryRequest, StreamEventKind,
                                ToolCallRequest)
from core.services.llm_helpers.tool_turn_helpers import (
    build_assistant_tool_call_turn, build_tool_result_turn,
    synthesize_tool_call_id)
from core.services.tool_loop.binding import ToolLoopBinding
from core.services.tool_loop.events import ToolEventEmitter
from dare_tools.services.retrieval_tool_executor import RetrievalScope

logger = logging.getLogger(__name__)


@dataclass
class ToolLoopResult:
    """Outcome of a full tool-loop turn.

    ``token_usage`` carries the summed token totals plus the extras the
    finalization helpers read (``web_search_sources``,
    ``provider_tool_calls``, ``memory_context``) — the same dict shape the
    single-call flow produced, so finalization is unchanged.
    """

    text: str = ""
    token_usage: Optional[Dict[str, Any]] = None
    usage_breakdown: List[Dict[str, Any]] = field(default_factory=list)
    rounds_used: int = 0
    tool_calls_made: int = 0
    interrupted: bool = False
    timed_out: bool = False
    error_response: Optional[Dict[str, Any]] = None


class ToolLoopService:
    """Runs the bounded tool loop for one host turn."""

    def __init__(self, llm_service) -> None:
        self.llm_service = llm_service
        self.execution_service: ToolExecutionService = tool_execution_service
        self.stream_idle_timeout_seconds = float(
            os.environ.get("LLM_STREAM_IDLE_TIMEOUT_SECONDS", "45")
        )

    async def _stream_round(self, prepared, messages, tools):
        """Yield provider events, failing when a stream goes silent.

        The timeout resets after every event, so healthy long responses are
        unaffected. It specifically bounds the no-first-token / stalled-stream
        case that otherwise leaves an empty assistant placeholder forever.
        """
        iterator = self.llm_service.stream_round(prepared, messages, tools).__aiter__()
        while True:
            try:
                event = await asyncio.wait_for(
                    anext(iterator), timeout=self.stream_idle_timeout_seconds
                )
            except StopAsyncIteration:
                return
            except TimeoutError as exc:
                await iterator.aclose()
                raise TimeoutError(
                    "The model stream was idle for "
                    f"{self.stream_idle_timeout_seconds:g} seconds"
                ) from exc
            yield event

    async def run(
        self,
        request: LLMQueryRequest,
        binding: ToolLoopBinding,
        retrieval_scope: Optional[RetrievalScope],
        regenerate: bool = False,
    ) -> ToolLoopResult:
        """Run the loop and return the finished turn.

        Args:
            request: Built LLMQueryRequest for the turn.
            binding: Host binding — persistence, stream sink, billing gate,
                event correlation (chat message or workflow step).
            retrieval_scope: Attached-source scope for search_documents.
            regenerate: True when regenerating — clears the turn's prior
                tool-call rows so history and the FE never show ghosts.
        """
        turn_key = binding.store.turn_key
        if regenerate:
            await binding.store.clear_prior_tool_calls()

        prepared = await self.llm_service.prepare_chat(request)
        messages: List[Dict[str, Any]] = list(prepared.messages)
        logger.info(
            "[journey] mid=%s prepared: %d prompt turns, %d tools, regenerate=%s",
            turn_key,
            len(messages),
            len(prepared.tools or []),
            regenerate,
        )
        emitter = ToolEventEmitter(binding.send_callback, binding.correlation)
        if prepared.context_trace and prepared.context_trace["stages"]:
            # Persist first, then emit: a client that misses the event (or
            # refreshes) still gets the trace from the turn's payload.
            await binding.store.save_context_trace(prepared.context_trace)
            await emitter.context_trace(prepared.context_trace)
        usage = UsageAccumulator()
        ctx = ToolExecutionContext(
            message=binding.message,
            conversation=binding.conversation,
            user=binding.user,
            send_callback=binding.send_callback,
            emitter=emitter,
            store=binding.store,
            retrieval_scope=retrieval_scope,
            artifact_host=binding.artifact_host,
        )

        result = ToolLoopResult()
        text_accum = ""
        thinking_accum = ""
        web_search_sources: List[Dict] = []
        provider_tool_calls: List[Dict] = []
        sent_provider_ids: set = set()
        empty_stream_retried = False

        # One extra stream attempt is reserved for the initial empty-response
        # recovery. ``round_index`` remains the logical tool round, so a
        # provider anomaly cannot consume the user's bounded tool budget.
        for stream_index in range(1, MAX_TOOL_ROUNDS + 3):
            round_index = stream_index - int(empty_stream_retried)
            if round_index > MAX_TOOL_ROUNDS + 1:
                break
            result.rounds_used = round_index
            tools = prepared.tools if round_index <= MAX_TOOL_ROUNDS else None
            pending_calls: List[ToolCallRequest] = []
            synthesized_ids: deque = deque()
            round_has_text = False
            round_has_thinking = False
            round_thinking = ""
            round_thinking_blocks: List[Dict[str, str]] = []
            logger.info(
                "[journey] mid=%s round %d start (tools=%s)",
                turn_key,
                round_index,
                "on" if tools else "off",
            )

            try:
                async for event in self._stream_round(prepared, messages, tools):
                    if event.kind is StreamEventKind.TEXT_DELTA:
                        if not event.text:
                            continue
                        if not round_has_text and text_accum:
                            # New segment after a tool round: append, never replace.
                            text_accum += "\n\n"
                        round_has_text = True
                        text_accum += event.text
                        await binding.sink.text(text_accum)

                    elif event.kind is StreamEventKind.THINKING_DELTA:
                        if not event.text:
                            continue
                        if not round_has_thinking and thinking_accum:
                            thinking_accum += "\n\n"
                        round_has_thinking = True
                        round_thinking += event.text
                        thinking_accum += event.text
                        await binding.sink.thinking(text_accum, thinking_accum)

                    elif event.kind is StreamEventKind.THINKING_BLOCK_READY:
                        if event.provider_thinking_block:
                            round_thinking_blocks.append(event.provider_thinking_block)

                    elif event.kind is StreamEventKind.TOOL_CALL_START:
                        call_id = event.tool_call_id
                        if not call_id:
                            # Providers without call ids (Gemini): synthesize one
                            # and hand it to the matching READY via FIFO order.
                            call_id = synthesize_tool_call_id(
                                turn_key, round_index, len(synthesized_ids)
                            )
                            synthesized_ids.append(call_id)
                        origin, server_slug, _ = ToolExecutionService._classify(
                            event.tool_name or ""
                        )
                        await emitter.tool_call_pending(
                            call_id,
                            event.tool_name or "",
                            server_slug,
                            origin,
                            round_index,
                        )

                    elif event.kind is StreamEventKind.TOOL_CALL_ARGS_DELTA:
                        progress_id = event.tool_call_id or (
                            synthesized_ids[-1] if synthesized_ids else ""
                        )
                        if progress_id:
                            await emitter.args_progress(progress_id, event.args_len)

                    elif (
                        event.kind is StreamEventKind.TOOL_CALL_READY
                        and event.tool_call
                    ):
                        call = event.tool_call
                        if not call.id:
                            call = call.with_id(
                                synthesized_ids.popleft()
                                if synthesized_ids
                                else synthesize_tool_call_id(
                                    turn_key, round_index, len(pending_calls)
                                )
                            )
                        pending_calls.append(call)

                    elif event.kind is StreamEventKind.USAGE and event.usage:
                        observed_usage = dict(event.usage)
                        if round_thinking:
                            observed_usage["thinking_summary"] = round_thinking
                        usage.observe(round_index, observed_usage)
                        if event.usage.get("web_search_sources"):
                            web_search_sources = event.usage["web_search_sources"]
                        for provider_call in (
                            event.usage.get("provider_tool_calls") or []
                        ):
                            await self._emit_provider_tool_call(
                                emitter, provider_call, round_index, sent_provider_ids
                            )
                            provider_tool_calls.append(provider_call)

                        can_continue, error_response = await binding.gate.check(
                            usage.totals()
                        )
                        if not can_continue:
                            result.interrupted = True
                            result.error_response = error_response
                            break

            except TimeoutError as exc:
                # Return the partial turn instead of raising: text the user
                # already read, usage already billed, and tool work already
                # persisted must survive a stalled provider stream.
                result.timed_out = True
                logger.warning(
                    "[ToolLoopService] %s (round %s, turn %s) — "
                    "finishing with the partial turn",
                    exc,
                    round_index,
                    turn_key,
                )
                break

            logger.info(
                "[journey] mid=%s round %d stream done: text=%d chars, "
                "pending_calls=%s, interrupted=%s",
                turn_key,
                round_index,
                len(text_accum),
                [call.name for call in pending_calls],
                result.interrupted,
            )
            if result.interrupted:
                break
            if not pending_calls:
                if not round_has_text and not text_accum and not empty_stream_retried:
                    # Anthropic can occasionally close a successful HTTP 200
                    # stream with usage but no content block. Retry once with
                    # the identical prepared turn; this is safe because no
                    # text was emitted and no tool was executed.
                    empty_stream_retried = True
                    logger.warning(
                        "[ToolLoopService] Provider returned an empty stream; "
                        "retrying the round once with explicit answer guidance."
                    )
                    messages = [dict(message) for message in messages]
                    if messages and messages[-1].get("role") == "user":
                        prior_content = messages[-1].get("content") or ""
                        if isinstance(prior_content, str):
                            messages[-1]["content"] = (
                                f"{prior_content}\n\n"
                                "Important: return a substantive answer. Do not "
                                "end the turn with empty content. If the request "
                                "is ambiguous, state your interpretation and "
                                "answer it directly."
                            )
                    continue
                break  # The model answered in text — the turn is done.

            messages.append(
                build_assistant_tool_call_turn(
                    self._round_text(round_has_text, text_accum),
                    pending_calls,
                    provider_thinking_blocks=round_thinking_blocks,
                )
            )
            tool_results = await self.execution_service.execute_round(
                pending_calls, ctx, round_index
            )
            messages.extend(
                build_tool_result_turn(tool_result) for tool_result in tool_results
            )
            result.tool_calls_made += len(pending_calls)

            if round_index == MAX_TOOL_ROUNDS:
                logger.info(
                    "[journey] mid=%s round cap hit — next round forces a "
                    "text answer",
                    turn_key,
                )
                await emitter.rounds_capped(round_index)

        token_usage = usage.totals() if usage.has_usage() else {}
        if web_search_sources:
            token_usage["web_search_sources"] = web_search_sources
        if provider_tool_calls:
            token_usage["provider_tool_calls"] = provider_tool_calls
        if prepared.memory_context:
            token_usage["memory_context"] = prepared.memory_context

        result.text = text_accum
        result.token_usage = token_usage or None
        logger.info(
            "[journey] mid=%s loop done: rounds=%d, tool_calls=%d, "
            "text=%d chars, tokens=%s/%s, interrupted=%s, timed_out=%s",
            turn_key,
            result.rounds_used,
            result.tool_calls_made,
            len(text_accum),
            (token_usage or {}).get("input_tokens"),
            (token_usage or {}).get("output_tokens"),
            result.interrupted,
            result.timed_out,
        )
        result.usage_breakdown = usage.breakdown()
        return result

    # ========== Helpers ==========

    @staticmethod
    def _round_text(round_has_text: bool, text_accum: str) -> str:
        """Text for the assistant tool-call turn — this round's segment only."""
        if not round_has_text:
            return ""
        # The last segment after the final "\n\n" is this round's text.
        return text_accum.rsplit("\n\n", 1)[-1] if "\n\n" in text_accum else text_accum

    @staticmethod
    async def _emit_provider_tool_call(
        emitter: ToolEventEmitter,
        provider_call: Dict[str, Any],
        round_index: int,
        sent_ids: set,
    ) -> None:
        """Emit a provider-executed tool (web fetch, url context) as a result event."""
        call_id = provider_call.get("id") or ""
        if not call_id or call_id in sent_ids:
            return
        sent_ids.add(call_id)
        await emitter.tool_call_result(
            call_id,
            provider_call.get("name", ""),
            provider_call.get("provider", "provider"),
            "provider",
            round_index,
            status=provider_call.get("status", "completed"),
            result=provider_call.get("result"),
        )
