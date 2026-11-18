from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from conversations.models import LLM, Conversation
from core.services.claude_service import ClaudeService
from core.services.openai_service import OpenAIService

User = get_user_model()


class LLMCapabilityAPITests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="tester@example.com",
            password="password",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_model_list_excludes_inactive_models(self):
        active = LLM.objects.create(
            name="Active Model",
            identifier="active-model",
            provider="openai",
            is_active=True,
        )
        inactive = LLM.objects.create(
            name="Inactive Model",
            identifier="inactive-model",
            provider="openai",
            is_active=False,
        )

        response = self.client.get("/api/llms/")

        self.assertEqual(response.status_code, 200)
        ids = {item["id"] for item in response.data["results"]}
        self.assertIn(active.id, ids)
        self.assertNotIn(inactive.id, ids)

    def test_all_models_includes_inactive_models_with_capabilities(self):
        inactive = LLM.objects.create(
            name="Inactive Model",
            identifier="inactive-model",
            provider="openai",
            is_active=False,
            supports_temperature=False,
            supports_effort=True,
            default_effort="xhigh",
        )

        response = self.client.get("/api/llms/all_models/")

        self.assertEqual(response.status_code, 200)
        row = next(item for item in response.data if item["id"] == inactive.id)
        self.assertFalse(row["is_active"])
        self.assertFalse(row["supports_temperature"])
        self.assertTrue(row["supports_effort"])
        self.assertEqual(row["default_effort"], "xhigh")


class ClaudeCapabilityPayloadTests(TestCase):
    def test_temperature_is_omitted_when_model_does_not_support_it(self):
        llm = LLM(
            name="Claude Opus 4.8",
            identifier="claude-opus-4-8-20260527",
            provider="claude",
            supports_temperature=False,
            supports_effort=True,
            supports_adaptive_thinking=True,
            default_effort="high",
            default_adaptive_thinking_enabled=True,
        )

        params = ClaudeService(llm=llm, api_key="test")._build_stream_params(
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=100,
            temperature=0.7,
            effort="xhigh",
            tools=None,
        )

        self.assertNotIn("temperature", params)
        self.assertEqual(params["extra_body"]["output_config"], {"effort": "xhigh"})
        self.assertEqual(
            params["thinking"], {"type": "adaptive", "display": "summarized"}
        )

    def test_temperature_is_sent_for_temperature_capable_models(self):
        llm = LLM(
            name="Claude Sonnet",
            identifier="claude-sonnet-4-20250514",
            provider="claude",
            supports_temperature=True,
        )

        params = ClaudeService(llm=llm, api_key="test")._build_stream_params(
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=100,
            temperature=0.3,
            effort=None,
            tools=None,
        )

        self.assertEqual(params["temperature"], 0.3)
        self.assertNotIn("output_config", params)
        self.assertNotIn("thinking", params)


class OpenAICapabilityPayloadTests(TestCase):
    def test_gpt_5_family_uses_max_completion_tokens(self):
        llm = LLM(
            name="GPT-5.4",
            identifier="gpt-5.4",
            provider="openai",
            is_reasoning=False,
            supports_temperature=True,
        )

        params = OpenAIService(llm=llm, api_key="test")._build_chat_completion_params(
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=100,
            temperature=0.7,
        )

        self.assertEqual(params["max_completion_tokens"], 100)
        self.assertNotIn("max_tokens", params)
        self.assertEqual(params["temperature"], 0.7)

    def test_temperature_is_omitted_for_gpt_5_models_without_sampling(self):
        llm = LLM(
            name="GPT-5.5",
            identifier="gpt-5.5",
            provider="openai",
            is_reasoning=True,
            supports_temperature=False,
        )

        params = OpenAIService(llm=llm, api_key="test")._build_chat_completion_params(
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=100,
            temperature=0.7,
        )

        self.assertEqual(params["max_completion_tokens"], 100)
        self.assertNotIn("temperature", params)


class ConversationIdTests(TestCase):
    def test_new_id_skips_one_held_by_a_soft_deleted_conversation(self):
        Conversation.active_objects.create(conversation_id="TAKEN123", is_deleted=True)
        rolls = iter([list("TAKEN123"), list("FRESH123")])
        with mock.patch(
            "conversations.models.random.choices",
            side_effect=lambda *a, **k: next(rolls),
        ):
            conversation = Conversation.active_objects.create()
        self.assertEqual(conversation.conversation_id, "FRESH123")
