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


class LiteLLMMessageCostTests(TestCase):
    """The chat card reads Message.cost, so proxy calls must populate it."""

    def setUp(self):
        from billing.constants import LiteLLMKeySourceChoice
        from billing.models import LiteLLMKey
        from conversations.models import Conversation, Message
        from users.models import User

        self.user = User.objects.create_user(email="proxy@example.com", password="x")
        self.key = LiteLLMKey.objects.create(
            label="test",
            base_url="https://proxy.example/v1",
            api_key="k",
            source=LiteLLMKeySourceChoice.USER,
            owner_user=self.user,
            created_by=self.user,
        )
        self.conversation = Conversation.active_objects.create(user=self.user)
        self.llm = LLM.objects.create(
            name="Claude Sonnet 4.6",
            identifier="claude-sonnet-4-6",
            provider="claude",
            input_token_rate_per_million=Decimal("3.00"),
            output_token_rate_per_million=Decimal("15.00"),
        )
        self.message = Message.active_objects.create(
            conversation=self.conversation,
            sender_type=2,
            litellm_key=self.key,
            litellm_model_name="us.anthropic.claude-sonnet-4-6",
        )

    def test_cost_is_priced_from_the_matching_dare_model(self):
        from core.services.billing_service import BillingService

        BillingService().finalize_ai_message(
            self.message,
            "hello",
            {"input_tokens": 1_000_000, "output_tokens": 1_000_000},
        )
        self.message.refresh_from_db()
        self.assertEqual(self.message.cost, Decimal("18.000000"))

    def test_cost_stays_zero_when_dare_does_not_offer_the_model(self):
        from core.services.billing_service import BillingService

        self.message.litellm_model_name = "wine-qwen3-coder-next"
        self.message.save()
        BillingService().finalize_ai_message(
            self.message, "hello", {"input_tokens": 100, "output_tokens": 100}
        )
        self.message.refresh_from_db()
        self.assertEqual(self.message.cost, Decimal("0.000000"))
