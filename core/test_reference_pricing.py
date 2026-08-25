from decimal import Decimal

from django.test import SimpleTestCase, TestCase

from conversations.models import LLM
from core.services.model_identity import candidate_keys
from core.services.reference_pricing import find_reference_llm


class CandidateKeyTests(SimpleTestCase):
    def test_offers_the_vendor_less_spelling_dare_uses(self):
        self.assertEqual(
            candidate_keys("bedrock/us.anthropic.claude-opus-4-6-v1"),
            ("anthropic-claude-opus-4-6", "claude-opus-4-6"),
        )

    def test_single_key_when_there_is_no_vendor_prefix(self):
        self.assertEqual(candidate_keys("gpt-5.6-sol"), ("gpt-5.6",))


class FindReferenceLLMTests(TestCase):
    def setUp(self):
        self.opus = LLM.objects.create(
            name="Claude Opus 4.6",
            identifier="claude-opus-4-6",
            provider="claude",
            input_token_rate_per_million=Decimal("15.00"),
            output_token_rate_per_million=Decimal("75.00"),
        )

    def test_matches_across_the_vendor_namespace(self):
        found = find_reference_llm("wine-claude-opus-4-6")
        self.assertEqual(found, self.opus)

    def test_matches_a_full_deployment_address(self):
        found = find_reference_llm("bedrock/us.anthropic.claude-opus-4-6-v1")
        self.assertEqual(found, self.opus)

    def test_returns_none_when_dare_does_not_offer_the_model(self):
        self.assertIsNone(find_reference_llm("wine-qwen3-coder-next"))
