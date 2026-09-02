from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.test import TestCase, override_settings
from pydantic import BaseModel

from api_keys.models import UserProviderAPIKey
from billing.constants import LiteLLMKeySourceChoice, UserWalletPreferenceTypeChoice
from billing.models import LiteLLMKey, UserWalletPreference
from conversations.models import LLM
from core.services.background_model_service import (
    BackgroundModelService,
    BackgroundModelUnavailable,
    resolve_background_model,
    resolve_background_payer,
)
from core.services.dtos import LLMStreamEvent
from feature_flags.models import FeatureFlag
from users.models import User


class _StructuredAnswer(BaseModel):
    ok: bool


class _TextService:
    def __init__(self):
        self.close = AsyncMock()

    async def stream_chat_completion(self, **_kwargs):
        yield LLMStreamEvent.text_delta("Useful title")
        yield LLMStreamEvent.usage_frame({"input_tokens": 12, "output_tokens": 3})


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
)
class BackgroundModelServiceTests(TestCase):
    def setUp(self):
        FeatureFlag.objects.update_or_create(
            key="enable_litellm_wallet",
            defaults={"default_enabled": True},
        )
        FeatureFlag.objects.update_or_create(
            key="enable_byok",
            defaults={"default_enabled": True},
        )
        self.user = User.objects.create_user(
            email="background@example.com", password="x"
        )
        self.luna, _ = LLM.objects.update_or_create(
            identifier="gpt-5.6-luna",
            defaults={
                "name": "GPT-5.6 Luna",
                "provider": "openai",
                "is_active": True,
                "is_reasoning": True,
                "supports_temperature": False,
                "input_token_rate_per_million": Decimal("0.20"),
                "output_token_rate_per_million": Decimal("1.20"),
            },
        )

    def test_dare_wallet_resolves_luna(self):
        route = resolve_background_model(self.user)

        self.assertEqual(route.model, self.luna)
        self.assertEqual(route.wallet_type, UserWalletPreferenceTypeChoice.DARE)
        self.assertEqual(route.dispatch_user, self.user)

    def test_missing_billing_user_fails_before_dispatch(self):
        service = BackgroundModelService()

        with patch.object(service, "_service_for", new=AsyncMock()) as dispatch:
            with self.assertRaisesRegex(
                BackgroundModelUnavailable, "require a billing user"
            ):
                async_to_sync(service.complete_text)(
                    user=None,
                    messages=[{"role": "user", "content": "Name this"}],
                    description="Conversation title generation",
                    max_tokens=80,
                )

        dispatch.assert_not_awaited()

    @patch("core.services.background_model_service.resolve_active_wallet_for_bot")
    def test_public_bot_uses_its_owner_as_the_billing_user(self, resolve_bot):
        resolve_bot.return_value = SimpleNamespace(payer_user=self.user)

        payer = resolve_background_payer(None, public_bot_id=42)

        self.assertEqual(payer, self.user)
        resolve_bot.assert_called_once_with(42, calling_user=None)

    def test_litellm_wallet_resolves_its_background_model(self):
        key = self._use_litellm("gateway/gpt-5.6-luna")

        route = resolve_background_model(self.user)

        self.assertEqual(route.model.identifier, "gateway/gpt-5.6-luna")
        self.assertEqual(route.model.provider, "custom")
        self.assertEqual(route.litellm_key, key)
        self.assertIsNone(route.persisted_llm)

    def test_blank_litellm_selection_explicitly_falls_back_to_dare(self):
        self._use_litellm("")

        route = resolve_background_model(self.user)

        self.assertEqual(route.model, self.luna)
        self.assertEqual(route.wallet_type, UserWalletPreferenceTypeChoice.DARE)
        self.assertIsNone(route.dispatch_user)

    def test_byo_wallet_uses_the_users_key_for_the_platform_model(self):
        UserProviderAPIKey.active_objects.update_or_create(
            user=self.user,
            provider="openai",
            defaults={"api_key": "user-key"},
        )
        preference = UserWalletPreference.get_or_create_for(self.user)
        preference.active_wallet_type = UserWalletPreferenceTypeChoice.BYO
        preference.active_wallet_ref_id = None
        preference.save()

        route = resolve_background_model(self.user)

        self.assertEqual(route.model, self.luna)
        self.assertEqual(route.wallet_type, UserWalletPreferenceTypeChoice.BYO)
        self.assertEqual(route.dispatch_user, self.user)

    def test_text_call_is_billed_after_usage_is_collected(self):
        billing = MagicMock()
        service = BackgroundModelService(billing)
        transport = _TextService()

        with patch.object(
            service, "_service_for", new=AsyncMock(return_value=transport)
        ):
            result = async_to_sync(service.complete_text)(
                user=self.user,
                messages=[{"role": "user", "content": "Name this"}],
                description="Conversation title generation",
                max_tokens=80,
            )

        self.assertEqual(result.value, "Useful title")
        billing.record_service_usage.assert_called_once_with(
            user=self.user,
            llm=self.luna,
            input_tokens=12,
            output_tokens=3,
            description="Conversation title generation",
        )
        transport.close.assert_awaited_once()

    def test_byo_text_call_records_external_usage(self):
        UserProviderAPIKey.active_objects.update_or_create(
            user=self.user,
            provider="openai",
            defaults={"api_key": "user-key"},
        )
        preference = UserWalletPreference.get_or_create_for(self.user)
        preference.active_wallet_type = UserWalletPreferenceTypeChoice.BYO
        preference.active_wallet_ref_id = None
        preference.save()
        billing = MagicMock()
        service = BackgroundModelService(billing)
        transport = _TextService()

        with patch.object(
            service, "_service_for", new=AsyncMock(return_value=transport)
        ):
            async_to_sync(service.complete_text)(
                user=self.user,
                messages=[{"role": "user", "content": "Name this"}],
                description="Conversation title generation",
                max_tokens=80,
            )

        billing.record_byo_service_usage.assert_called_once_with(
            user=self.user,
            llm=self.luna,
            input_tokens=12,
            output_tokens=3,
            description="Conversation title generation",
        )
        billing.record_service_usage.assert_not_called()

    def test_structured_proxy_call_is_attributed_to_litellm(self):
        key = self._use_litellm("gateway/gpt-5.6-luna")
        billing = MagicMock()
        service = BackgroundModelService(billing)
        transport = MagicMock()
        transport.parse_structured_output = AsyncMock(
            return_value=(
                _StructuredAnswer(ok=True),
                {"input_tokens": 20, "output_tokens": 5},
            )
        )
        transport.close = AsyncMock()

        with patch.object(
            service, "_service_for", new=AsyncMock(return_value=transport)
        ):
            result = async_to_sync(service.parse_structured)(
                user=self.user,
                messages=[{"role": "user", "content": "Return JSON"}],
                response_model=_StructuredAnswer,
                description="Memory writer",
                max_tokens=100,
            )

        self.assertTrue(result.value.ok)
        billing.record_litellm_service_usage.assert_called_once_with(
            user=self.user,
            litellm_key=key,
            model_name="gateway/gpt-5.6-luna",
            input_tokens=20,
            output_tokens=5,
            description="Memory writer",
        )
        billing.record_service_usage.assert_not_called()
        transport.close.assert_awaited_once()

    def _use_litellm(self, background_model):
        key = LiteLLMKey.objects.create(
            label="gateway",
            base_url="https://proxy.example/v1",
            api_key="k",
            source=LiteLLMKeySourceChoice.USER,
            owner_user=self.user,
            created_by=self.user,
            background_model=background_model,
        )
        preference = UserWalletPreference.get_or_create_for(self.user)
        preference.active_wallet_type = UserWalletPreferenceTypeChoice.LITELLM
        preference.active_wallet_ref_id = str(key.pk)
        preference.save()
        return key
