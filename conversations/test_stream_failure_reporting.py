"""Handled stream failures must still reach Sentry without losing partial output."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import sentry_sdk
from django.test import SimpleTestCase
from django.utils import timezone
from sentry_sdk.integrations.logging import LoggingIntegration

from conversations.services.message_coordinator import MessageCoordinator
from conversations.services.tool_loop_service import ToolLoopResult, ToolLoopService
from conversations.test_tool_loop_resilience import _binding
from core.services.dtos import LLMStreamEvent


class StreamFailureReportingTests(SimpleTestCase):
    async def test_handled_timeout_emits_one_sentry_event_and_keeps_partial_text(self):
        events = []
        client = sentry_sdk.Client(
            dsn="https://public@example.com/1",
            transport=events.append,
            default_integrations=False,
            integrations=[LoggingIntegration()],
        )
        service = ToolLoopService(
            SimpleNamespace(
                prepare_chat=AsyncMock(
                    return_value=SimpleNamespace(
                        messages=[],
                        tools=None,
                        memory_context=[],
                        context_trace=None,
                        llm=SimpleNamespace(
                            identifier="gemini-3.8-flash", provider="gemini"
                        ),
                    )
                )
            )
        )

        async def stalled_round(*args):
            yield LLMStreamEvent.text_delta("Partial answer")
            raise TimeoutError("The model stream was idle for 45 seconds")

        with sentry_sdk.new_scope() as scope:
            scope.set_client(client)
            with patch.object(service, "_stream_round", stalled_round):
                result = await service.run(
                    request=SimpleNamespace(),
                    binding=_binding(
                        SimpleNamespace(id=42, created_at=timezone.now()), AsyncMock()
                    ),
                    retrieval_scope=None,
                )
        client.close()

        self.assertTrue(result.timed_out)
        self.assertEqual(result.text, "Partial answer")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["level"], "error")
        self.assertEqual(events[0]["extra"]["stream_failure"], "timeout")
        self.assertEqual(events[0]["extra"]["turn_id"], "42")
        self.assertEqual(events[0]["extra"]["model"], "gemini-3.8-flash")
        self.assertEqual(events[0]["extra"]["idle_timeout_seconds"], 45)
        self.assertTrue(events[0]["extra"]["has_partial_text"])

    async def test_terminal_empty_response_is_reported_and_finalized(self):
        for tool_calls in (0, 1):
            with self.subTest(tool_calls=tool_calls):
                coordinator = MessageCoordinator.__new__(MessageCoordinator)
                coordinator.user = None
                coordinator.conversation = SimpleNamespace(id=1, bot_id=2)
                coordinator.platform = "SocraticBots"
                coordinator.send = AsyncMock()
                coordinator.billing_service = None
                coordinator._cancellable_message_ids = set()
                coordinator.tool_loop_service = SimpleNamespace(
                    run=AsyncMock(
                        return_value=ToolLoopResult(tool_calls_made=tool_calls)
                    )
                )
                coordinator._save_usage_breakdown = AsyncMock()
                coordinator._finalize_message = AsyncMock()
                request = MagicMock()
                request.requires_audio_transcription.return_value = False
                request.requires_image_generation.return_value = False
                message = SimpleNamespace(id=42, created_at=timezone.now())
                with patch(
                    "conversations.services.message_coordinator.LLMQueryRequestBuilder.from_message_data",
                    return_value=request,
                ), self.assertLogs(
                    "conversations.services.message_coordinator", level="ERROR"
                ) as logs:
                    await coordinator.stream_ai_response(
                        {"message": "test"}, message, SimpleNamespace()
                    )
                self.assertEqual(len(logs.records), 1)
                self.assertEqual(logs.records[0].stream_failure, "empty_response")
                self.assertEqual(logs.records[0].message_id, 42)
                coordinator._finalize_message.assert_awaited_once()
