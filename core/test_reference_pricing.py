from decimal import Decimal

from django.test import SimpleTestCase, TestCase

from conversations.models import LLM
from core.services.model_identity import pricing_keys
from core.services.reference_pricing import reference_rates


class PricingKeyTests(SimpleTestCase):
    def test_offers_the_vendor_less_spelling_dare_uses(self):
        self.assertEqual(
            pricing_keys("bedrock/us.anthropic.claude-opus-4-6-v1"),
            ("anthropic-claude-opus-4-6", "claude-opus-4-6"),
        )

    def test_tier_suffix_survives_because_tiers_are_priced_apart(self):
        # sol/terra/luna differ by 25x. Collapsing them to "gpt-5.6" would
        # price one from whichever sibling the DB returned first.
        self.assertEqual(
            pricing_keys("bedrock_mantle/openai.gpt-5.6-sol"),
            ("openai-gpt-5.6-sol", "gpt-5.6-sol"),
        )


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
        found = reference_rates("wine-claude-opus-4-6")
        self.assertEqual(found, self.opus)

    def test_matches_a_full_deployment_address(self):
        found = reference_rates("bedrock/us.anthropic.claude-opus-4-6-v1")
        self.assertEqual(found, self.opus)

    def test_returns_none_when_dare_does_not_offer_the_model(self):
        self.assertIsNone(reference_rates("wine-qwen3-coder-next"))


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


class TierPricingTests(TestCase):
    """gpt-5.6 tiers share a capability profile but not a price.

    The tier rows are seeded by migration, so this asserts the mapping rather
    than the rates — the mapping is the invariant, the prices can move.
    """

    def test_each_tier_prices_from_its_own_row(self):
        for tier in ("sol", "terra", "luna"):
            with self.subTest(tier=tier):
                found = reference_rates(f"bedrock_mantle/openai.gpt-5.6-{tier}")
                self.assertEqual(found.identifier, f"gpt-5.6-{tier}")

    def test_tiers_do_not_collapse_onto_one_row(self):
        found = {
            reference_rates(f"gpt-5.6-{tier}").identifier
            for tier in ("sol", "terra", "luna")
        }
        self.assertEqual(len(found), 3)


class RegistryPricingTests(TestCase):
    """Models DARE routes but does not offer fall back to the price registry."""

    def test_registry_prices_a_model_with_no_dare_row(self):
        rates = reference_rates("gpt-5-nano")
        self.assertIsNotNone(rates)
        self.assertEqual(rates.input_token_rate_per_million, Decimal("0.05"))
        self.assertEqual(rates.output_token_rate_per_million, Decimal("0.40"))

    def test_unknown_model_stays_unpriced(self):
        # Better a blank cost than a wrong one in a billing table.
        self.assertIsNone(reference_rates("acme/never-heard-of-it"))


class SpendCounterTests(TestCase):
    """The wallet indicator reads one row, so the counter must stay accurate."""

    def test_each_call_adds_to_the_running_total(self):
        from billing.constants import LiteLLMKeySourceChoice
        from billing.models import LiteLLMKey, LiteLLMSpend
        from conversations.models import Conversation, Message
        from core.services.billing_service import BillingService
        from users.models import User

        user = User.objects.create_user(email="spender@example.com", password="x")
        key = LiteLLMKey.objects.create(
            label="gw",
            base_url="https://proxy.example/v1",
            api_key="k",
            source=LiteLLMKeySourceChoice.USER,
            owner_user=user,
            created_by=user,
        )
        llm = LLM.objects.get(identifier="claude-sonnet-5")
        llm.input_token_rate_per_million = Decimal("3.00")
        llm.output_token_rate_per_million = Decimal("15.00")
        llm.save()
        conversation = Conversation.active_objects.create(user=user)

        for _ in range(2):
            message = Message.active_objects.create(
                conversation=conversation,
                sender_type=2,
                litellm_key=key,
                litellm_model_name="us.anthropic.claude-sonnet-5",
            )
            BillingService().finalize_ai_message(
                message,
                "hi",
                {"input_tokens": 1_000_000, "output_tokens": 0},
            )

        spend = LiteLLMSpend.objects.get(user=user, litellm_key=key)
        self.assertEqual(spend.call_count, 2)
        self.assertEqual(spend.total_reference_amount, Decimal("6.000000"))
