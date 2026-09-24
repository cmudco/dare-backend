"""Secondary failures remain recoverable and emit diagnosable Sentry events."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import sentry_sdk
from django.test import SimpleTestCase
from sentry_sdk.integrations.logging import LoggingIntegration

from conversations.namespaces.chat import ChatNamespace
from conversations.services.message_coordinator import MessageCoordinator
from conversations.services.tool_loop_binding import ChatToolLoopStore
from conversations.services.web_search_source_service import WebSearchSourceService
from core.services.claude_service import ClaudeService
from core.services.custom_llm_service import CustomLLMService
from core.services.gemini_service import GeminiService
from core.services.openai_service import OpenAIService


class SecondaryFailureReportingTests(SimpleTestCase):
    def setUp(self):
        self.events = []
        self.client = sentry_sdk.Client(
            dsn="https://public@example.com/1",
            transport=self.events.append,
            default_integrations=False,
            integrations=[LoggingIntegration()],
        )
        self.scope = sentry_sdk.new_scope()
        self.scope.__enter__().set_client(self.client)
        self.addCleanup(self.client.close)
        self.addCleanup(self.scope.__exit__, None, None, None)

    def assert_exception_reported(self):
        self.assertEqual(len(self.events), 1)
        event = self.events.pop()
        self.assertEqual(event["level"], "error")
        self.assertEqual(event["exception"]["values"][-1]["type"], "RuntimeError")
        self.assertIn("stacktrace", event["exception"]["values"][-1])
        return event

    async def test_usage_save_failure_is_reported_without_interrupting_chat(self):
        coordinator = MessageCoordinator.__new__(MessageCoordinator)
        message = SimpleNamespace(id=42, pk=42)
        with patch(
            "conversations.services.message_coordinator.Message._base_manager.filter",
            side_effect=RuntimeError("DB failure"),
        ):
            await coordinator._save_usage_breakdown(message, [{"round_index": 1}])
        self.assertEqual(self.assert_exception_reported()["extra"]["message_id"], 42)

    async def test_citation_save_failure_is_reported(self):
        with patch(
            "conversations.services.web_search_source_service.WebSearchSource.active_objects.create",
            side_effect=RuntimeError("DB failure"),
        ):
            count = await WebSearchSourceService.save_sources(
                SimpleNamespace(id=42), [{"url": "https://example.com"}]
            )
        self.assertEqual(count, 0)
        self.assert_exception_reported()

    async def test_provider_cleanup_failures_are_reported(self):
        for provider in (GeminiService, ClaudeService, OpenAIService, CustomLLMService):
            with self.subTest(provider=provider.__name__):
                service = provider.__new__(provider)
                client = SimpleNamespace(
                    close=AsyncMock(side_effect=RuntimeError("Close failure")),
                    aio=SimpleNamespace(
                        aclose=AsyncMock(side_effect=RuntimeError("Close failure"))
                    ),
                )
                if provider is CustomLLMService:
                    service.client = client
                else:
                    service._client = client
                await service.close()
                self.assert_exception_reported()

    async def test_unexpected_send_failure_is_reported_but_disconnect_is_not(self):
        coordinator = MessageCoordinator.__new__(MessageCoordinator)
        for error in (RuntimeError("Send failure"), ConnectionError("Disconnected")):
            coordinator.send_callback = AsyncMock(side_effect=error)
            await coordinator.send({"type": "message"})
            if isinstance(error, ConnectionError):
                self.assertEqual(self.events, [])
            else:
                self.assert_exception_reported()

    async def test_socket_emit_failure_is_reported_once(self):
        callback = ChatNamespace()._create_send_callback("socket", "conversation")
        coordinator = MessageCoordinator.__new__(MessageCoordinator)
        coordinator.send_callback = callback
        with patch(
            "conversations.namespaces.chat.sio.emit",
            side_effect=RuntimeError("Emit failure"),
        ):
            await coordinator.send({"type": "message"})
        self.assert_exception_reported()

    async def test_tool_call_save_failure_includes_traceback(self):
        store = ChatToolLoopStore(SimpleNamespace(id=42))
        with patch(
            "conversations.services.tool_loop_binding.MessageToolCall.objects.create",
            side_effect=RuntimeError("DB failure"),
        ):
            await store.save_tool_call(
                call=SimpleNamespace(id="call", name="tool"),
                server_slug="server",
                origin="mcp",
                arguments={},
                raw_result={},
                is_error=False,
                error="",
                round_index=1,
                execution_time_ms=1,
            )
        self.assert_exception_reported()

    async def test_gemini_stream_failure_includes_traceback_and_recovery_text(self):
        service = GeminiService.__new__(GeminiService)
        service._prepare_messages = Mock(return_value=[])
        service._create_stream = AsyncMock(side_effect=RuntimeError("Provider failure"))
        chunks = [chunk async for chunk in service.stream_chat_completion([])]
        self.assertEqual(len(chunks), 1)
        self.assertTrue(chunks[0].text.startswith("Error:"))
        self.assert_exception_reported()
