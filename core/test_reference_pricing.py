from decimal import Decimal

from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext

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
        # price one from whichever sibling resolved first.
        self.assertEqual(
            pricing_keys("bedrock_mantle/openai.gpt-5.6-sol"),
            ("openai-gpt-5.6-sol", "gpt-5.6-sol"),
        )


class RegistryLookupTests(TestCase):
    """The registry is keyed by the identifiers a gateway actually serves."""

    def test_gateway_identifier_matches_verbatim(self):
        rates = reference_rates("us.anthropic.claude-sonnet-4-6")
        self.assertIsNotNone(rates)
        self.assertGreater(rates.input_token_rate_per_million, Decimal("0"))

    def test_lookup_costs_no_query(self):
        # The wallet indicator and every finalized message hit this path, so a
        # registry hit must not reach the database.
        with CaptureQueriesContext(connection) as queries:
            reference_rates("us.anthropic.claude-sonnet-4-6")
        self.assertEqual(len(queries), 0)

    def test_tiers_of_one_family_price_apart(self):
        rates = [
            reference_rates(f"bedrock_mantle/openai.gpt-5.6-{tier}")
            for tier in ("sol", "terra", "luna")
        ]
        self.assertNotIn(None, rates)
        self.assertEqual(len({r.input_token_rate_per_million for r in rates}), 3)

    def test_a_route_can_cost_more_than_the_bare_model(self):
        # Bedrock fronts the same model at a markup; the registry distinguishes
        # them, which a single model-table row cannot.
        fronted = reference_rates("bedrock_mantle/openai.gpt-5.6-terra")
        direct = reference_rates("gpt-5.6-terra")
        self.assertGreater(
            fronted.input_token_rate_per_million,
            direct.input_token_rate_per_million,
        )

    def test_unknown_model_stays_unpriced(self):
        # Better a blank cost than a wrong one in a billing table.
        self.assertIsNone(reference_rates("acme/never-heard-of-it"))

    def test_a_dare_model_row_does_not_price_a_proxy_call(self):
        # The model table holds what DARE charges on its own keys, which says
        # nothing about what a proxy route cost. Only the registry knows that.
        LLM.objects.create(
            name="House model",
            identifier="dare-house-model-x1",
            provider="custom",
            input_token_rate_per_million=Decimal("7.00"),
            output_token_rate_per_million=Decimal("21.00"),
        )
        self.assertIsNone(reference_rates("dare-house-model-x1"))


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
        conversation = Conversation.active_objects.create(user=user)
        model_name = "us.anthropic.claude-sonnet-4-6"
        rate = reference_rates(model_name).input_token_rate_per_million

        for _ in range(2):
            message = Message.active_objects.create(
                conversation=conversation,
                sender_type=2,
                litellm_key=key,
                litellm_model_name=model_name,
            )
            BillingService().finalize_ai_message(
                message,
                "hi",
                {"input_tokens": 1_000_000, "output_tokens": 0},
            )

        spend = LiteLLMSpend.objects.get(user=user, litellm_key=key)
        self.assertEqual(spend.call_count, 2)
        self.assertEqual(spend.total_reference_amount, rate * 2)

    def test_an_unpriced_model_leaves_the_counter_alone(self):
        from billing.constants import LiteLLMKeySourceChoice
        from billing.models import LiteLLMKey, LiteLLMSpend
        from conversations.models import Conversation, Message
        from core.services.billing_service import BillingService
        from users.models import User

        user = User.objects.create_user(email="unpriced@example.com", password="x")
        key = LiteLLMKey.objects.create(
            label="gw",
            base_url="https://proxy.example/v1",
            api_key="k",
            source=LiteLLMKeySourceChoice.USER,
            owner_user=user,
            created_by=user,
        )
        conversation = Conversation.active_objects.create(user=user)
        message = Message.active_objects.create(
            conversation=conversation,
            sender_type=2,
            litellm_key=key,
            litellm_model_name="acme/never-heard-of-it",
        )
        BillingService().finalize_ai_message(
            message, "hi", {"input_tokens": 100, "output_tokens": 100}
        )

        self.assertFalse(LiteLLMSpend.objects.filter(user=user).exists())
