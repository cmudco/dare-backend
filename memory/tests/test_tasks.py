from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from conversations.constants import SenderType
from conversations.models import Conversation, Message
from memory.services.ingest import IngestReport
from memory.tasks import run_memory_writer


class MemoryWriterTaskTests(TestCase):
    def test_a_skipped_turn_is_still_idempotent(self):
        user = get_user_model().objects.create_user(
            email="task-idempotency@example.com", password="x"
        )
        conversation = Conversation.active_objects.create(
            user=user,
            conversation_id="task-idempotency",
            memory_enabled=True,
        )
        Message.active_objects.create(
            conversation=conversation,
            sender_type=SenderType.PLAYER,
            message="Hello",
        )
        reply = Message.active_objects.create(
            conversation=conversation,
            sender_type=SenderType.AI_ASSISTANT,
            message="Hi",
        )
        report = IngestReport(
            entries=[],
            created=[],
            retired=0,
            reinforced=0,
            profile_changed=False,
            skipped="writer proposed nothing",
        )

        with patch("memory.tasks.close_old_connections"), patch(
            "memory.tasks.USE_POSTGRES", True
        ), patch("memory.tasks.ingest_turn", return_value=report) as ingest:
            run_memory_writer(reply.id)
            run_memory_writer(reply.id)

        self.assertEqual(ingest.call_count, 1)
        reply.refresh_from_db()
        self.assertEqual(reply.memory_write_data["skipped"], "writer proposed nothing")
