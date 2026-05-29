from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from conversations.models import LLM
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
        self.assertEqual(params["thinking"], {"type": "adaptive"})

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
            name="GPT-5.5",
            identifier="gpt-5.5",
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
        self.assertNotIn("temperature", params)
