from decimal import Decimal

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from billing.constants import UserWalletPreferenceTypeChoice
from conversations.models import LLM
from core.models import PlatformSettings
from users.constants import RoleChoice
from users.models import User

USER_URL = "/api/background-models/"
PLATFORM_URL = "/api/platform/background-models/"


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
)
class BackgroundModelsApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="bg-api@example.com", password="x")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.luna = self._model("gpt-5.6-luna", "openai", "0.20", "1.20")
        self.haiku = self._model("test-api-haiku", "claude", "1", "5")

    def _model(self, identifier, provider, input_rate, output_rate):
        model, _ = LLM.objects.update_or_create(
            identifier=identifier,
            defaults={
                "name": identifier,
                "provider": provider,
                "is_active": True,
                "input_token_rate_per_million": Decimal(input_rate),
                "output_token_rate_per_million": Decimal(output_rate),
            },
        )
        return model

    def test_overview_names_the_model_and_payer_of_every_task(self):
        data = self.client.get(USER_URL).json()

        self.assertEqual(data["walletType"], UserWalletPreferenceTypeChoice.DARE)
        self.assertEqual(
            {task["task"] for task in data["tasks"]},
            {"titles", "summaries", "memory", "query_analysis"},
        )
        for task in data["tasks"]:
            self.assertEqual(task["modelIdentifier"], "gpt-5.6-luna")
            self.assertEqual(task["source"], "environment")
        self.assertFalse(data["canManagePlatform"])

    def test_user_picks_and_clears_their_background_model(self):
        response = self.client.patch(
            USER_URL, {"model": "test-api-haiku"}, format="json"
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.background_model, "test-api-haiku")
        self.assertTrue(
            all(task["source"] == "user" for task in response.json()["tasks"])
        )

        self.client.patch(USER_URL, {"model": ""}, format="json")
        self.user.refresh_from_db()
        self.assertEqual(self.user.background_model, "")

    def test_a_model_the_wallet_cannot_run_is_refused(self):
        image = self._model("test-api-image", "openai", "1", "1")
        image.is_image_generator = True
        image.save(update_fields=["is_image_generator"])

        response = self.client.patch(
            USER_URL, {"model": "test-api-image"}, format="json"
        )

        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertEqual(self.user.background_model, "")

    def test_anonymous_requests_are_rejected(self):
        self.assertEqual(APIClient().get(USER_URL).status_code, 401)
        self.assertEqual(APIClient().get(PLATFORM_URL).status_code, 401)

    def test_platform_settings_are_superadmin_only(self):
        self.assertEqual(self.client.get(PLATFORM_URL).status_code, 403)
        response = self.client.patch(
            PLATFORM_URL, {"default": "test-api-haiku"}, format="json"
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            PlatformSettings.objects.exclude(background_model=None).exists()
        )

    def test_superadmin_sets_the_default_and_a_task_override(self):
        self.user.platform_role = RoleChoice.SUPERADMIN
        self.user.save(update_fields=["platform_role"])

        response = self.client.patch(
            PLATFORM_URL,
            {
                "default": "test-api-haiku",
                "overrides": [{"task": "titles", "model": "gpt-5.6-luna"}],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        settings = PlatformSettings.objects.get()
        self.assertEqual(settings.background_model, self.haiku)
        self.assertEqual(settings.title_model, self.luna)
        tasks = {
            task["task"]: task for task in self.client.get(USER_URL).json()["tasks"]
        }
        self.assertEqual(tasks["titles"]["modelIdentifier"], "gpt-5.6-luna")
        self.assertEqual(tasks["memory"]["modelIdentifier"], "test-api-haiku")
        self.assertTrue(self.client.get(USER_URL).json()["canManagePlatform"])

    def test_superadmin_clears_an_override_and_unknown_models_are_refused(self):
        self.user.platform_role = RoleChoice.SUPERADMIN
        self.user.save(update_fields=["platform_role"])
        PlatformSettings.objects.create(pk=1, memory_model=self.haiku)

        cleared = self.client.patch(
            PLATFORM_URL,
            {"overrides": [{"task": "memory", "model": ""}]},
            format="json",
        )
        refused = self.client.patch(
            PLATFORM_URL, {"default": "no-such-model"}, format="json"
        )

        self.assertEqual(cleared.status_code, 200)
        self.assertEqual(refused.status_code, 400)
        settings = PlatformSettings.objects.get()
        self.assertIsNone(settings.memory_model)
        self.assertIsNone(settings.background_model)
