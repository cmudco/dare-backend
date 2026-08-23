from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import TestCase

from billing.models import Transaction, Wallet
from conversations.models import LLM
from core.services.billing_service import BillingService
from core.services.openai_service import OpenAIService
from memory.services.writer import Decision, WriterResponse, propose_decisions


class StructuredWriterServiceTests(TestCase):
    def setUp(self):
        self.llm = LLM.objects.get(identifier="gpt-5.6-luna")
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

    def test_writer_records_usage_for_the_structured_call(self):
        parsed = WriterResponse(
            explicit_request=False,
            decisions=[self._ignore_decision()],
        )
        service = MagicMock()
        service.parse_structured_output = AsyncMock(
            return_value=(
                parsed,
                {"input_tokens": 7000, "output_tokens": 300, "total_tokens": 7300},
            )
        )
        service.close = AsyncMock()

        with patch(
            "memory.services.writer.get_provider_api_key_sync",
            return_value="test",
        ), patch(
            "memory.services.writer.OpenAIService",
            return_value=service,
        ), patch(
            "memory.services.writer.BillingService"
        ) as billing_class:
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
        billing_class.return_value.record_service_usage.assert_called_once_with(
            user=self.user,
            llm=self.llm,
            input_tokens=7000,
            output_tokens=300,
            description="Memory writer for message 42",
        )
        service.close.assert_awaited_once()

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
