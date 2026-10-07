"""Who pays for a SocraticBooks bot turn, and through which key it is sent.

A DARE catalog model is paid by the chatter (DARE wallet, or their BYO key);
a LiteLLM model saved on the bot is sponsored by the owner's key.
"""

from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from api_keys.constants import BillingModeChoice
from api_keys.models import UserProviderAPIKey
from billing.constants import (
    LITELLM_SPEND_LIMIT_REACHED,
    UserWalletPreferenceTypeChoice,
)
from billing.exceptions import BotModelUnavailable, PaymentRequiredError
from billing.litellm_key_service import LiteLLMKeyDependencyError, delete_key
from billing.models import (
    GroupWallet,
    LiteLLMKey,
    LiteLLMSpend,
    Transaction,
    UserWalletPreference,
    Wallet,
)
from billing.test_spend_caps import (
    activate,
    make_group_key,
    make_personal_key,
    make_user,
    record_spend,
)
from conversations.constants import SenderType
from conversations.models import LLM, Conversation, Message, ProviderAPIKey
from conversations.services.llm_filter_service import (
    KEY_UNAVAILABLE,
    MODEL_NOT_ON_KEY,
    TRACKING_NEEDS_DARE_MODEL,
    bot_model_problem,
    filter_for_bot,
)
from conversations.services.message_helpers.db_helpers import parse_model_id
from core.services.api_key_service import get_chat_dispatch_credentials_sync
from core.services.billing_service import BillingService
from core.services.dtos.llm_descriptor_dto import litellm_picker_id
from core.services.sb_client import BotBillingConfig, SocraticBooksRequestError
from feature_flags.models import FeatureFlag
from users.models import AccessCodeGroup

BOT_ID = 7
BILLING_CONFIG = "core.services.sb_client.SocraticBooksClient.get_bot_billing_config"


class SponsorshipCase(TestCase):
    """Owner with a personal key, subscriber on their own key, bot saved on the owner's."""

    def setUp(self):
        for flag in ("enable_litellm_wallet", "enable_byok"):
            FeatureFlag.objects.update_or_create(
                key=flag, defaults={"default_enabled": True}
            )
        self.owner = make_user("owner@example.com")
        self.owner_key = make_personal_key(self.owner)
        self.subscriber = make_user("sub@example.com", balance="2.00")
        self.subscriber_key = LiteLLMKey.objects.create(
            label="sub gateway",
            base_url="https://other-gateway.example/v1",
            api_key="subscriber-key",
            source=self.owner_key.source,
            owner_user=self.subscriber,
            created_by=self.subscriber,
        )
        activate(self.subscriber, self.subscriber_key)
        self.saved_ref = litellm_picker_id(self.owner_key.pk, "gpt-5")
        self.config_patch = patch(BILLING_CONFIG, side_effect=self._config)
        self.config_patch.start()
        self.addCleanup(self.config_patch.stop)

    def _config(self, bot_id):
        return BotBillingConfig(
            bot_id=bot_id,
            owner_dare_user_id=self.owner.pk,
            budget=None,
            budget_used=Decimal("0"),
            is_publicly_deployed=False,
            is_active=True,
            chat_model_ref=self.saved_ref,
        )

    def _sponsored(self, user, ref=None):
        return get_chat_dispatch_credentials_sync(
            "custom", user, bot_id=BOT_ID, litellm_model_ref=ref or self.saved_ref
        )


