from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from billing.constants import LiteLLMKeySourceChoice
from billing.models import LiteLLMKey
from conversations.constants import SenderType
from conversations.models import LLM, Conversation, Message
from users.constants import AuthSourceChoice
from users.models import User


class SelectedModelRefTests(TestCase):
    """A reopened conversation preselects the model its last answer used."""

    def setUp(self):
        self.user = User.objects.create_user(
            email="ref@example.com", password="x", auth_source=AuthSourceChoice.DARE
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
        self.conversation = Conversation.active_objects.create(
            conversation_id="REF01", user=self.user, source=AuthSourceChoice.DARE
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _answer(self, **kwargs):
        Message.active_objects.create(
            conversation=self.conversation,
            sender_type=SenderType.AI_ASSISTANT,
            message="answer",
            **kwargs,
        )

    def _ref(self):
        response = self.client.get("/api/conversations/REF01/")
        self.assertEqual(response.status_code, 200)
        return response.json()["selectedModelRef"]

    def test_last_answer_through_a_litellm_key_restores_that_model(self):
        self._answer(llm=self.llm)
        self._answer(litellm_key=self.key, litellm_model_name="gemini/flash")

        self.assertEqual(self._ref(), f"litellm:{self.key.pk}:gemini/flash")

    def test_last_answer_on_a_dare_model_restores_its_pk(self):
        self._answer(litellm_key=self.key, litellm_model_name="gemini/flash")
        self._answer(llm=self.llm)

        self.assertEqual(self._ref(), str(self.llm.pk))

    def test_conversation_without_answers_has_no_ref(self):
        self.assertIsNone(self._ref())
