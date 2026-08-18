"""The transcript tool: word-for-word search with neighbor context.

The two properties worth pinning: scope is the server-side user (a
hallucinated argument cannot widen it), and every execution lands in the
ledger so reads share the audit timeline with writes.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

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


class DateBoundTests(TestCase):
    """ "What did we discuss last week" is a real question about a period.

    Without bounds it could only be asked by searching for the words "last
    week", which matches messages that happen to SAY "last week" and never
    messages FROM last week.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            email="dated-search@example.com", password="x"
        )
        cls.conversation = Conversation.active_objects.create(
            user=cls.user, conversation_id="dated-conv"
        )
        cls.old = cls.message("We settled on Postgres for the archive.", days_ago=40)
        cls.recent = cls.message("We settled on Redis for the queue.", days_ago=2)

    @classmethod
    def message(cls, text, days_ago):
        record = Message.active_objects.create(
            conversation=cls.conversation,
            sender_type=SenderType.PLAYER,
            message=text,
        )
        # created_at is auto_now_add, so the age has to be written after.
        stamp = timezone.now() - timedelta(days=days_ago)
        Message.active_objects.filter(pk=record.pk).update(created_at=stamp)
        record.refresh_from_db()
        return record

    def search(self, query="", **bounds):
        return search_sessions_for_user(self.user, query, **bounds)

    def test_a_since_bound_hides_what_came_before_it(self):
        recent_day = (timezone.now() - timedelta(days=7)).date().isoformat()
        result = self.search("settled", since=recent_day)
        self.assertIn("Redis", result["transcript"])
        self.assertNotIn("Postgres", result["transcript"])

    def test_an_until_bound_hides_what_came_after_it(self):
        cutoff = (timezone.now() - timedelta(days=30)).date().isoformat()
        result = self.search("settled", until=cutoff)
        self.assertIn("Postgres", result["transcript"])
        self.assertNotIn("Redis", result["transcript"])

    def test_a_period_can_be_asked_for_with_no_words_at_all(self):
        result = self.search(
            since=(timezone.now() - timedelta(days=7)).date().isoformat()
        )
        self.assertTrue(result["success"])
        self.assertIn("Redis", result["transcript"])

    def test_the_bounds_come_back_so_the_answer_can_say_what_it_searched(self):
        day = (timezone.now() - timedelta(days=7)).date().isoformat()
        self.assertEqual(self.search("settled", since=day)["since"], day)

    def test_a_malformed_date_refuses_rather_than_widening(self):
        """The first contract silently dropped a bad bound and searched on —
        red-teamed, ?since=not-a-date returned the WHOLE history dressed as
        the bounded search that was asked for. A typo in a privacy-adjacent
        search must fail loudly, with the reason readable by both the model
        (tool result) and a person (400 at the API)."""
        result = self.search("settled", since="last tuesday")
        self.assertFalse(result["success"])
        self.assertIn("YYYY-MM-DD", result["error"])

    def test_a_reversed_range_is_refused(self):
        result = self.search("settled", since="2026-08-14", until="2026-08-13")
        self.assertFalse(result["success"])
        self.assertIn("reversed", result["error"])

    def test_an_empty_call_is_corrected_rather_than_answered(self):
        """found=0 for an empty call taught the model "we never talked" —
        it had asked about yesterday with no dates and no words, got a
        clean zero, and reported the conversation history as empty."""
        result = self.search("")
        self.assertFalse(result["success"])
        self.assertIn("since/until", result["error"])
