from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from feature_flags.models import FeatureFlag
from users.constants import RoleChoice


User = get_user_model()


class ResearchFeatureFlagTests(TestCase):
    """Research APIs remain unavailable until the release flag is enabled."""

    def setUp(self):
        self.user = User.objects.create_user(
            email="researcher@example.com",
            password="pw",
            platform_role=RoleChoice.RESEARCHER,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.flag, _ = FeatureFlag.objects.update_or_create(
            key="enable_research",
            defaults={"default_enabled": False},
        )

    def test_research_projects_are_hidden_when_flag_is_disabled(self):
        response = self.client.get("/api/research/projects/")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_research_projects_are_available_when_flag_is_enabled(self):
        self.flag.default_enabled = True
        self.flag.save(update_fields=["default_enabled"])

        response = self.client.get("/api/research/projects/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
