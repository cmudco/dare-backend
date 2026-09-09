"""Tests for the dedupe_legacy_slug_pairs management command.

Local and stage have no real legacy pairs, so these tests are the only exercise
of the write path before it runs against prod. They build two of the real pairs
(one linked, one unlinked) in the test database and assert the full behavior:
dry-run is inert, execute transfers/deletes, a second execute is a no-op, and a
survivor that already holds a link causes a clean refusal.
"""

from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from conversations.constants import ModelReasoningLevel
from conversations.models import LLM, ModelCardData

# Two real pairs from the command's hardcoded table.
LINKED = ("gpt-41", "gpt-4-1")
UNLINKED = ("gpt-51", "gpt-5-1")

# Non-colliding LLM identifiers for the fixtures we create.
PRIMARY_LLM_ID = "test-dedupe-primary-llm"
OTHER_LLM_ID = "test-dedupe-other-llm"


def make_card(slug, *, llm=None, reasoning=ModelReasoningLevel.NONE):
    return ModelCardData.objects.create(
        name=slug,
        slug=slug,
        provider_name="test-provider",
        llm=llm,
        reasoning_level=reasoning,
    )


class DedupeLegacySlugPairsTests(TestCase):
    def setUp(self):
        ModelCardData.objects.filter(
            slug__in=[LINKED[0], LINKED[1], UNLINKED[0], UNLINKED[1]]
        ).delete()
        LLM.objects.filter(identifier__in=[PRIMARY_LLM_ID, OTHER_LLM_ID]).delete()

        # Linked pair: loser holds the LLM link and a real reasoning_level.
        self.llm = LLM.objects.create(name="Primary", identifier=PRIMARY_LLM_ID)
        self.linked_loser = make_card(
            LINKED[0],
            llm=self.llm,
            reasoning=ModelReasoningLevel.COST_UNCONSTRAINED,
        )
        self.linked_survivor = make_card(LINKED[1])

        # Unlinked pair: loser has a real reasoning_level, no link either side.
        self.unlinked_loser = make_card(
            UNLINKED[0], reasoning=ModelReasoningLevel.COST_PREDICTABLE
        )
        self.unlinked_survivor = make_card(UNLINKED[1])

    def _run(self, *args):
        out = StringIO()
        call_command("dedupe_legacy_slug_pairs", *args, stdout=out, stderr=out)
        return out.getvalue()

    def test_dry_run_changes_nothing(self):
        self._run()

        # Losers still present.
        self.assertTrue(ModelCardData.objects.filter(slug=LINKED[0]).exists())
        self.assertTrue(ModelCardData.objects.filter(slug=UNLINKED[0]).exists())

        # Survivors untouched.
        self.linked_survivor.refresh_from_db()
        self.unlinked_survivor.refresh_from_db()
        self.assertIsNone(self.linked_survivor.llm_id)
        self.assertEqual(self.linked_survivor.reasoning_level, ModelReasoningLevel.NONE)
        self.assertEqual(
            self.unlinked_survivor.reasoning_level, ModelReasoningLevel.NONE
        )

        # LLM link still on the loser.
        self.linked_loser.refresh_from_db()
        self.assertEqual(self.linked_loser.llm_id, self.llm.id)

    def test_execute_transfers_and_deletes(self):
        self._run("--execute")

        # Losers deleted.
        self.assertFalse(ModelCardData.objects.filter(slug=LINKED[0]).exists())
        self.assertFalse(ModelCardData.objects.filter(slug=UNLINKED[0]).exists())

        # Linked survivor received the link and the loser's reasoning_level.
        self.linked_survivor.refresh_from_db()
        self.assertEqual(self.linked_survivor.llm_id, self.llm.id)
        self.assertEqual(
            self.linked_survivor.reasoning_level,
            ModelReasoningLevel.COST_UNCONSTRAINED,
        )

        # LLM's OneToOne now resolves to the survivor.
        self.llm.refresh_from_db()
        self.assertEqual(self.llm.model_card_data.slug, LINKED[1])

        # Unlinked survivor carried the loser's reasoning_level.
        self.unlinked_survivor.refresh_from_db()
        self.assertEqual(
            self.unlinked_survivor.reasoning_level,
            ModelReasoningLevel.COST_PREDICTABLE,
        )

    def test_second_execute_is_noop(self):
        self._run("--execute")
        output = self._run("--execute")
        self.assertIn("0 rows changed", output)

        # State unchanged by the second run.
        self.linked_survivor.refresh_from_db()
        self.assertEqual(self.linked_survivor.llm_id, self.llm.id)
        self.assertFalse(ModelCardData.objects.filter(slug=LINKED[0]).exists())

    def test_preexisting_survivor_link_refuses_with_no_writes(self):
        # Survivor of the linked pair already holds a link to another LLM.
        other = LLM.objects.create(name="Other", identifier=OTHER_LLM_ID)
        self.linked_survivor.llm = other
        self.linked_survivor.save(update_fields=["llm"])

        with self.assertRaises(CommandError):
            self._run("--execute")

        # No writes: loser intact, both links unchanged.
        self.assertTrue(ModelCardData.objects.filter(slug=LINKED[0]).exists())
        self.linked_loser.refresh_from_db()
        self.linked_survivor.refresh_from_db()
        self.assertEqual(self.linked_loser.llm_id, self.llm.id)
        self.assertEqual(self.linked_survivor.llm_id, other.id)
        # The unlinked pair must also be untouched by the refused run.
        self.assertTrue(ModelCardData.objects.filter(slug=UNLINKED[0]).exists())
