from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from api_keys.constants import BillingModeChoice
from billing.constants import LiteLLMKeySourceChoice
from billing.models import LiteLLMKey, LiteLLMSpend, Transaction, Wallet
from conversations.models import LLM
from core.services.background_model_service import (
    BackgroundModelResult,
    BackgroundModelRoute,
)
from core.services.billing_service import BillingService
from core.services.custom_llm_service import CustomLLMService
from core.services.openai_service import OpenAIService
from memory.services.writer import Decision, WriterResponse, propose_decisions


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
)
class StructuredWriterServiceTests(TestCase):
    def setUp(self):
        self.llm, _created = LLM.objects.update_or_create(
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
        self.user = get_user_model().objects.create_user(
            email="writer-service@example.com",
            password="x",
        )

    def test_openai_service_returns_parsed_model_and_usage(self):
        parsed = WriterResponse(
            explicit_request=False,
            decisions=[self._ignore_decision()],
        )
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(parsed=parsed))],
            usage=SimpleNamespace(prompt_tokens=123, completion_tokens=45),
        )
        client = MagicMock()
        client.chat.completions.parse = AsyncMock(return_value=response)
        service = OpenAIService(self.llm, api_key="test")
        service._client = client

        result, usage = async_to_sync(service.parse_structured_output)(
            messages=[{"role": "user", "content": "hello"}],
            response_model=WriterResponse,
            max_tokens=4000,
        )

        self.assertEqual(result, parsed)
        self.assertEqual(usage["input_tokens"], 123)
        self.assertEqual(usage["output_tokens"], 45)
        params = client.chat.completions.parse.await_args.kwargs
        self.assertEqual(params["response_format"], WriterResponse)
        self.assertEqual(params["max_completion_tokens"], 4000)
        self.assertNotIn("temperature", params)

    def test_custom_service_returns_parsed_model_and_usage(self):
        parsed = WriterResponse(
            explicit_request=False,
            decisions=[self._ignore_decision()],
        )
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(parsed=parsed))],
            usage=SimpleNamespace(prompt_tokens=321, completion_tokens=54),
        )
        client = MagicMock()
        client.chat.completions.parse = AsyncMock(return_value=response)
        client.close = AsyncMock()

        with patch("core.services.custom_llm_service.AsyncOpenAI", return_value=client):
            service = CustomLLMService(
                self.llm,
                api_key="test",
                base_url="https://proxy.example/v1",
            )

        result, usage = async_to_sync(service.parse_structured_output)(
            messages=[{"role": "user", "content": "hello"}],
            response_model=WriterResponse,
            max_tokens=4000,
        )
        async_to_sync(service.close)()

        self.assertEqual(result, parsed)
        self.assertEqual(usage["input_tokens"], 321)
        self.assertEqual(usage["output_tokens"], 54)
        params = client.chat.completions.parse.await_args.kwargs
        self.assertEqual(params["response_format"], WriterResponse)
        self.assertEqual(params["max_completion_tokens"], 4000)
        client.close.assert_awaited_once()

    def test_writer_records_usage_for_the_structured_call(self):
        parsed = WriterResponse(
            explicit_request=False,
            decisions=[self._ignore_decision()],
        )
        background_models = MagicMock()
        background_models.parse_structured = AsyncMock(
            return_value=BackgroundModelResult(
                value=parsed,
                route=BackgroundModelRoute(
                    model=self.llm,
                    wallet_type="DARE",
                    dispatch_user=self.user,
                ),
                input_tokens=7000,
                output_tokens=300,
            )
        )

        with patch(
            "memory.services.writer.BackgroundModelService",
            return_value=background_models,
        ):
            proposal = propose_decisions(
                user=self.user,
                source_message_id=42,
                user_doc="",
                archive=[],
                user_message="Hello",
                assistant_message="Hi",
                keys_in_use=[],
            )

        self.assertEqual(proposal.decisions[0].action, "ignore")
        call = background_models.parse_structured.await_args.kwargs
        self.assertEqual(call["user"], self.user)
        self.assertIs(call["response_model"], WriterResponse)
        self.assertEqual(call["description"], "Memory writer for message 42")
        self.assertEqual(call["max_tokens"], 4000)
        self.assertIsNone(call["model_override"])

    def test_service_usage_creates_a_costed_transaction(self):
        wallet = Wallet.objects.get(user=self.user)
        wallet.balance = "5.00"
        wallet.save(update_fields=["balance", "updated_at"])

        transaction = BillingService().record_service_usage(
            user=self.user,
            llm=self.llm,
            input_tokens=2000,
            output_tokens=100,
            description="Memory writer for message 42",
        )

        transaction = Transaction.objects.get(pk=transaction.pk)
        wallet.refresh_from_db()
        self.assertEqual(str(transaction.amount), "0.000520")
        self.assertEqual(str(wallet.balance), "4.999480")
        self.assertEqual(transaction.input_tokens, 2000)
        self.assertEqual(transaction.output_tokens, 100)

    def test_litellm_service_usage_is_reported_without_debiting_wallet(self):
        key = self._make_litellm_key()
        wallet = Wallet.objects.get(user=self.user)
        wallet.balance = "5.00"
        wallet.save(update_fields=["balance", "updated_at"])

        transaction = BillingService().record_litellm_service_usage(
            user=self.user,
            litellm_key=key,
            model_name="bedrock_mantle/openai.gpt-5.6-luna",
            input_tokens=2000,
            output_tokens=100,
            description="Memory writer for message 84",
        )

        transaction.refresh_from_db()
        wallet.refresh_from_db()
        spend = LiteLLMSpend.objects.get(user=self.user, litellm_key=key)
        self.assertEqual(transaction.billing_mode, BillingModeChoice.LITELLM)
        self.assertEqual(transaction.amount, 0)
        self.assertIsNone(transaction.llm)
        self.assertEqual(transaction.llm_name, "bedrock_mantle/openai.gpt-5.6-luna")
        self.assertGreater(transaction.reference_amount, 0)
        self.assertEqual(wallet.balance, 5)
        self.assertEqual(spend.call_count, 1)
        self.assertEqual(spend.total_reference_amount, transaction.reference_amount)

    def _make_litellm_key(self):
        return LiteLLMKey.objects.create(
            label="writer proxy",
            base_url="https://proxy.example/v1",
            api_key="k",
            source=LiteLLMKeySourceChoice.USER,
            owner_user=self.user,
            created_by=self.user,
        )

    @staticmethod
    def _ignore_decision() -> Decision:
        return Decision(
            action="ignore",
            reason="Small talk is not durable.",
            pinned_to=None,
            text=None,
            trigger=None,
            applies_when=None,
            topic=None,
            qualifier=None,
            importance=None,
            confidence=None,
            sensitivity=None,
            occurred_at=None,
            is_snapshot=False,
            valid_until=None,
            supersedes_id=None,
            reinforces_id=None,
        )
