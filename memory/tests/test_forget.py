"""Forgetting erases: the statement survives nowhere memory keeps it."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from conversations.constants import SenderType
from conversations.models import Conversation, Message
from memory.constants import MemoryState, WriterAction
from memory.models import MemoryLedgerEntry, MemoryRecord, MemoryTombstone
from memory.services.api_service import clear_store, forget_item
from memory.services.omp_export import export_archive


class ForgetTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            email="forgetter@example.com", password="x"
        )

    def setUp(self):
        self.conversation = Conversation.active_objects.create(
            user=self.user, conversation_id="forget-test-conv"
        )
        self.said = Message.active_objects.create(
            conversation=self.conversation,
            sender_type=SenderType.PLAYER,
            message="I moved from Lahore to Islamabad.",
        )
        self.old = MemoryRecord.objects.create(
            user=self.user,
            kind="fact",
            key="location",
            text="They live in Lahore.",
            state=MemoryState.SUPERSEDED,
        )
        self.new = MemoryRecord.objects.create(
            user=self.user,
            kind="fact",
            key="location",
            text="They live in Islamabad.",
            provenance="I moved from Lahore to Islamabad.",
            replaces=self.old,
            source_conversation=self.conversation,
            source_message=self.said,
        )
        self.old.superseded_by = self.new
        self.old.save(update_fields=["superseded_by"])
        self.kept = MemoryRecord.objects.create(
            user=self.user, kind="fact", key="diet", text="They are vegetarian."
        )
        MemoryLedgerEntry.objects.create(
            user=self.user,
            record=self.new,
            action=WriterAction.SUPERSEDE,
            proposed_action=WriterAction.SUPERSEDE,
            applied=True,
            note='Retired "They live in Lahore." — kept.',
            detail="They live in Islamabad.",
            source_text="I moved from Lahore to Islamabad.",
            source_message=self.said,
            proposal={"text": "They live in Islamabad."},
        )
        self.reply = Message.active_objects.create(
            conversation=self.conversation,
            sender_type=SenderType.AI_ASSISTANT,
            message="Noted.",
            memory_write_data={
                "created": 1,
                "changes": [
                    {
                        "action": "supersede",
                        "recordId": str(self.new.id),
                        "reason": "They said they moved.",
                        "note": 'Retired "They live in Lahore." — kept.',
                        "detail": "They live in Islamabad.",
                    }
                ],
            },
        )

    def everything_memory_keeps(self):
        self.reply.refresh_from_db()
        return " ".join(
            [
                str(list(MemoryRecord.objects.values())),
                str(list(MemoryLedgerEntry.objects.values())),
                str(self.reply.memory_write_data),
                str(export_archive(self.user, include_sensitive=True)),
            ]
        )

    def test_a_forgotten_memory_and_its_earlier_versions_are_erased(self):
        forget_item(self.user, str(self.new.id))

        kept = self.everything_memory_keeps()
        self.assertNotIn("Islamabad", kept)
        self.assertNotIn("Lahore", kept)
        self.assertIn("vegetarian", kept)
        # The transcript is not memory.
        self.said.refresh_from_db()
        self.assertIn("Islamabad", self.said.message)

    def test_forgetting_leaves_a_tombstone_that_travels_without_content(self):
        forget_item(self.user, str(self.new.id))

        self.assertEqual(
            set(MemoryTombstone.objects.values_list("identifier", flat=True)),
            {str(self.new.id), str(self.old.id)},
        )
        archive = export_archive(self.user)
        self.assertEqual(
            {tombstone["identifier"] for tombstone in archive["tombstones"]},
            {str(self.new.id), str(self.old.id)},
        )
        self.assertEqual(
            archive["tombstones"][0]["origin"], archive["manifest"]["origin"]
        )

    def test_forgetting_an_earlier_version_keeps_the_current_one(self):
        forget_item(self.user, str(self.old.id))

        # The current memory keeps its own source sentence, which names both
        # cities; only the earlier statement and the note quoting it are gone.
        kept = self.everything_memory_keeps()
        self.assertNotIn("They live in Lahore.", kept)
        self.assertIn("They live in Islamabad.", kept)
        self.assertTrue(MemoryRecord.objects.filter(pk=self.new.id).exists())

    def test_clearing_everything_leaves_tombstones(self):
        clear_store(self.user)

        self.assertEqual(MemoryRecord.objects.filter(user=self.user).count(), 0)
        self.assertEqual(MemoryTombstone.objects.filter(user=self.user).count(), 3)
