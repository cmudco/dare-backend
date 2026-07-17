from types import SimpleNamespace
from unittest.mock import MagicMock

from django.test import SimpleTestCase

from conversations.api.serializers import MessageSerializer, MessageToolCallSerializer
from conversations.constants import ToolCallOrigin
from core.services.conversation_service import ConversationService


class MessageSerializerArtifactTests(SimpleTestCase):
    def test_plural_and_compatibility_artifact_fields_share_one_query(self):
        artifacts = MagicMock()
        values = (
            artifacts.filter.return_value.order_by.return_value.values_list
        )
        values.return_value = [11, 12]
        message = SimpleNamespace(
            conversation_id=3,
            artifacts=artifacts,
        )
        serializer = MessageSerializer()

        self.assertEqual(serializer.get_artifactId(message), "11")
        self.assertEqual(serializer.get_artifactIds(message), [11, 12])
        artifacts.filter.assert_called_once_with(
            is_active=True,
            conversation_id=3,
        )
        values.assert_called_once_with("id", flat=True)

    def test_artifact_ids_use_prefetch_without_querying_relation(self):
        artifacts = MagicMock()
        newer = SimpleNamespace(
            id=12,
            conversation_id=3,
            is_active=True,
            created_at=2,
        )
        older = SimpleNamespace(
            id=11,
            conversation_id=3,
            is_active=True,
            created_at=1,
        )
        message = SimpleNamespace(
            conversation_id=3,
            artifacts=artifacts,
            _prefetched_objects_cache={"artifacts": [newer, older]},
        )

        artifact_ids = MessageSerializer().get_artifactIds(message)

        self.assertEqual(artifact_ids, [11, 12])
        artifacts.filter.assert_not_called()

    def test_tool_call_contract_keeps_arguments_without_raw_result_duplication(self):
        fields = MessageToolCallSerializer.Meta.fields

        self.assertIn("arguments", fields)
        self.assertNotIn("result", fields)
        self.assertNotIn("tool_call_id", fields)

    def test_socket_history_uses_serializer_typed_result(self):
        payload = ConversationService()._build_tool_call_payload(
            {
                "id": "call-1",
                "tool_name": "create_chart",
                "server_slug": "dare",
                "origin": ToolCallOrigin.DARE,
                "status": "completed",
                "round": 1,
                "arguments": {"title": "Trend"},
                "dare_result": {"success": True, "artifactId": 12},
            }
        )

        self.assertEqual(payload["dareResult"]["artifactId"], 12)
        self.assertEqual(payload["arguments"], {"title": "Trend"})
