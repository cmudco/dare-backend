from types import SimpleNamespace
from unittest.mock import AsyncMock

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase

from conversations.models import LLM
from core.services.claude_service import ClaudeService


class ClaudeStructuredOutputTests(SimpleTestCase):
    def _service(self, content):
        service = ClaudeService(
            llm=LLM(identifier="claude-haiku-5-5", provider="claude"), api_key="test"
        )
        create = AsyncMock(
            return_value=SimpleNamespace(
                stop_reason="end_turn",
                content=content,
                usage=SimpleNamespace(input_tokens=10, output_tokens=4),
            )
        )
        service._client = SimpleNamespace(
            beta=SimpleNamespace(messages=SimpleNamespace(create=create))
        )
        return service

    def test_json_is_read_from_the_text_block_after_thinking(self):
        service = self._service(
            [
                SimpleNamespace(type="thinking", thinking="Let me decide."),
                SimpleNamespace(type="text", text='{"ok": true}'),
            ]
        )

        result, usage = async_to_sync(service.generate_structured_output_with_usage)(
            [{"role": "user", "content": "Return JSON"}],
            {"type": "object", "properties": {"ok": {"type": "boolean"}}},
        )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(usage, {"input_tokens": 10, "output_tokens": 4})

    def test_a_reply_without_text_is_refused(self):
        service = self._service([SimpleNamespace(type="thinking", thinking="...")])

        with self.assertRaisesRegex(ValueError, "Empty response"):
            async_to_sync(service.generate_structured_output_with_usage)(
                [{"role": "user", "content": "Return JSON"}],
                {"type": "object", "properties": {}},
            )
