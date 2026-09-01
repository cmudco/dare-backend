from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from billing.constants import LiteLLMKeySourceChoice
from billing.litellm_probe import LiteLLMProbeResult, ProbedModel
from billing.models import LiteLLMKey
from users.models import User


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
)
class LiteLLMBackgroundModelAPITests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="proxy@example.com", password="x")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    @patch("billing.api.views.probe_litellm_connection")
    def test_probe_returns_backend_recommendation(self, probe):
        probe.return_value = LiteLLMProbeResult(
            ok=True,
            models=[
                ProbedModel("gemini/gemini-3.7-flash"),
                ProbedModel("openai/gpt-5.6-luna"),
            ],
        )

        response = self.client.post(
            reverse("billing:api:litellm-keys-test-unsaved"),
            {"baseUrl": "https://proxy.example/v1", "apiKey": "secret"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["suggested_model"], "openai/gpt-5.6-luna")

    def test_create_persists_one_background_model(self):
        response = self.client.post(
            reverse("billing:api:litellm-keys-list"),
            {
                "label": "Personal proxy",
                "baseUrl": "https://proxy.example/v1",
                "apiKey": "secret",
                "backgroundModel": "gemini/gemini-3.7-flash",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        key = LiteLLMKey.objects.get(owner_user=self.user)
        self.assertEqual(key.background_model, "gemini/gemini-3.7-flash")
        self.assertEqual(response.data["background_model"], "gemini/gemini-3.7-flash")

    def test_patch_replaces_the_background_model(self):
        key = LiteLLMKey.objects.create(
            label="Personal proxy",
            base_url="https://proxy.example/v1",
            api_key="secret",
            background_model="old-model",
            source=LiteLLMKeySourceChoice.USER,
            owner_user=self.user,
            created_by=self.user,
        )

        response = self.client.patch(
            reverse("billing:api:litellm-keys-detail", args=[key.pk]),
            {"backgroundModel": "openai/gpt-5.6-luna"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        key.refresh_from_db()
        self.assertEqual(key.background_model, "openai/gpt-5.6-luna")
