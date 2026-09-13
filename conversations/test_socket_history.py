import json

from asgiref.sync import async_to_sync
from django.test import TransactionTestCase
from djangorestframework_camel_case.util import camelize

from conversations.api.serializers import MessageSerializer
from conversations.constants import SenderType, ToolCallOrigin
from conversations.models import Conversation, Message, MessageToolCall
from core.services.conversation_service import ConversationService
from users.models import User


class SocketHistoryContractTests(TransactionTestCase):
    """The socket ``conversation_history`` rows are the REST message contract."""

    def setUp(self):
        self.user = User.objects.create_user(
            email="history@example.test", password="test-only"
        )
        self.conversation = Conversation.active_objects.create(
            user=self.user, title="History contract"
        )
        self.question = Message.active_objects.create(
            conversation=self.conversation,
            sender_type=SenderType.PLAYER,
            message="Why is the sky blue?",
        )
        self.answer = Message.active_objects.create(
            conversation=self.conversation,
            sender_type=SenderType.AI_ASSISTANT,
            message="Rayleigh scattering.",
            context_trace={"totalMs": 12, "stages": [{"name": "retrieval"}]},
            retrieval_trace={"source": "documents", "stages": []},
        )
        MessageToolCall.objects.create(
            message=self.answer,
            tool_call_id="call-1",
            tool_name="search_documents",
            server_slug="dare",
            origin=ToolCallOrigin.DARE,
            status="completed",
            round_index=1,
            arguments={"query": "sky"},
            result='{"success": true, "artifact_id": 12}',
        )

    def history(self):
        return async_to_sync(ConversationService().fetch_chat_history_from_db)(
            self.conversation
        )

    def test_rows_match_the_rest_serializer_exactly(self):
        rows = self.history()
        stored = Message.active_objects.filter(
            pk__in=[self.question.pk, self.answer.pk]
        )
        expected = camelize(MessageSerializer(stored.order_by("pk"), many=True).data)

        # Plain-dict comparison: jsonb does not preserve key order.
        self.assertEqual(json.loads(json.dumps(rows)), json.loads(json.dumps(expected)))

    def test_rows_carry_what_both_clients_read(self):
        answer = self.history()[1]

        self.assertEqual(answer["id"], self.answer.id)
        self.assertEqual(answer["senderType"], SenderType.AI_ASSISTANT)
        self.assertIn("senderName", answer)
        self.assertIn("createdAt", answer)
        self.assertEqual(answer["contextTrace"]["totalMs"], 12)
        self.assertEqual(answer["retrievalTrace"]["source"], "documents")
        self.assertEqual(answer["memoryContextData"], [])

        (tool_call,) = answer["toolCalls"]
        self.assertEqual(tool_call["id"], "call-1")
        self.assertEqual(tool_call["toolName"], "search_documents")
        self.assertEqual(tool_call["dareResult"], {"success": True, "artifactId": 12})
        self.assertIsNone(tool_call["mcpResult"])
        self.assertIsNone(tool_call["providerResult"])

    def test_rows_are_oldest_first_and_bounded(self):
        rows = self.history()

        self.assertEqual(
            [row["id"] for row in rows], [self.question.id, self.answer.id]
        )
        limited = async_to_sync(ConversationService().fetch_chat_history_from_db)(
            self.conversation, limit=1
        )
        self.assertEqual([row["id"] for row in limited], [self.answer.id])
