"""The read path: what one turn's prompt actually carries.

Embeddings are mocked to None — on SQLite the lexical fallback and the weight
redistribution carry retrieval, which is exactly the degraded mode the read
path must survive.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from memory.constants import MemoryState
from memory.models import MemoryRecord, UserMemoryDocument
from memory.services.context import read_context

DOC = """# User

## Identity
- Preferred name: Farhat.

## Communication
- Prefers direct explanations.
"""


class ReadContextTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            email="context-tester@example.com", password="x"
        )
        UserMemoryDocument.objects.create(user=cls.user, content=DOC)
        MemoryRecord.objects.create(
            user=cls.user, kind="fact", key="location", text="Lives in Lahore."
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
            kind="procedure",
            key="when:writing-commit-messages:never-use-emoji",
            text="Never use emoji.",
            importance=0.7,
        )

    def read(self, question):
        with patch("memory.services.context.embed_one", return_value=None):
            return read_context(self.user, question)

    def test_all_three_layers_arrive_framed_and_separated(self):
        context = self.read("help me write a commit message about where I live")

        self.assertIn("<user_md>", context.block)
        self.assertIn("- Preferred name: Farhat.", context.block)
        self.assertIn("<retrieved_memories>", context.block)
        self.assertIn("Lives in Lahore.", context.block)
        self.assertIn("<procedures>", context.block)
        self.assertIn("- When writing commit messages: Never use emoji.", context.block)
        # Rules are framed as rules, not information.
        self.assertIn("Follow them silently", context.block)

    def test_a_held_row_never_reaches_the_prompt(self):
        context = self.read("how have the migraines been")
        self.assertNotIn("migraines most afternoons", context.block)
        self.assertNotIn(
            "migraines", " ".join(item["content"] for item in context.items)
        )

    def test_display_items_carry_every_layer_with_the_panel_shape(self):
        context = self.read("commit message help for where I live")
        types = {item["memory_type"] for item in context.items}
        self.assertEqual(types, {"profile", "knowledge", "behavior"})
        for item in context.items:
            self.assertEqual(set(item.keys()), {"content", "memory_type", "categories"})

    def test_the_question_is_embedded_exactly_once_per_turn(self):
        with patch(
            "memory.services.context.embed_one", return_value=[0.0] * 8
        ) as context_embed, patch(
            "memory.services.retrieval.embed_one"
        ) as retrieval_embed:
            read_context(self.user, "where do I live and how should I commit")

        self.assertEqual(context_embed.call_count, 1)
        # The funnels must reuse that vector, never embed again.
        retrieval_embed.assert_not_called()

    def test_a_failed_embedding_is_not_retried_by_each_funnel(self):
        # None is ambiguous — "nobody embedded yet" vs "embedding failed".
        # Without embed_query=False, a failed embed on a flaky network fires
        # two more calls on the same turn.
        with patch(
            "memory.services.context.embed_one", return_value=None
        ) as context_embed, patch(
            "memory.services.retrieval.embed_one"
        ) as retrieval_embed:
            context = read_context(self.user, "where do I live")

        self.assertEqual(context_embed.call_count, 1)
        retrieval_embed.assert_not_called()
        # And the turn still works, ranked on words and importance alone.
        self.assertIn("Lives in Lahore.", context.block)

    def test_an_empty_store_yields_an_empty_block(self):
        stranger = get_user_model().objects.create_user(
            email="empty-store@example.com", password="x"
        )
        with patch("memory.services.context.embed_one", return_value=None):
            context = read_context(stranger, "anything at all")
        self.assertEqual(context.block, "")
        self.assertEqual(context.items, [])
