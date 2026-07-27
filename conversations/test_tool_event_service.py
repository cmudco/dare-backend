from asgiref.sync import async_to_sync
from django.test import SimpleTestCase

from conversations.services.tool_event_service import ToolEventEmitter


class ToolEventEmitterTests(SimpleTestCase):
    def setUp(self):
        self.events = []

        async def capture(payload):
            self.events.append(payload)

        self.emitter = ToolEventEmitter(capture, message_id=42)

    def test_lifecycle_payload_keeps_identity_and_round(self):
        async_to_sync(self.emitter.tool_call_pending)(
            tool_call_id="call-1",
            tool_name="search_documents",
            server_slug="dare",
            origin="dare",
            round_index=2,
        )

        self.assertEqual(
            self.events,
            [
                {
                    "type": "tool_call_pending",
                    "messageId": 42,
                    "toolCallId": "call-1",
                    "toolName": "search_documents",
                    "serverSlug": "dare",
                    "origin": "dare",
                    "round": 2,
                    "status": "pending",
                }
            ],
        )

    def test_result_is_routed_to_the_origin_specific_field(self):
        async_to_sync(self.emitter.tool_call_result)(
            tool_call_id="call-2",
            tool_name="render_document",
            server_slug="quillmark",
            origin="mcp",
            round_index=3,
            status="completed",
            result={"content": [{"type": "text", "text": "Ready"}]},
        )

        event = self.events[0]
        self.assertEqual(event["mcpResult"]["content"][0]["text"], "Ready")
        self.assertNotIn("dareResult", event)
        self.assertNotIn("providerResult", event)

    def test_round_cap_identifies_the_message_and_last_tool_round(self):
        async_to_sync(self.emitter.rounds_capped)(round_index=5)

        self.assertEqual(
            self.events,
            [
                {
                    "type": "tool_rounds_capped",
                    "messageId": 42,
                    "round": 5,
                }
            ],
        )