class BotSponsorshipTests(SponsorshipCase):
    def test_saved_litellm_model_is_sent_through_the_owners_key(self):
        creds = self._sponsored(self.subscriber)

        self.assertEqual(creds.litellm_key_id, str(self.owner_key.pk))
        self.assertEqual(creds.api_key, "personal-key")
        self.assertEqual(creds.gateway_user, f"dare-user-{self.owner.pk}")

    def test_anonymous_chatters_are_sponsored_the_same_way(self):
        self.assertEqual(self._sponsored(None).litellm_key_id, str(self.owner_key.pk))

    def test_only_the_bots_saved_model_is_sponsored(self):
        other_key = make_personal_key(self.owner)

        with self.assertRaises(BotModelUnavailable):
            self._sponsored(
                self.subscriber, litellm_picker_id(other_key.pk, "claude-opus-5")
            )

    def test_a_key_the_owner_can_no_longer_use_is_unavailable(self):
        group = AccessCodeGroup.objects.create(access_code="SPN-1", max_capacity=5)
        group_key = make_group_key(group, self.owner)
        self.saved_ref = litellm_picker_id(group_key.pk, "gpt-5")
        self.owner.access_code_group = group
        self.owner.save(update_fields=["access_code_group"])
        self.assertEqual(
            self._sponsored(self.subscriber).litellm_key_id, str(group_key.pk)
        )

        self.owner.access_code_group = None
        self.owner.save(update_fields=["access_code_group"])
        with self.assertRaises(BotModelUnavailable):
            self._sponsored(self.subscriber)

    def test_group_key_sponsorship_spends_the_owners_member_allowance(self):
        group = AccessCodeGroup.objects.create(access_code="SPN-2", max_capacity=5)
        GroupWallet.objects.create(group=group, litellm_member_cap=Decimal("15"))
        group_key = make_group_key(group, make_user("admin@example.com"))
        self.owner.access_code_group = group
        self.owner.save(update_fields=["access_code_group"])
        self.saved_ref = litellm_picker_id(group_key.pk, "gpt-5")
        self._sponsored(self.subscriber)

        record_spend(self.owner, group_key, "15")
        with self.assertRaises(PaymentRequiredError) as refused:
            self._sponsored(self.subscriber)
        self.assertEqual(refused.exception.code, LITELLM_SPEND_LIMIT_REACHED)

    def test_dare_model_bot_bills_a_litellm_chatter_from_dare(self):
        ProviderAPIKey._default_manager.create(
            provider="claude", api_key="system-claude"
        )

        creds = get_chat_dispatch_credentials_sync(
            "claude", self.subscriber, bot_id=BOT_ID
        )

        self.assertEqual(creds.wallet_type, UserWalletPreferenceTypeChoice.DARE)
        self.assertEqual(creds.api_key, "system-claude")

    def test_dare_model_bot_uses_the_chatters_byo_key_for_that_provider(self):
        UserProviderAPIKey._default_manager.update_or_create(
            user=self.subscriber, provider="claude", defaults={"api_key": "sub-claude"}
        )
        pref = self.subscriber.wallet_preference
        pref.active_wallet_type = UserWalletPreferenceTypeChoice.BYO
        pref.active_wallet_ref_id = None
        pref.save()

        creds = get_chat_dispatch_credentials_sync(
            "claude", self.subscriber, bot_id=BOT_ID
        )

        self.assertEqual(creds.wallet_type, UserWalletPreferenceTypeChoice.BYO)
        self.assertEqual(creds.api_key, "sub-claude")

    def test_plain_chat_sends_a_litellm_model_through_its_own_key(self):
        other_key = make_personal_key(self.owner)
        activate(self.owner, other_key)

        creds = get_chat_dispatch_credentials_sync(
            "custom", self.owner, litellm_model_ref=self.saved_ref
        )

        self.assertEqual(creds.litellm_key_id, str(self.owner_key.pk))

    def test_sponsored_reply_is_recorded_for_the_chatter_and_spent_by_the_owner(self):
        conversation = Conversation._default_manager.create(
            user=self.subscriber, bot_id=BOT_ID, source="SocraticBots"
        )
        reply = Message._default_manager.create(
            conversation=conversation,
            sender_type=SenderType.AI_ASSISTANT,
            message="",
            litellm_key=self.owner_key,
            litellm_model_name="gpt-5",
        )

        BillingService().finalize_ai_message(
            reply, "Hello", {"input_tokens": 1000, "output_tokens": 100}
        )

        row = Transaction.objects.get(llm_name="gpt-5")
        self.assertEqual(row.user, self.subscriber)
        self.assertEqual(row.bot_id, BOT_ID)
        self.assertEqual(row.bot_owner, self.owner)
        self.assertEqual(row.billing_mode, BillingModeChoice.LITELLM)
        self.assertEqual(row.amount, Decimal("0"))
        self.assertGreater(row.reference_amount, 0)
        self.assertTrue(
            LiteLLMSpend.objects.filter(
                user=self.owner, litellm_key=self.owner_key
            ).exists()
        )
        self.assertFalse(LiteLLMSpend.objects.filter(user=self.subscriber).exists())
        self.assertEqual(
            Wallet.objects.get(user=self.subscriber).balance, Decimal("2.00")
        )


