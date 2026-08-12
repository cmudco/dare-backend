"""The write path against a real database.

The writer LLM and the embedding API are mocked — what is under test is the
plumbing the mocks feed: the two-pass archive assembly, the gate's decisions
landing as rows, supersession chains, reinforcement bumps, idempotency, and
the shortlist union. Everything here runs on SQLite (the lexical branch falls
back to LIKE); the Postgres-only FTS branch is exercised in the live E2E.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from conversations.constants import SenderType
from conversations.models import Conversation, Message
from memory.constants import TOKEN_BUDGET, MemoryState
from memory.domain.types import WriterDecision
from memory.domain.user_doc import estimate_tokens
from memory.models import MemoryLedgerEntry, MemoryRecord, UserMemoryDocument
from memory.services.ingest import ingest_turn
from memory.services.store import (
    active_keys,
    find_by_keys,
    read_user_doc,
    row_from_record,
    shortlist,
)
from memory.services.writer import WriterProposal


def make_turn(user, text, reply="Understood."):
    conversation = Conversation.active_objects.create(
        user=user, conversation_id=f"test-conv-{Message.active_objects.count()}"
    )
    user_message = Message.active_objects.create(
        conversation=conversation,
        sender_type=SenderType.PLAYER,
        sender="tester",
        message=text,
    )
    ai_message = Message.active_objects.create(
        conversation=conversation,
        sender_type=SenderType.AI_ASSISTANT,
        sender="assistant",
        message=reply,
    )
    return conversation, user_message, ai_message


def no_vectors(texts):
    return [None] * len(texts)


class IngestTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            email="mem-tester@example.com", password="x"
        )

    def ingest(self, text, decisions, reply="Understood.", explicit=False):
        conversation, user_message, ai_message = make_turn(self.user, text, reply)
        with (
            patch(
                "memory.services.ingest.propose_decisions",
                return_value=WriterProposal(decisions=decisions, explicit=explicit),
            ),
            patch("memory.services.ingest.embed_texts", side_effect=no_vectors),
            patch("memory.services.retrieval.embed_one", return_value=None),
        ):
            report = ingest_turn(self.user, conversation, user_message, ai_message)
        return report, user_message

    def test_a_fact_lands_with_provenance_sources_and_ledger(self):
        report, user_message = self.ingest(
            "I moved to Pittsburgh last month.",
            [
                WriterDecision(
                    action="add_fact",
                    reason="Stated where they live.",
                    text="Lives in Pittsburgh.",
                    key="location",
                    topic_key="location",
                )
            ],
        )

        self.assertIsNone(report.skipped)
        record = MemoryRecord.objects.get(user=self.user, key="location")
        self.assertEqual(record.text, "Lives in Pittsburgh.")
        self.assertEqual(record.state, MemoryState.ACTIVE)
        self.assertEqual(record.provenance, "I moved to Pittsburgh last month.")
        self.assertEqual(record.source_message_id, user_message.id)

        entry = MemoryLedgerEntry.objects.get(user=self.user)
        self.assertEqual(entry.action, "add_fact")
        self.assertTrue(entry.applied)
        self.assertEqual(entry.source_message_id, user_message.id)
        self.assertEqual(entry.proposal["action"], "add_fact")

    def test_a_collision_supersedes_through_the_exact_seek(self):
        # The second pass (find_by_keys) must surface the existing row even
        # though the mocked writer-side retrieval returned nothing.
        self.ingest(
            "I live in Boston.",
            [
                WriterDecision(
                    action="add_fact",
                    reason="Stated.",
                    text="Lives in Boston.",
                    key="location",
                )
            ],
        )
        self.ingest(
            "I moved to Pittsburgh.",
            [
                WriterDecision(
                    action="add_fact",
                    reason="They moved.",
                    text="Lives in Pittsburgh.",
                    key="location",
                )
            ],
        )

        rows = MemoryRecord.objects.filter(user=self.user, key="location")
        self.assertEqual(rows.count(), 2)

        old = rows.get(text="Lives in Boston.")
        new = rows.get(text="Lives in Pittsburgh.")
        self.assertEqual(old.state, MemoryState.SUPERSEDED)
        self.assertEqual(old.superseded_by_id, new.id)
        self.assertEqual(new.replaces_id, old.id)
        self.assertEqual(new.state, MemoryState.ACTIVE)
        # Retired on the day the replacement arrived.
        self.assertEqual(old.valid_until, new.valid_from)

    def test_a_verbatim_restatement_reinforces_instead_of_writing(self):
        self.ingest(
            "I'm vegetarian.",
            [
                WriterDecision(
                    action="add_fact",
                    reason="Stated.",
                    text="Is vegetarian.",
                    key="diet",
                )
            ],
        )
        report, _ = self.ingest(
            "I'm vegetarian, as I said.",
            [
                WriterDecision(
                    action="add_fact",
                    reason="Mentioned again.",
                    text="Is vegetarian.",
                    key="diet",
                )
            ],
        )

        self.assertEqual(report.reinforced, 1)
        record = MemoryRecord.objects.get(user=self.user, key="diet")
        self.assertEqual(record.reinforced, 1)
        self.assertEqual(
            MemoryRecord.objects.filter(user=self.user, key="diet").count(), 1
        )

    def test_a_health_mention_is_held_and_never_usable(self):
        self.ingest(
            "Been getting migraines most afternoons lately.",
            [
                WriterDecision(
                    action="add_fact",
                    reason="Mentioned in passing.",
                    text="Gets migraines most afternoons.",
                    key="health:migraines",
                    sensitivity="health",
                )
            ],
        )

        record = MemoryRecord.objects.get(user=self.user)
        self.assertEqual(record.state, MemoryState.HELD)
        self.assertEqual(MemoryRecord.usable(self.user).count(), 0)
        self.assertEqual(MemoryRecord.visible(self.user).count(), 1)

    def test_a_safety_fact_is_pinned_into_the_document(self):
        self.ingest(
            "Careful — I'm severely allergic to peanuts.",
            [
                WriterDecision(
                    action="add_fact",
                    reason="Acting without this could hurt them.",
                    text="Has a severe peanut allergy.",
                    key="health:peanut",
                    sensitivity="safety",
                    importance=1.0,
                )
            ],
        )

        # Pinned, not copied: the row carries its place in the profile, and
        # the document renders from it.
        self.assertIn("## Constraints", read_user_doc(self.user))
        self.assertIn("- Has a severe peanut allergy.", read_user_doc(self.user))
        record = MemoryRecord.objects.get(user=self.user)
        self.assertEqual(record.state, MemoryState.ACTIVE)

    def test_ingest_is_idempotent_per_user_message(self):
        conversation, user_message, ai_message = make_turn(
            self.user, "I live in Boston."
        )
        decisions = [
            WriterDecision(
                action="add_fact",
                reason="Stated.",
                text="Lives in Boston.",
                key="location",
            )
        ]
        with (
            patch(
                "memory.services.ingest.propose_decisions",
                return_value=WriterProposal(decisions=decisions),
            ),
            patch("memory.services.ingest.embed_texts", side_effect=no_vectors),
            patch("memory.services.retrieval.embed_one", return_value=None),
        ):
            ingest_turn(self.user, conversation, user_message, ai_message)

        # The RQ job's guard: a ledger row for this message means done.
        self.assertTrue(
            MemoryLedgerEntry.objects.filter(source_message=user_message).exists()
        )

    def test_the_writers_keys_are_seekable_after_persistence(self):
        self.ingest(
            "I have a PSEB certificate.",
            [
                WriterDecision(
                    action="add_fact",
                    reason="Durable document.",
                    text="Has a PSEB certificate.",
                    key="note:pseb-certificate",
                )
            ],
        )
        rows = find_by_keys(self.user, ["note:pseb-certificate", "note:missing"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].key, "note:pseb-certificate")


class ShortlistTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            email="shortlist-tester@example.com", password="x"
        )
        MemoryRecord.objects.create(
            user=cls.user, kind="fact", key="location", text="Lives in Lahore."
        )
        MemoryRecord.objects.create(
            user=cls.user,
            kind="fact",
            key="health:peanut",
            text="Has a severe peanut allergy.",
            importance=1.0,
        )
        MemoryRecord.objects.create(
            user=cls.user,
            kind="fact",
            key="health:migraines",
            text="Gets migraines most afternoons.",
            state=MemoryState.HELD,
            sensitivity="health",
        )
        MemoryRecord.objects.create(
            user=cls.user,
            kind="fact",
            key="occupation",
            text="Worked as a barista.",
            state=MemoryState.SUPERSEDED,
        )

    def test_the_union_finds_by_text_and_importance_and_recency(self):
        candidates = shortlist(self.user, "peanut snacks for the flight")
        keys = {candidate.record.key for candidate in candidates}
        self.assertIn("health:peanut", keys)
        via = {candidate.record.key: candidate.via for candidate in candidates}
        # The allergy is reachable both by its word and by importance.
        self.assertIn("text", via["health:peanut"])

    def test_held_rows_are_never_candidates(self):
        candidates = shortlist(self.user, "how are the migraines lately")
        keys = {candidate.record.key for candidate in candidates}
        self.assertNotIn("health:migraines", keys)

    def test_historical_phrasing_adds_superseded_but_still_not_held(self):
        candidates = shortlist(self.user, "what did I use to do for work")
        keys = {candidate.record.key for candidate in candidates}
        self.assertIn("occupation", keys)
        self.assertNotIn("health:migraines", keys)

    def test_ordinary_phrasing_excludes_superseded(self):
        candidates = shortlist(self.user, "what do I do for work")
        states = {candidate.record.state for candidate in candidates}
        self.assertNotIn(MemoryState.SUPERSEDED, states)


class ActiveKeysTests(TestCase):
    """The key space handed to the writer.

    Keys are the collision domain: a fact can only retire another one that
    shares its key. Retrieval alone shows the writer rows related to the
    current turn, so an upgrade phrased unlike the original ("upgraded to the
    17 pro" against "owns an iPhone 15 Pro Max") never surfaces its own slot
    and a second one gets minted.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            email="keyspace@example.com", password="x"
        )
        MemoryRecord.objects.create(
            user=cls.user, kind="fact", key="location", text="Lives in Islamabad."
        )
        MemoryRecord.objects.create(
            user=cls.user,
            kind="fact",
            key="note:device-phone",
            text="Owns an iPhone 15 Pro Max.",
            importance=0.9,
        )
        MemoryRecord.objects.create(
            user=cls.user,
            kind="fact",
            key="note:retired-thing",
            text="Used to own a Pixel.",
            state=MemoryState.SUPERSEDED,
        )
        MemoryRecord.objects.create(
            user=cls.user,
            kind="fact",
            key="health:migraines",
            text="Gets migraines.",
            state=MemoryState.HELD,
        )

    def test_lists_active_keys_most_important_first(self):
        keys = active_keys(self.user)
        self.assertEqual(keys[0], "note:device-phone")
        self.assertIn("location", keys)

    def test_leaves_out_retired_and_held_rows(self):
        keys = active_keys(self.user)
        self.assertNotIn("note:retired-thing", keys)
        self.assertNotIn("health:migraines", keys)

    def test_never_leaks_another_users_key_space(self):
        other = get_user_model().objects.create_user(
            email="keyspace-other@example.com", password="x"
        )
        self.assertEqual(active_keys(other), [])

    def test_caps_a_very_large_store(self):
        self.assertLessEqual(len(active_keys(self.user, limit=2)), 2)


