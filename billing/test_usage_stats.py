from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from api_keys.constants import BillingModeChoice
from billing.constants import (
    LiteLLMKeySourceChoice,
    TransactionSourceChoice,
    TransactionTypeChoice,
)
from billing.models import LiteLLMKey, LiteLLMSpend, Transaction, Wallet
from conversations.constants import SenderType
from conversations.models import LLM, Conversation, Message
from users.constants import AuthSourceChoice
from users.models import User


class UsageStatsTests(TestCase):
    """Proxy usage has to be counted, but never as wallet spend.

    A LiteLLM call has no ``llm`` row and is charged to the user's own proxy
    account. Both facts used to make it invisible: the dashboard filtered on
    the foreign key, so the tokens vanished along with the dollars.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            email="stats@example.com",
            password="x",
            auth_source=AuthSourceChoice.DARE,
        )
        self.llm = LLM.objects.create(
            name="Wallet Model",
            identifier="wallet-model",
            provider="openai",
            input_token_rate_per_million=Decimal("1"),
            output_token_rate_per_million=Decimal("2"),
        )
        self.key = LiteLLMKey.objects.create(
            label="gateway",
            base_url="https://proxy.example/v1",
            api_key="k",
            source=LiteLLMKeySourceChoice.USER,
            owner_user=self.user,
            created_by=self.user,
        )
        Wallet.objects.update_or_create(
            user=self.user, defaults={"balance": Decimal("10.00")}
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _debit(self, **kwargs):
        defaults = dict(
            user=self.user,
            type=TransactionTypeChoice.DEBIT,
            source=TransactionSourceChoice.USAGE,
            platform=AuthSourceChoice.DARE,
            input_tokens=100,
            output_tokens=50,
            message="usage",
        )
        defaults.update(kwargs)
        return Transaction.objects.create(**defaults)

    def _wallet_call(self):
        return self._debit(
            amount=Decimal("0.25"),
            llm=self.llm,
            billing_mode=BillingModeChoice.WALLET,
        )

    def _proxy_call(self, model="wine-claude-opus-4-6", reference=Decimal("0.10")):
        return self._debit(
            amount=Decimal("0"),
            reference_amount=reference,
            llm=None,
            llm_name=model,
            billing_mode=BillingModeChoice.LITELLM,
        )

    def test_token_totals_count_proxy_calls(self):
        self._wallet_call()
        self._proxy_call()

        stats = self.client.get("/users/api/stats/").json()

        self.assertEqual(stats["totalInputTokens"], 200)
        self.assertEqual(stats["totalOutputTokens"], 100)

    def test_charged_and_estimated_costs_are_reported_apart(self):
        # Summing them would produce a number describing neither: one is money
        # DARE took, the other is money the proxy took.
        self._wallet_call()
        self._proxy_call()

        overall = self.client.get("/api/billing/model_stats/").json()["overallStats"]

        self.assertEqual(Decimal(str(overall["totalCostDecimal"])), Decimal("0.25"))
        self.assertEqual(Decimal(str(overall["estimatedCostDecimal"])), Decimal("0.10"))

    def test_proxy_rows_group_by_the_identifier_the_gateway_served(self):
        self._proxy_call(model="wine-claude-opus-4-6")
        self._proxy_call(model="wine-claude-opus-4-6")
        self._proxy_call(model="gpt-5.6-sol")

        rows = self.client.get("/api/billing/model_stats/").json()["modelsBillingStats"]
        proxy = {row["llmName"]: row for row in rows if row["isEstimated"]}

        self.assertEqual(set(proxy), {"wine-claude-opus-4-6", "gpt-5.6-sol"})
        self.assertEqual(proxy["wine-claude-opus-4-6"]["transactionCount"], 2)
        self.assertIsNone(proxy["wine-claude-opus-4-6"]["llmId"])

    def test_a_model_the_registry_cannot_price_is_not_shown_as_free(self):
        self._proxy_call(model="unknown-model", reference=None)

        rows = self.client.get("/api/billing/model_stats/").json()["modelsBillingStats"]
        row = next(r for r in rows if r["llmName"] == "unknown-model")

        self.assertEqual(row["totalCost"], "—")

    def test_litellm_stats_separates_priced_from_unpriced_calls(self):
        self._proxy_call(reference=Decimal("0.10"))
        self._proxy_call(model="unknown-model", reference=None)
        LiteLLMSpend.objects.create(
            user=self.user,
            litellm_key=self.key,
            total_reference_amount=Decimal("0.10"),
            call_count=1,
        )

        body = self.client.get("/api/billing/litellm-stats/").json()

        self.assertEqual(body["overallStats"]["totalCalls"], 2)
        self.assertEqual(body["overallStats"]["unpricedCalls"], 1)
        self.assertEqual(
            Decimal(body["overallStats"]["totalReferenceCost"]), Decimal("0.10")
        )
        self.assertEqual([key["label"] for key in body["keysBreakdown"]], ["gateway"])

    def test_wallet_only_usage_reports_no_proxy_activity(self):
        self._wallet_call()

        body = self.client.get("/api/billing/litellm-stats/").json()

        self.assertEqual(body["overallStats"]["totalCalls"], 0)
        self.assertEqual(body["modelsBreakdown"], [])


class EnergyStatsTests(TestCase):
    """Retiring a model must not blank out the messages it produced.

    ``Message.llm`` is SET_NULL, so deleting an LLM leaves its messages with
    energy readings and no model row. The breakdown used to hand the frontend
    a null provider, and the chart's colour lookup lowercased it — taking the
    whole Environmental Impact tab down for anyone whose history touched a
    retired model.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            email="energy@example.com",
            password="x",
            auth_source=AuthSourceChoice.DARE,
        )
        self.conversation = Conversation.active_objects.create(
            user=self.user, title="t", source=AuthSourceChoice.DARE
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _message(self, llm):
        return Message.active_objects.create(
            conversation=self.conversation,
            sender_type=SenderType.AI_ASSISTANT,
            sender="ai",
            message="hello",
            llm=llm,
            energy_wh=Decimal("1.5"),
            carbon_g=Decimal("0.4"),
            water_ml=Decimal("2.0"),
        )

    def _breakdown(self):
        return self.client.get("/api/billing/energy-stats/").json()["modelsBreakdown"]

    def test_a_retired_model_keeps_a_name_and_a_provider(self):
        llm = LLM.objects.create(
            name="Retired Model",
            identifier="retired-model",
            provider="openai",
            input_token_rate_per_million=Decimal("1"),
            output_token_rate_per_million=Decimal("1"),
        )
        self._message(llm)
        llm.delete()

        row = self._breakdown()[0]

        self.assertIsNone(row["llmId"])
        self.assertIsNotNone(row["llmName"])
        self.assertIsNotNone(row["llmProvider"])
        self.assertEqual(row["energyWh"], 1.5)

    def test_a_live_model_reports_its_own_name(self):
        self._message(
            LLM.objects.create(
                name="Live Model",
                identifier="live-model",
                provider="claude",
                input_token_rate_per_million=Decimal("1"),
                output_token_rate_per_million=Decimal("1"),
            )
        )

        row = self._breakdown()[0]

        self.assertEqual(row["llmName"], "Live Model")
        self.assertEqual(row["llmProvider"], "claude")
