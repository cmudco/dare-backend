from django.test import TestCase
from django.urls import reverse

from conversations.models import LLM
from core.models import PlatformSettings
from users.models import User


class PlatformSettingsAdminTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            email="platform-admin@example.com", password="x"
        )
        self.client.force_login(self.admin)
        self.text_model, _ = LLM.objects.update_or_create(
            identifier="test-admin-text-model",
            defaults={"name": "Admin Text", "provider": "claude", "is_active": True},
        )
        self.change_url = reverse(
            "admin:core_platformsettings_change", args=[PlatformSettings.SINGLETON_ID]
        )

    def test_list_view_opens_the_single_settings_row(self):
        response = self.client.get(reverse("admin:core_platformsettings_changelist"))

        self.assertRedirects(response, self.change_url)
        self.assertEqual(PlatformSettings.objects.count(), 1)

    def test_saving_a_text_model_makes_it_the_background_model_in_use(self):
        self.client.get(reverse("admin:core_platformsettings_changelist"))

        response = self.client.post(
            self.change_url, {"background_model": self.text_model.pk}
        )

        self.assertEqual(response.status_code, 302)
        settings = PlatformSettings.objects.get()
        self.assertEqual(settings.background_model, self.text_model)
        page = self.client.get(self.change_url).content.decode()
        self.assertIn("Admin Text (test-admin-text-model), from this setting", page)

    def test_image_and_inactive_models_are_not_offered(self):
        image_model, _ = LLM.objects.update_or_create(
            identifier="test-admin-image-model",
            defaults={
                "name": "Admin Image",
                "provider": "openai",
                "is_active": True,
                "is_image_generator": True,
            },
        )
        inactive_model, _ = LLM.objects.update_or_create(
            identifier="test-admin-inactive-model",
            defaults={
                "name": "Admin Inactive",
                "provider": "claude",
                "is_active": False,
            },
        )
        self.client.get(reverse("admin:core_platformsettings_changelist"))

        for model in (image_model, inactive_model):
            response = self.client.post(self.change_url, {"background_model": model.pk})

            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["adminform"].form.errors)
        self.assertIsNone(PlatformSettings.objects.get().background_model)
