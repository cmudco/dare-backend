import asyncio
from types import SimpleNamespace

from django.test import SimpleTestCase
from django.utils import timezone

from conversations.services.tool_loop_service import ToolLoopService
from core.services.dtos import LLMStreamEvent


class _StalledLLMService:
    async def stream_round(self, prepared, messages, tools):
        await asyncio.sleep(1)
        if False:  # pragma: no cover - makes this an async generator
            yield None


class ToolLoopResilienceTests(SimpleTestCase):
    async def test_silent_provider_stream_times_out(self):
        service = ToolLoopService(_StalledLLMService(), billing_service=None)
        service.stream_idle_timeout_seconds = 0.01

        with self.assertRaisesRegex(TimeoutError, "idle for 0.01 seconds"):
            async for _event in service._stream_round(None, [], None):
                pass

    async def test_empty_stream_retries_once_with_answer_guidance(self):
        class EmptyThenTextLLMService:
            def __init__(self):
                self.calls = []

            async def prepare_chat(self, request):
                return SimpleNamespace(
                    messages=[{"role": "user", "content": "Ambiguous topic"}],
                    tools=None,
                    memory_context=[],
                )

            async def stream_round(self, prepared, messages, tools):
                self.calls.append(messages)
                if len(self.calls) == 1:
                    yield LLMStreamEvent.usage_frame(
                        {"input_tokens": 10, "output_tokens": 1}
                    )
                    return
                yield LLMStreamEvent.text_delta("Recovered answer")

        llm_service = EmptyThenTextLLMService()
        service = ToolLoopService(llm_service, billing_service=None)
        sent = []

        async def send(payload):
            sent.append(payload)

        result = await service.run(
            request=SimpleNamespace(),
            message_obj=SimpleNamespace(id=7, created_at=timezone.now()),
            llm=SimpleNamespace(),
            user=None,
            conversation=SimpleNamespace(),
            send_callback=send,
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
            async def prepare_chat(self, request):
                return SimpleNamespace(
                    messages=[{"role": "user", "content": "format this"}],
                    tools=None,
                    memory_context=[],
                )

            async def stream_round(self, prepared, messages, tools):
                for text in ("Hello", " ", "world", "\n\n", "Done"):
                    yield LLMStreamEvent.text_delta(text)

        service = ToolLoopService(WhitespaceLLMService(), billing_service=None)

        async def send(_payload):
            return None

        result = await service.run(
            request=SimpleNamespace(),
            message_obj=SimpleNamespace(id=8, created_at=timezone.now()),
            llm=SimpleNamespace(),
            user=None,
            conversation=SimpleNamespace(),
            send_callback=send,
            retrieval_scope=None,
        )

        self.assertEqual(result.text, "Hello world\n\nDone")

    async def test_idle_timeout_returns_partial_turn(self):
        # Regression: a stalled stream used to raise out of run(), and the
        # coordinator replaced already-streamed text with a generic failure
        # notice and dropped the accumulated usage (unbilled tokens).
        class TextThenStallLLMService:
            async def prepare_chat(self, request):
                return SimpleNamespace(
                    messages=[{"role": "user", "content": "long question"}],
                    tools=None,
                    memory_context=[],
                )

            async def stream_round(self, prepared, messages, tools):
                yield LLMStreamEvent.text_delta("Partial answer the user saw")
                yield LLMStreamEvent.usage_frame(
                    {"input_tokens": 11, "output_tokens": 7}
                )
                await asyncio.sleep(1)

        service = ToolLoopService(TextThenStallLLMService(), billing_service=None)
        service.stream_idle_timeout_seconds = 0.01

        async def send(_payload):
            return None

        result = await service.run(
            request=SimpleNamespace(),
            message_obj=SimpleNamespace(id=9, created_at=timezone.now()),
            llm=SimpleNamespace(),
            user=None,
            conversation=SimpleNamespace(),
            send_callback=send,
            retrieval_scope=None,
        )

        self.assertTrue(result.timed_out)
        self.assertEqual(result.text, "Partial answer the user saw")
        self.assertEqual(result.token_usage["input_tokens"], 11)
        self.assertEqual(result.token_usage["output_tokens"], 7)
