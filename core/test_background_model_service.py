from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from pydantic import BaseModel

from api_keys.models import UserProviderAPIKey
from billing.constants import LiteLLMKeySourceChoice, UserWalletPreferenceTypeChoice
from billing.models import LiteLLMKey, UserWalletPreference, Wallet
from conversations.models import LLM
from core.constants import BackgroundModelSource, BackgroundTask
from core.models import PlatformSettings
from core.services.background_model_service import (
    BackgroundModelNotOffered,
    BackgroundModelService,
    BackgroundModelUnavailable,
    BackgroundWalletEmpty,
    background_model_candidates,
    platform_background_model,
    resolve_background_model,
    resolve_background_payer,
    set_user_background_model,
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
        route = resolve_background_model(self.user, BackgroundTask.TITLES)

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
                    task=BackgroundTask.TITLES,
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

        route = resolve_background_model(self.user, BackgroundTask.TITLES)

        self.assertEqual(route.model.identifier, "gateway/gpt-5.6-luna")
        self.assertEqual(route.model.provider, "custom")
        self.assertEqual(route.litellm_key, key)
        self.assertIsNone(route.persisted_llm)

    def test_blank_litellm_selection_explicitly_falls_back_to_dare(self):
        self._use_litellm("")

        route = resolve_background_model(self.user, BackgroundTask.TITLES)

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

        route = resolve_background_model(self.user, BackgroundTask.TITLES)

        self.assertEqual(route.model, self.luna)
        self.assertEqual(route.wallet_type, UserWalletPreferenceTypeChoice.BYO)
        self.assertEqual(route.dispatch_user, self.user)

    def test_text_call_is_billed_after_usage_is_collected(self):
        Wallet.objects.update_or_create(
            user=self.user, defaults={"balance": Decimal("0.000001")}
        )
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
                task=BackgroundTask.TITLES,
            )

        self.assertEqual(result.value, "Useful title")
        billing.record_service_usage.assert_called_once_with(
            user=self.user,
            llm=self.luna,
            input_tokens=12,
            output_tokens=3,
            description="Conversation title generation",
            allow_overdraft=True,
        )
        transport.close.assert_awaited_once()

    def test_empty_dare_wallet_stops_the_call_before_dispatch(self):
        Wallet.objects.update_or_create(
            user=self.user, defaults={"balance": Decimal("0")}
        )
        billing = MagicMock()
        service = BackgroundModelService(billing)
        service_for = AsyncMock()

        with patch.object(service, "_service_for", new=service_for):
            with self.assertRaises(BackgroundWalletEmpty):
                async_to_sync(service.complete_text)(
                    user=self.user,
                    messages=[{"role": "user", "content": "Name this"}],
                    description="Conversation title generation",
                    max_tokens=80,
                    task=BackgroundTask.TITLES,
                )

        service_for.assert_not_awaited()
        billing.record_service_usage.assert_not_called()

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
                task=BackgroundTask.TITLES,
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
                task=BackgroundTask.MEMORY,
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

    def _admin_default(self):
        model, _ = LLM.objects.update_or_create(
            identifier="test-admin-background-model",
            defaults={
                "name": "Admin Background",
                "provider": "claude",
                "is_active": True,
            },
        )
        PlatformSettings.objects.create(
            pk=PlatformSettings.SINGLETON_ID, background_model=model
        )
        return model

    def test_admin_default_replaces_the_environment_model(self):
        haiku = self._admin_default()

        route = resolve_background_model(self.user, BackgroundTask.TITLES)

        self.assertEqual(route.model, haiku)
        self.assertEqual(platform_background_model(BackgroundTask.TITLES)[0], haiku)

    def test_empty_admin_default_uses_the_environment_model(self):
        PlatformSettings.objects.create(pk=PlatformSettings.SINGLETON_ID)

        self.assertEqual(platform_background_model(BackgroundTask.TITLES)[0], self.luna)

    def test_deactivated_admin_default_falls_back_to_the_environment_model(self):
        haiku = self._admin_default()
        haiku.is_active = False
        haiku.save(update_fields=["is_active"])

        with self.assertLogs(
            "core.services.background_model_service", level="WARNING"
        ) as logs:
            model = platform_background_model(BackgroundTask.TITLES)[0]

        self.assertEqual(model, self.luna)
        self.assertIn("no longer eligible", logs.output[0])

    def test_litellm_key_background_model_outranks_the_admin_default(self):
        self._admin_default()
        self._use_litellm("gateway/gpt-5.6-luna")

        route = resolve_background_model(self.user, BackgroundTask.TITLES)

        self.assertEqual(route.model.identifier, "gateway/gpt-5.6-luna")

    def test_platform_settings_allow_only_one_row(self):
        PlatformSettings.objects.create(pk=PlatformSettings.SINGLETON_ID)

        with self.assertRaises(IntegrityError), transaction.atomic():
            PlatformSettings.objects.create(pk=2)

    def _model(self, identifier, provider="claude", **extra):
        model, _ = LLM.objects.update_or_create(
            identifier=identifier,
            defaults={
                "name": identifier,
                "provider": provider,
                "is_active": True,
                "input_token_rate_per_million": Decimal("1"),
                "output_token_rate_per_million": Decimal("5"),
                **extra,
            },
        )
        return model

    def _use_byo(self, *providers):
        for provider in providers:
            UserProviderAPIKey.active_objects.update_or_create(
                user=self.user, provider=provider, defaults={"api_key": "user-key"}
            )
        preference = UserWalletPreference.get_or_create_for(self.user)
        preference.active_wallet_type = UserWalletPreferenceTypeChoice.BYO
        preference.active_wallet_ref_id = None
        preference.save()

    def _choose(self, model):
        self.user.background_model = model.identifier
        self.user.save(update_fields=["background_model"])

    def test_user_choice_outranks_the_platform_models(self):
        self._admin_default()
        sonnet = self._model("test-user-sonnet")
        PlatformSettings.objects.filter(pk=1).update(title_model=self.luna)
        self._choose(sonnet)

        for task in BackgroundTask:
            route = resolve_background_model(self.user, task)
            self.assertEqual(route.model, sonnet)
            self.assertEqual(route.source, BackgroundModelSource.USER)
            self.assertEqual(route.wallet_type, UserWalletPreferenceTypeChoice.DARE)

    def test_deactivated_user_choice_falls_back_to_the_platform(self):
        sonnet = self._model("test-user-sonnet")
        self._choose(sonnet)
        sonnet.is_active = False
        sonnet.save(update_fields=["is_active"])

        with self.assertLogs("core.services.background_model_service", level="WARNING"):
            route = resolve_background_model(self.user, BackgroundTask.TITLES)

        self.assertEqual(route.model, self.luna)
        self.assertEqual(route.source, BackgroundModelSource.ENVIRONMENT)

    def test_byo_choice_runs_on_the_users_own_key(self):
        sonnet = self._model("test-user-sonnet")
        self._use_byo("claude")
        self._choose(sonnet)

        route = resolve_background_model(self.user, BackgroundTask.MEMORY)

        self.assertEqual(route.model, sonnet)
        self.assertEqual(route.wallet_type, UserWalletPreferenceTypeChoice.BYO)

    def test_byo_choice_without_a_key_for_its_provider_is_not_used(self):
        sonnet = self._model("test-user-sonnet")
        self._use_byo("openai")
        self._choose(sonnet)

        candidates = background_model_candidates(
            self.user, UserWalletPreferenceTypeChoice.BYO
        )
        with self.assertLogs("core.services.background_model_service", level="WARNING"):
            route = resolve_background_model(self.user, BackgroundTask.TITLES)

        self.assertNotIn(sonnet, candidates)
        self.assertIn(self.luna, candidates)
        self.assertEqual(route.model, self.luna)
        self.assertEqual(route.wallet_type, UserWalletPreferenceTypeChoice.BYO)

    def test_byo_platform_model_without_a_key_is_billed_to_dare(self):
        self._use_byo("claude")

        route = resolve_background_model(self.user, BackgroundTask.TITLES)

        self.assertEqual(route.model, self.luna)
        self.assertEqual(route.wallet_type, UserWalletPreferenceTypeChoice.DARE)

    def test_litellm_key_model_outranks_the_users_choice(self):
        self._choose(self._model("test-user-sonnet"))
        self._use_litellm("gateway/gemini-flash")

        route = resolve_background_model(self.user, BackgroundTask.QUERY_ANALYSIS)

        self.assertEqual(route.model.identifier, "gateway/gemini-flash")
        self.assertEqual(route.source, BackgroundModelSource.LITELLM_KEY)

    def test_task_override_applies_to_its_task_only(self):
        default = self._admin_default()
        flash = self._model("test-flash", provider="gemini")
        PlatformSettings.objects.filter(pk=1).update(query_analysis_model=flash)

        analysis = resolve_background_model(self.user, BackgroundTask.QUERY_ANALYSIS)
        titles = resolve_background_model(self.user, BackgroundTask.TITLES)

        self.assertEqual(analysis.model, flash)
        self.assertEqual(analysis.source, BackgroundModelSource.TASK_OVERRIDE)
        self.assertEqual(titles.model, default)
        self.assertEqual(titles.source, BackgroundModelSource.PLATFORM_DEFAULT)

    def test_deactivated_task_override_falls_back_to_the_default(self):
        default = self._admin_default()
        flash = self._model("test-flash", provider="gemini", is_active=False)
        PlatformSettings.objects.filter(pk=1).update(memory_model=flash)

        with self.assertLogs("core.services.background_model_service", level="WARNING"):
            model, source = platform_background_model(BackgroundTask.MEMORY)

        self.assertEqual(model, default)
        self.assertEqual(source, BackgroundModelSource.PLATFORM_DEFAULT)

    def test_models_without_structured_output_are_not_offered(self):
        llama = self._model("test-llama", provider="llama")
        self._choose(llama)

        with self.assertLogs("core.services.background_model_service", level="WARNING"):
            route = resolve_background_model(self.user, BackgroundTask.MEMORY)

        self.assertEqual(route.model, self.luna)
        with self.assertRaises(BackgroundModelNotOffered):
            set_user_background_model(self.user, llama.identifier)

    def test_missing_environment_model_is_reported(self):
        self.luna.is_active = False
        self.luna.save(update_fields=["is_active"])

        with self.assertRaisesRegex(BackgroundModelUnavailable, "not active"):
            resolve_background_model(self.user, BackgroundTask.TITLES)

    def test_schema_only_providers_get_a_validated_structured_answer(self):
        sonnet = self._model("test-user-sonnet")
        self._choose(sonnet)
        Wallet.objects.update_or_create(user=self.user, defaults={"balance": 1})
        billing = MagicMock()
        service = BackgroundModelService(billing)
        transport = SimpleNamespace(
            generate_structured_output_with_usage=AsyncMock(
                return_value=({"ok": True}, {"input_tokens": 9, "output_tokens": 2})
            ),
            close=AsyncMock(),
        )

        with patch.object(
            service, "_service_for", new=AsyncMock(return_value=transport)
        ):
            result = async_to_sync(service.parse_structured)(
                user=self.user,
                messages=[{"role": "user", "content": "Return JSON"}],
                response_model=_StructuredAnswer,
                description="Advanced RAG query analysis",
                max_tokens=100,
                task=BackgroundTask.QUERY_ANALYSIS,
            )

        self.assertEqual(result.value, _StructuredAnswer(ok=True))
        schema = transport.generate_structured_output_with_usage.await_args.kwargs[
            "response_schema"
        ]
        self.assertFalse(schema["additionalProperties"])
        billing.record_service_usage.assert_called_once_with(
            user=self.user,
            llm=sonnet,
            input_tokens=9,
            output_tokens=2,
            description="Advanced RAG query analysis",
            allow_overdraft=True,
        )

    def test_invalid_structured_answer_is_billed_then_refused(self):
        self._choose(self._model("test-user-sonnet"))
        Wallet.objects.update_or_create(user=self.user, defaults={"balance": 1})
        billing = MagicMock()
        service = BackgroundModelService(billing)
        transport = SimpleNamespace(
            generate_structured_output_with_usage=AsyncMock(
                return_value=({"ok": "maybe"}, {"input_tokens": 9, "output_tokens": 2})
            ),
            close=AsyncMock(),
        )

        with patch.object(
            service, "_service_for", new=AsyncMock(return_value=transport)
        ):
            with self.assertRaisesRegex(BackgroundModelUnavailable, "invalid"):
                async_to_sync(service.parse_structured)(
                    user=self.user,
                    messages=[{"role": "user", "content": "Return JSON"}],
                    response_model=_StructuredAnswer,
                    description="Memory writer",
                    max_tokens=100,
                    task=BackgroundTask.MEMORY,
                )

        billing.record_service_usage.assert_called_once()
        transport.close.assert_awaited_once()
