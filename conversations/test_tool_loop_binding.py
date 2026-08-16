import json
from types import SimpleNamespace
from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase
from django.utils import timezone

from conversations.services.tool_loop_binding import ChatToolLoopBinding


class ChatToolLoopBindingTests(SimpleTestCase):
    def setUp(self):
        self.sent = []

        async def send(payload):
            self.sent.append(payload)

        self.message = SimpleNamespace(id=42, created_at=timezone.now())
        self.binding = ChatToolLoopBinding(
            message_obj=self.message,
            conversation=SimpleNamespace(),
            user=None,
            llm=SimpleNamespace(),
            send_callback=send,
            billing_service=None,
        )

    def test_correlation_and_turn_key_identify_the_message(self):
        self.assertEqual(self.binding.correlation, {"message_id": 42})
        self.assertEqual(self.binding.store.turn_key, "42")
        self.assertIs(self.binding.store.retrieval_target, self.message)

    def test_save_tool_call_persists_the_history_row(self):
        with patch(
            "conversations.services.tool_loop_binding.MessageToolCall"
        ) as tool_call_model:
            async_to_sync(self.binding.store.save_tool_call)(
                call=SimpleNamespace(id="call-1", name="search_documents"),
                server_slug="dare",
                origin="dare",
                arguments={"query": "pensions"},
                raw_result={"success": True, "snippets": 3},
                is_error=False,
                error="",
                round_index=2,
                execution_time_ms=17,
            )

        kwargs = tool_call_model.objects.create.call_args.kwargs
        self.assertIs(kwargs["message"], self.message)
        self.assertEqual(kwargs["tool_call_id"], "call-1")
        self.assertEqual(kwargs["tool_name"], "search_documents")
        self.assertEqual(kwargs["origin"], "dare")
        self.assertEqual(kwargs["status"], "completed")
        self.assertEqual(kwargs["round_index"], 2)
        self.assertIsNone(kwargs["error"])
        self.assertEqual(json.loads(kwargs["result"]), {"success": True, "snippets": 3})

    def test_save_tool_call_failure_keeps_error_and_drops_result(self):
        with patch(
            "conversations.services.tool_loop_binding.MessageToolCall"
        ) as tool_call_model:
            async_to_sync(self.binding.store.save_tool_call)(
                call=SimpleNamespace(id="call-2", name="quillmark__render"),
                server_slug="quillmark",
                origin="mcp",
                arguments={},
                raw_result={"isError": True},
                is_error=True,
                error="boom",
                round_index=1,
                execution_time_ms=3,
            )

        kwargs = tool_call_model.objects.create.call_args.kwargs
        self.assertEqual(kwargs["status"], "failed")
        self.assertEqual(kwargs["error"], "boom")
        self.assertIsNone(kwargs["result"])

    def test_save_context_trace_updates_only_the_trace_column(self):
        message = SimpleNamespace(
            id=7, created_at=timezone.now(), context_trace=None, saved=None
        )

        def save(update_fields):
            message.saved = update_fields

        message.save = save
        binding = ChatToolLoopBinding(
            message_obj=message,
            conversation=SimpleNamespace(),
            user=None,
            llm=SimpleNamespace(),
            send_callback=self.binding.send_callback,
            billing_service=None,
        )

        async_to_sync(binding.store.save_context_trace)({"stages": [{"name": "rag"}]})

        self.assertEqual(message.context_trace, {"stages": [{"name": "rag"}]})
        self.assertEqual(message.saved, ["context_trace"])

    def test_billing_gate_passes_without_a_user(self):
        can_continue, error = async_to_sync(self.binding.gate.check)(
            {"input_tokens": 10}
        )
        self.assertTrue(can_continue)
        self.assertIsNone(error)

    def test_sink_streams_chat_chunk_format(self):
        async_to_sync(self.binding.sink.text)("Hello world")

        payload = self.sent[0]
        self.assertEqual(payload["type"], "ai_stream")
        self.assertEqual(payload["id"], 42)
        self.assertEqual(payload["message"], "Hello world")
        self.assertFalse(payload["isComplete"])
        self.assertTrue(payload["streaming"])
        self.assertFalse(payload["regenerate"])

    def test_sink_thinking_attaches_summary(self):
        async_to_sync(self.binding.sink.thinking)("Partial", "because reasons")

        payload = self.sent[0]
        self.assertEqual(payload["message"], "Partial")
        self.assertEqual(payload["thinkingSummary"], "because reasons")
