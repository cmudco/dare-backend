import asyncio
from types import SimpleNamespace

from django.test import SimpleTestCase
from django.utils import timezone

from conversations.services.tool_loop_binding import ChatToolLoopBinding
from conversations.services.tool_loop_service import ToolLoopService
from core.services.dtos import (
    LLMStreamEvent,
    ToolCallRequest,
    ToolCallResult,
    ToolLoopConfig,
)


def _binding(message_obj, send_callback):
    return ChatToolLoopBinding(
        message_obj=message_obj,
        conversation=SimpleNamespace(),
        user=None,
        llm=SimpleNamespace(),
        send_callback=send_callback,
        billing_service=None,
    )


class _StalledLLMService:
    async def stream_round(self, prepared, messages, tools, allow_tool_calls=True):
        await asyncio.sleep(1)
        if False:  # pragma: no cover - makes this an async generator
            yield None


class ToolLoopResilienceTests(SimpleTestCase):
    async def test_silent_provider_stream_times_out(self):
        service = ToolLoopService(_StalledLLMService())
        service.stream_idle_timeout_seconds = 0.01

        with self.assertRaisesRegex(TimeoutError, "idle for 0.01 seconds"):
            async for _event in service._stream_round(None, [], None):
                pass

    async def test_empty_stream_retries_once_with_answer_guidance(self):
        class EmptyThenTextLLMService:
            def __init__(self):
                self.calls = []

            async def prepare_chat(self, request, messages=None):
                return SimpleNamespace(
                    messages=[{"role": "user", "content": "Ambiguous topic"}],
                    tools=None,
                    memory_context=[],
                    context_trace=None,
                )

            async def stream_round(
                self, prepared, messages, tools, allow_tool_calls=True
            ):
                self.calls.append(messages)
                if len(self.calls) == 1:
                    yield LLMStreamEvent.usage_frame(
                        {"input_tokens": 10, "output_tokens": 1}
                    )
                    return
                yield LLMStreamEvent.text_delta("Recovered answer")

        llm_service = EmptyThenTextLLMService()
        service = ToolLoopService(llm_service)
        sent = []

        async def send(payload):
            sent.append(payload)

        result = await service.run(
            request=SimpleNamespace(),
            binding=_binding(SimpleNamespace(id=7, created_at=timezone.now()), send),
            retrieval_scope=None,
        )

        self.assertEqual(result.text, "Recovered answer")
        self.assertEqual(result.rounds_used, 1)
        self.assertEqual(len(llm_service.calls), 2)
        self.assertIn(
            "return a substantive answer",
            llm_service.calls[1][-1]["content"],
        )
        self.assertEqual(len(sent), 1)

    async def test_stream_preserves_whitespace_only_deltas(self):
        class WhitespaceLLMService:
            async def prepare_chat(self, request, messages=None):
                return SimpleNamespace(
                    messages=[{"role": "user", "content": "format this"}],
                    tools=None,
                    memory_context=[],
                    context_trace=None,
                )

            async def stream_round(
                self, prepared, messages, tools, allow_tool_calls=True
            ):
                for text in ("Hello", " ", "world", "\n\n", "Done"):
                    yield LLMStreamEvent.text_delta(text)

        service = ToolLoopService(WhitespaceLLMService())

        async def send(_payload):
            return None

        result = await service.run(
            request=SimpleNamespace(),
            binding=_binding(SimpleNamespace(id=8, created_at=timezone.now()), send),
            retrieval_scope=None,
        )

        self.assertEqual(result.text, "Hello world\n\nDone")

    async def test_idle_timeout_returns_partial_turn(self):
        # Regression: a stalled stream used to raise out of run(), and the
        # coordinator replaced already-streamed text with a generic failure
        # notice and dropped the accumulated usage (unbilled tokens).
        class TextThenStallLLMService:
            async def prepare_chat(self, request, messages=None):
                return SimpleNamespace(
                    messages=[{"role": "user", "content": "long question"}],
                    llm=SimpleNamespace(identifier="test-model", provider="gemini"),
                    tools=None,
                    memory_context=[],
                    context_trace=None,
                )

            async def stream_round(
                self, prepared, messages, tools, allow_tool_calls=True
            ):
                yield LLMStreamEvent.text_delta("Partial answer the user saw")
                yield LLMStreamEvent.usage_frame(
                    {"input_tokens": 11, "output_tokens": 7}
                )
                await asyncio.sleep(1)

        service = ToolLoopService(TextThenStallLLMService())
        service.stream_idle_timeout_seconds = 0.01

        async def send(_payload):
            return None

        result = await service.run(
            request=SimpleNamespace(),
            binding=_binding(SimpleNamespace(id=9, created_at=timezone.now()), send),
            retrieval_scope=None,
        )

        self.assertTrue(result.timed_out)
        self.assertEqual(result.text, "Partial answer the user saw")
        self.assertEqual(result.token_usage["input_tokens"], 11)
        self.assertEqual(result.token_usage["output_tokens"], 7)