class BotModelProblemTests(TestCase):
    def setUp(self):
        self.owner = make_user("owner@example.com")
        self.key = make_personal_key(self.owner)
        self.model = LLM.objects.filter(is_active=True).first() or LLM.objects.create(
            name="Test", identifier="test-model", provider="claude"
        )
        listed = patch(
            "billing.litellm_models_service.list_models",
            return_value=type(
                "Probe", (), {"models": [type("M", (), {"name": "gpt-5"})()]}
            )(),
        )
        listed.start()
        self.addCleanup(listed.stop)

    def test_catalog_model_and_listed_gateway_model_can_be_saved(self):
        self.assertIsNone(
            bot_model_problem(self.owner, str(self.model.pk), is_tracking=False)
        )
        self.assertIsNone(
            bot_model_problem(
                self.owner, litellm_picker_id(self.key.pk, "gpt-5"), is_tracking=False
            )
        )

    def test_gateway_model_the_key_does_not_list_is_rejected(self):
        self.assertEqual(
            bot_model_problem(
                self.owner, litellm_picker_id(self.key.pk, "nope"), is_tracking=False
            ),
            MODEL_NOT_ON_KEY,
        )

    def test_someone_elses_key_is_rejected(self):
        stranger = make_user("stranger@example.com")
        self.assertEqual(
            bot_model_problem(
                stranger, litellm_picker_id(self.key.pk, "gpt-5"), is_tracking=False
            ),
            KEY_UNAVAILABLE,
        )

    def test_progress_tracking_needs_a_catalog_model(self):
        self.assertEqual(
            bot_model_problem(
                self.owner, litellm_picker_id(self.key.pk, "gpt-5"), is_tracking=True
            ),
            TRACKING_NEEDS_DARE_MODEL,
        )
        self.assertIsNone(
            bot_model_problem(self.owner, str(self.model.pk), is_tracking=True)
        )


class LiteLLMKeyDeletionTests(TestCase):
    def setUp(self):
        self.owner = make_user("owner@example.com")
        self.key = make_personal_key(self.owner)
        configured = patch(
            "core.services.sb_client.SocraticBooksClient.is_configured",
            return_value=True,
        )
        configured.start()
        self.addCleanup(configured.stop)

    @patch("core.services.sb_client.SocraticBooksClient.nullify_litellm_key_references")
    def test_deleting_a_key_clears_it_from_bots(self, nullify):
        key_id = self.key.pk
        delete_key(self.key)

        nullify.assert_called_once_with(str(key_id))
        self.assertFalse(LiteLLMKey.objects.filter(pk=key_id).exists())

    @patch(
        "core.services.sb_client.SocraticBooksClient.nullify_litellm_key_references",
        side_effect=SocraticBooksRequestError("down"),
    )
    def test_key_survives_when_bots_cannot_be_updated(self, _nullify):
        key_id = self.key.pk
        with self.assertRaises(LiteLLMKeyDependencyError):
            delete_key(self.key)

        self.assertTrue(LiteLLMKey.objects.filter(pk=key_id).exists())


class BotPickerTests(TestCase):
    """The bot picker follows the owner's active wallet and keeps saved models."""

    def setUp(self):
        for flag in ("enable_litellm_wallet", "enable_byok"):
            FeatureFlag.objects.update_or_create(
                key=flag, defaults={"default_enabled": True}
            )
        self.owner = make_user("owner@example.com")
        self.key = make_personal_key(self.owner)
        self.gemini = LLM.objects.create(
            name="Gem", identifier="gem-test", provider="gemini"
        )
        self.claude = LLM.objects.create(
            name="Cla", identifier="cla-test", provider="claude"
        )
        self.saved = {"chat": str(self.claude.pk), "tracking": None}
        listed = patch(
            "billing.litellm_models_service.list_models",
            return_value=type(
                "Probe",
                (),
                {"models": [type("M", (), {"name": "gpt-5"})()], "is_stale": False},
            )(),
        )
        listed.start()
        self.addCleanup(listed.stop)
        config = patch(BILLING_CONFIG, side_effect=self._config)
        config.start()
        self.addCleanup(config.stop)

    def _config(self, bot_id):
        return BotBillingConfig(
            bot_id=bot_id,
            owner_dare_user_id=self.owner.pk,
            budget=None,
            budget_used=Decimal("0"),
            is_publicly_deployed=False,
            is_active=True,
            chat_model_ref=self.saved["chat"],
            tracking_model_ref=self.saved["tracking"],
        )

    def _ids(self, bot_id=BOT_ID):
        entries, _meta = filter_for_bot(
            bot_id,
            self.owner,
            LLM.objects.filter(pk__in=[self.gemini.pk, self.claude.pk]),
        )
        return {e["id"]: e["paid_by"] for e in entries}

    def test_byo_owner_sees_their_providers_plus_the_saved_model(self):
        UserProviderAPIKey._default_manager.update_or_create(
            user=self.owner, provider="gemini", defaults={"api_key": "g"}
        )
        pref = UserWalletPreference.get_or_create_for(self.owner)
        pref.active_wallet_type = UserWalletPreferenceTypeChoice.BYO
        pref.save()

        self.assertEqual(
            self._ids(),
            {str(self.gemini.pk): "CHATTER", str(self.claude.pk): "CHATTER"},
        )
        self.assertEqual(self._ids(bot_id=None), {str(self.gemini.pk): "CHATTER"})

    def test_litellm_owner_sees_only_their_keys_models(self):
        activate(self.owner, self.key)
        self.saved["chat"] = None

        self.assertEqual(
            self._ids(), {litellm_picker_id(self.key.pk, "gpt-5"): "OWNER"}
        )


