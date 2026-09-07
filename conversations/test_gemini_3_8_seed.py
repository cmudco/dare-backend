from decimal import Decimal
from importlib import import_module

from django.apps import apps
from django.db.migrations.loader import MigrationLoader
from django.test import TestCase

from conversations.models import LLM

seed = import_module("conversations.migrations.0100_seed_gemini_3_8_flash")


class GeminiSeedTests(TestCase):
    def test_seed_is_idempotent_and_preserves_previous_model(self):
        previous = LLM.objects.get(identifier="gemini-3.7-flash")
        previous_active = previous.is_active
        seed.seed_gemini_3_8_flash(apps, None)
        seed.seed_gemini_3_8_flash(apps, None)
        self.assertEqual(LLM.objects.filter(identifier="gemini-3.8-flash").count(), 1)
        model = LLM.objects.get(identifier="gemini-3.8-flash")
        self.assertEqual(model.input_token_rate_per_million, Decimal("0.75"))
        self.assertEqual(model.output_token_rate_per_million, Decimal("3.75"))
        self.assertEqual(model.cached_input_token_rate_per_million, Decimal("0.075"))
        self.assertEqual(model.default_effort, "medium")
        self.assertTrue(model.supports_effort)
        self.assertTrue(model.supports_vision)
        previous.refresh_from_db()
        self.assertEqual(previous.is_active, previous_active)

    def test_reverse_removes_unreferenced_seed_and_can_reapply(self):
        seed.reverse_seed_gemini_3_8_flash(apps, None)
        self.assertFalse(LLM.objects.filter(identifier="gemini-3.8-flash").exists())
        seed.seed_gemini_3_8_flash(apps, None)
        self.assertTrue(LLM.objects.filter(identifier="gemini-3.8-flash").exists())

    def test_single_conversations_migration_leaf(self):
        loader = MigrationLoader(None)
        self.assertEqual(
            loader.graph.leaf_nodes("conversations"),
            [("conversations", "0100_seed_gemini_3_8_flash")],
        )
