"""The transcript tool: word-for-word search with neighbor context.

The two properties worth pinning: scope is the server-side user (a
hallucinated argument cannot widen it), and every execution lands in the
ledger so reads share the audit timeline with writes.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from conversations.constants import SenderType
from conversations.models import Conversation, Message
from memory.models import MemoryLedgerEntry
from memory.services.session_search import search_sessions_for_user


class SessionSearchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            email="session-tester@example.com", password="x"
        )
        cls.other = get_user_model().objects.create_user(
            email="session-other@example.com", password="x"
        )

        conversation = Conversation.active_objects.create(
            user=cls.user, conversation_id="session-conv"
        )
        for sender_type, text in [
            (SenderType.PLAYER, "Should we use SQLite or Postgres for the archive?"),
            (SenderType.AI_ASSISTANT, "Postgres, for pgvector and real FTS."),
            (SenderType.PLAYER, "Alright, agreed — Postgres it is."),
        ]:
            Message.active_objects.create(
                conversation=conversation,
                sender_type=sender_type,
                sender="t",
                message=text,
            )

        stranger_conv = Conversation.active_objects.create(
            user=cls.other, conversation_id="stranger-conv"
        )
        Message.active_objects.create(
            conversation=stranger_conv,
            sender_type=SenderType.PLAYER,
            sender="t",
            message="My secret Postgres password is hunter2.",
        )

    def test_a_hit_arrives_dated_with_its_neighbors(self):
        result = search_sessions_for_user(self.user, "postgres archive decision")

        self.assertTrue(result["success"])
        self.assertGreater(result["found"], 0)
        transcript = result["transcript"]
        # The matched line plus the turn on each side, role-labelled.
        self.assertIn("user: Should we use SQLite or Postgres", transcript)
        self.assertIn("assistant: Postgres, for pgvector", transcript)
        self.assertIn("[2", transcript)  # a [YYYY-MM-DD] stamp

    def test_scope_is_the_user_never_the_arguments(self):
        result = search_sessions_for_user(self.user, "secret password hunter2")
        self.assertNotIn("hunter2", result["transcript"])

    def test_every_search_lands_in_the_ledger(self):
        search_sessions_for_user(self.user, "postgres")
        search_sessions_for_user(self.user, "zebra xylophone quandary")

        entries = list(
            MemoryLedgerEntry.objects.filter(
                user=self.user, action="search_sessions"
            ).order_by("created_at")
        )
        self.assertEqual(len(entries), 2)
        self.assertTrue(entries[0].applied)
        self.assertFalse(entries[1].applied)
        self.assertIn("Nothing in the transcript matched", entries[1].note)

    def test_no_user_degrades_gracefully(self):
        result = search_sessions_for_user(None, "anything")
        self.assertFalse(result["success"])
        self.assertIn("unavailable", result["error"])