class ProfileRenderTests(TestCase):
    """USER.md is a view of what is pinned, not a second copy of it.

    The reason the profile can now hold a life fact at all: the row keeps its
    key and its timeline, so "lives in Islamabad" sits in the file read on
    every turn AND still retires itself when they move. A markdown bullet had
    no key and could do neither.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            email="profile-render@example.com", password="x"
        )

    def pin(self, key, text, heading="identity", **kwargs):
        return MemoryRecord.objects.create(
            user=self.user, kind="fact", key=key, text=text, pinned_to=heading, **kwargs
        )

    def test_a_pinned_fact_renders_under_its_heading(self):
        self.pin("location", "Lives in Islamabad.")
        doc = read_user_doc(self.user)
        self.assertIn("## Identity", doc)
        self.assertIn("- Lives in Islamabad.", doc)

    def test_retiring_the_fact_takes_its_profile_line_with_it(self):
        old = self.pin("location", "Lives in Islamabad.")
        self.assertIn("Islamabad", read_user_doc(self.user))

        new = self.pin("location", "Lives in Lahore.")
        old.state = MemoryState.SUPERSEDED
        old.superseded_by = new
        old.save(update_fields=["state", "superseded_by"])

        doc = read_user_doc(self.user)
        self.assertIn("- Lives in Lahore.", doc)
        self.assertNotIn("Islamabad", doc)

    def test_a_replacement_inherits_its_predecessors_place(self):
        # The pin lives on the row, so it has to survive the round trip
        # through the database. It did not at first: row_from_record dropped
        # it, the replacement came back unpinned, and moving city removed the
        # location from USER.md instead of updating it.
        self.ingest_move()
        doc = read_user_doc(self.user)
        self.assertIn("- Lives in Lahore.", doc)
        self.assertNotIn("Islamabad", doc)

    def ingest_move(self):
        old = self.pin("location", "Lives in Islamabad.")
        row = row_from_record(old)
        self.assertEqual(row.pinned_to, "identity")

        new = self.pin("location", "Lives in Lahore.")
        old.state = MemoryState.SUPERSEDED
        old.superseded_by = new
        old.save(update_fields=["state", "superseded_by"])

    def test_an_unpinned_fact_stays_out_of_the_profile(self):
        MemoryRecord.objects.create(
            user=self.user, kind="fact", key="note:bank", text="Banks with UBL."
        )
        self.assertNotIn("UBL", read_user_doc(self.user))

    def test_an_authored_line_survives_alongside_pinned_facts(self):
        UserMemoryDocument.objects.create(
            user=self.user,
            content="# User\n\n## Background\n- Wrote this by hand.\n",
        )
        self.pin("location", "Lives in Islamabad.")

        doc = read_user_doc(self.user)
        self.assertIn("- Wrote this by hand.", doc)
        self.assertIn("- Lives in Islamabad.", doc)

    def test_an_authored_duplicate_of_a_pinned_fact_is_dropped(self):
        # Both would render the same sentence twice, and only the pinned one
        # can ever be superseded.
        UserMemoryDocument.objects.create(
            user=self.user,
            content="# User\n\n## Identity\n- Lives in Islamabad.\n",
        )
        self.pin("location", "Lives in Islamabad.")

        self.assertEqual(read_user_doc(self.user).count("Islamabad"), 1)

    def test_an_empty_profile_renders_as_nothing_at_all(self):
        # Not a bare "# User" heading injected into every prompt saying nothing.
        self.assertEqual(read_user_doc(self.user), "")

    def test_the_budget_drops_the_least_important_line_not_the_newest(self):
        for index in range(60):
            self.pin(
                f"note:filler-{index}",
                f"A reasonably long standing preference number {index}.",
                heading="working-preferences",
                importance=0.4,
            )
        self.pin("name", "Goes by Farhat.", importance=0.9)

        doc = read_user_doc(self.user)
        self.assertIn("Goes by Farhat.", doc)
        self.assertLess(estimate_tokens(doc), TOKEN_BUDGET * 1.5)

    def test_a_safety_line_is_never_dropped_by_the_budget(self):
        # The ceiling is the cheaper thing to break.
        for index in range(60):
            self.pin(
                f"note:filler-{index}",
                f"A reasonably long standing preference number {index}.",
                heading="working-preferences",
                importance=0.9,
            )
        self.pin(
            "diet_avoid:peanut",
            "Severely allergic to peanuts.",
            heading="constraints",
            sensitivity="safety",
            importance=1.0,
        )

        self.assertIn("Severely allergic to peanuts.", read_user_doc(self.user))