class ToolLoopBudgetTests(SimpleTestCase):
    async def test_call_budget_forbids_more_calls_and_forces_an_answer(self):
        class AlwaysSearchesLLMService:
            def __init__(self):
                self.rounds = []

            async def prepare_chat(self, request, messages=None):
                return SimpleNamespace(
                    messages=[{"role": "user", "content": "Find it"}],
                    tools=[{"type": "function"}],
                    memory_context=[],
                    context_trace=None,
                )

            async def stream_round(
                self, prepared, messages, tools, allow_tool_calls=True
            ):
                self.rounds.append((tools, allow_tool_calls))
                if not allow_tool_calls:
                    yield LLMStreamEvent.text_delta("Not in the docs.")
                    return
                # Two parallel searches every round it is allowed to.
                for index in range(2):
                    yield LLMStreamEvent.tool_call_ready(
                        ToolCallRequest(id=f"c{index}", name="search", arguments="{}")
                    )

        llm_service = AlwaysSearchesLLMService()
        result = await _run_budgeted(llm_service)

        # Rounds 1-2 spend 4 calls (budget 3), so round 3 may not call. The
        # tools themselves never leave the request.
        tools = [{"type": "function"}]
        self.assertEqual(
            llm_service.rounds, [(tools, True), (tools, True), (tools, False)]
        )
        self.assertEqual(result.tool_calls_made, 4)
        self.assertEqual(result.text, "Not in the docs.")

    async def test_a_call_made_after_the_budget_is_spent_is_not_run(self):
        class NeverStopsLLMService:
            async def prepare_chat(self, request, messages=None):
                return SimpleNamespace(
                    messages=[{"role": "user", "content": "Find it"}],
                    tools=[{"type": "function"}],
                    memory_context=[],
                    context_trace=None,
                )

            async def stream_round(
                self, prepared, messages, tools, allow_tool_calls=True
            ):
                for index in range(2):
                    yield LLMStreamEvent.tool_call_ready(
                        ToolCallRequest(id=f"c{index}", name="search", arguments="{}")
                    )

        result = await _run_budgeted(NeverStopsLLMService())

        self.assertEqual(result.tool_calls_made, 4)


class _CountingExecutor:
    async def execute_round(self, calls, ctx, round_index):
        return [
            ToolCallResult(
                tool_call_id=call.id,
                tool_name=call.name,
                origin="dare",
                server_slug="dare",
                content="nothing",
            )
            for call in calls
        ]


async def _run_budgeted(llm_service):
    service = ToolLoopService(
        llm_service, ToolLoopConfig(max_rounds=5, max_tool_calls=3)
    )
    service.execution_service = _CountingExecutor()

    async def send(payload):
        pass

    return await service.run(
        request=SimpleNamespace(),
        binding=_binding(SimpleNamespace(id=8, created_at=timezone.now()), send),
        retrieval_scope=None,
    )