class SponsorshipEdgeTests(SponsorshipCase):
    """Anonymous turns, switched-off wallets, and the HTTP surfaces around them."""

    def test_anonymous_reply_is_recorded_against_the_owner(self):
        conversation = Conversation._default_manager.create(
            user=None, bot_id=BOT_ID, source="SocraticBots"
        )
        reply = Message._default_manager.create(
            conversation=conversation,
            sender_type=SenderType.AI_ASSISTANT,
            message="",
            litellm_key=self.owner_key,
            litellm_model_name="gpt-5",
        )

        BillingService().finalize_ai_message(
            reply, "Hello", {"input_tokens": 1000, "output_tokens": 100}
        )

        row = Transaction.objects.get(llm_name="gpt-5")
        self.assertEqual(
            (row.user, row.bot_owner, row.bot_id), (self.owner, self.owner, BOT_ID)
        )
        self.assertTrue(
            LiteLLMSpend.objects.filter(
                user=self.owner, litellm_key=self.owner_key
            ).exists()
        )

    def test_anonymous_turns_need_a_model_the_budget_can_meter(self):
        self.saved_ref = litellm_picker_id(self.owner_key.pk, "no-price-model-x")
        with self.assertRaises(PaymentRequiredError) as refused:
            self._sponsored(None)
        self.assertEqual(refused.exception.code, "BOT_CAP_REACHED")
        self.assertEqual(
            self._sponsored(self.subscriber).litellm_key_id, str(self.owner_key.pk)
        )

    def test_owner_with_litellm_switched_off_cannot_sponsor(self):
        flag = FeatureFlag.objects.get(key="enable_litellm_wallet")
        flag.default_enabled = False
        flag.save()

        with self.assertRaises(PaymentRequiredError) as refused:
            self._sponsored(self.subscriber)
        self.assertEqual(refused.exception.code, "LITELLM_UNAVAILABLE")

    def test_chat_resolves_only_the_bots_saved_litellm_model(self):
        saved = parse_model_id.func(self.saved_ref, self.subscriber, bot_id=BOT_ID)
        other = parse_model_id.func(
            litellm_picker_id(make_personal_key(self.owner).pk, "gpt-5"),
            self.subscriber,
            bot_id=BOT_ID,
        )

        self.assertEqual(saved.litellm_key.pk, self.owner_key.pk)
        self.assertIsNone(other)

    def test_only_the_owner_sees_a_bots_model_list(self):
        client = APIClient()
        client.force_authenticate(self.subscriber)
        self.assertEqual(
            client.get("/api/llms/", {"wallet_scope": f"bot:{BOT_ID}"}).status_code, 404
        )
        client.force_authenticate(self.owner)
        self.assertEqual(
            client.get("/api/llms/", {"wallet_scope": f"bot:{BOT_ID}"}).status_code, 200
        )

    @override_settings(DARE_INTERNAL_KEY="internal-secret")
    def test_bot_model_check_needs_the_internal_key(self):
        body = {"ownerDareUserId": self.owner.pk, "chatModelDareId": "999999"}
        url = "/api/internal/bot-model-check/"
        client = APIClient()
        self.assertIn(client.post(url, body, format="json").status_code, (401, 403))
        response = client.post(
            url, body, format="json", HTTP_X_INTERNAL_KEY="internal-secret"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["chatModelProblem"], "MODEL_NOT_AVAILABLE")

    @patch(
        "core.services.sb_client.SocraticBooksClient.is_configured", return_value=True
    )
    @patch(
        "core.services.sb_client.SocraticBooksClient.get_litellm_key_dependencies",
        return_value=(),
    )
    def test_key_dependents_are_the_owners_to_see(self, _deps, _configured):
        url = f"/api/billing/wallets/litellm/{self.owner_key.pk}/dependents/"
        client = APIClient()
        client.force_authenticate(self.subscriber)
        self.assertEqual(client.get(url).status_code, 404)
        client.force_authenticate(self.owner)
        response = client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["botCount"], 0)
