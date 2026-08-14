"""Export/import: the roundtrip IS the contract.

A bundle that cannot reinstate the store it came from — chains, held rows,
pinned facts, the hand-authored document — is a report, not an export.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from memory.constants import MemoryState, WriterAction
from memory.models import MemoryLedgerEntry, MemoryRecord, UserMemoryDocument
from memory.services.portability import (
    SCHEMA,
    ImportError_,
    export_bundle,
    import_bundle,
)


def no_vectors(texts):
    return [None for _ in texts]


class PortabilityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            email="export-tester@example.com", password="x"
        )
        cls.other = get_user_model().objects.create_user(
            email="import-tester@example.com", password="x"
        )

    def seed(self):
        old = MemoryRecord.objects.create(
            user=self.user,
            kind="fact",
            key="location",
            text="They live in Lahore.",
            state=MemoryState.SUPERSEDED,
            provenance="I live in Lahore",
        )
        new = MemoryRecord.objects.create(
            user=self.user,
            kind="fact",
            key="location",
            text="They live in Islamabad.",
            pinned_to="identity",
            replaces=old,
            importance=0.8,
        )
        old.superseded_by = new
        old.save(update_fields=["superseded_by"])
        MemoryRecord.objects.create(
            user=self.user,
            kind="fact",
            key="health:migraine",
            text="They get migraines.",
            state=MemoryState.HELD,
            sensitivity="health",
        )
        MemoryRecord.objects.create(
            user=self.user,
            kind="procedure",
            key="when:reviewing-code:blunt",
            text="Be blunt.",
            applies_when="Reviewing code they share.",
            importance=0.7,
        )
        UserMemoryDocument.objects.create(
            user=self.user,
            content="# User\n\n## Identity\n- They are called Abbas.\n",
        )

    def test_the_roundtrip_reinstates_the_store_exactly(self):
        self.seed()
        bundle = export_bundle(self.user)
        self.assertEqual(bundle["schema"], SCHEMA)
        self.assertEqual(len(bundle["records"]), 4)

        with patch("memory.services.portability.embed_texts", side_effect=no_vectors):
            result = import_bundle(self.other, bundle)
        self.assertEqual(result["records"], 4)

        rows = MemoryRecord.visible(self.other)
        self.assertEqual(rows.count(), 4)

        # The chain survived, on fresh ids.
        old = rows.get(text="They live in Lahore.")
        new = rows.get(text="They live in Islamabad.")
        self.assertEqual(old.state, MemoryState.SUPERSEDED)
        self.assertEqual(old.superseded_by_id, new.id)
        self.assertEqual(new.replaces_id, old.id)
        self.assertNotEqual(
            str(new.id),
            next(
                r["id"]
                for r in bundle["records"]
                if r["text"] == "They live in Islamabad."
            ),
        )

        # Pinned stayed pinned; held stayed held; the rule kept its firing
        # description; the document came through the normalizer intact.
        self.assertEqual(new.pinned_to, "identity")
        held = rows.get(key="health:migraine")
        self.assertEqual(held.state, MemoryState.HELD)
        rule = rows.get(kind="procedure")
        self.assertEqual(rule.applies_when, "Reviewing code they share.")
        self.assertIn(
            "They are called Abbas",
            UserMemoryDocument.objects.get(user=self.other).content,
        )

        # One honest ledger row for the event — not a replayed history.
        entries = MemoryLedgerEntry.objects.filter(user=self.other)
        self.assertEqual(entries.count(), 1)
        self.assertEqual(entries.first().action, WriterAction.IMPORT)

    def test_a_non_empty_store_refuses_the_import(self):
        MemoryRecord.objects.create(
            user=self.other, kind="fact", key="location", text="Lives somewhere."
        )
        with self.assertRaises(ImportError_):
            import_bundle(self.other, {"schema": SCHEMA, "records": []})

    def test_garbage_is_refused_with_a_readable_reason(self):
        for bundle in [
            "not even a dict",
            {"schema": "dare-export-v1", "records": []},
            {"schema": SCHEMA},
            {"schema": SCHEMA, "records": "nope"},
        ]:
            with self.assertRaises(ImportError_):
                import_bundle(self.user, bundle)

    def test_damaged_rows_are_skipped_not_fatal(self):
        bundle = {
            "schema": SCHEMA,
            "document": "",
            "records": [
                {"id": "a", "kind": "fact", "key": "location", "text": "Lives in Goa."},
                {"id": "b", "kind": "fact", "key": "note:x", "text": ""},
                {
                    "id": "c",
                    "kind": "alien-kind",
                    "key": "note:y",
                    "text": "Owns a boat.",
                    "importance": "not-a-number",
                    "state": "weird",
                    "superseded_by": "missing-id",
                },
            ],
        }
        with patch("memory.services.portability.embed_texts", side_effect=no_vectors):
            result = import_bundle(self.other, bundle)
        self.assertEqual(result["records"], 2)
        coerced = MemoryRecord.visible(self.other).get(text="Owns a boat.")
        self.assertEqual(coerced.kind, "fact")
        self.assertEqual(coerced.state, MemoryState.ACTIVE)
        self.assertEqual(coerced.importance, 0.5)
        self.assertIsNone(coerced.superseded_by_id)

    def test_foreign_paste_becomes_queued_writer_turns(self):
        from conversations.models import Conversation, Message

        from memory.services.portability import FOREIGN_FRAME, import_foreign

        paste = "\n".join(f"- Durable fact number {i}." for i in range(80))
        with patch("memory.tasks.run_memory_writer.delay") as delay:
            result = import_foreign(self.other, paste)

        conversation = Conversation.active_objects.get(
            conversation_id=result["conversation_id"]
        )
        self.assertEqual(conversation.user, self.other)
        self.assertTrue(conversation.memory_enabled)
        turns = Message.active_objects.filter(conversation=conversation)
        # One user+assistant pair per chunk; every assistant message queued
        # on the SAME writer job live turns use — order and idempotency come
        # from the queue, not from new machinery.
        self.assertEqual(turns.count(), result["queued_chunks"] * 2)
        self.assertEqual(delay.call_count, result["queued_chunks"])
        self.assertGreater(result["queued_chunks"], 1)
        first = turns.order_by("id").first()
        self.assertTrue(first.message.startswith(FOREIGN_FRAME))

    def test_foreign_paste_refuses_empty_and_oversized(self):
        from memory.services.portability import FOREIGN_MAX_CHARS, import_foreign

        with self.assertRaises(ImportError_):
            import_foreign(self.other, "   ")
        with self.assertRaises(ImportError_):
            import_foreign(self.other, "x" * (FOREIGN_MAX_CHARS + 1))

    def test_export_scope_is_the_user_alone(self):
        self.seed()
        MemoryRecord.objects.create(
            user=self.other, kind="fact", key="note:z", text="Someone else's fact."
        )
        texts = [r["text"] for r in export_bundle(self.user)["records"]]
        self.assertNotIn("Someone else's fact.", texts)
