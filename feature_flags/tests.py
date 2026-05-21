from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from feature_flags.models import (
    FeatureFlag,
    GroupFeatureOverride,
    UserFeatureOverride,
)
from feature_flags.services import resolve_flags_for_user
from users.models import AccessCodeGroup

User = get_user_model()


class ResolveFlagsForUserTests(TestCase):
    def setUp(self):
        # Wipe any seeded flags from data migrations so tests are deterministic.
        FeatureFlag.objects.all().delete()

        self.flag_on = FeatureFlag.objects.create(
            key="enable_alpha", default_enabled=True
        )
        self.flag_off = FeatureFlag.objects.create(
            key="enable_beta", default_enabled=False
        )

        self.group = AccessCodeGroup.objects.create(
            access_code="TEST-CODE",
            max_capacity=10,
        )
        self.user = User.objects.create_user(
            email="alice@example.com",
            password="pw",
            access_code_group=self.group,
        )

    def test_defaults_only(self):
        # No overrides anywhere — flags resolve to their default_enabled.
        resolved = resolve_flags_for_user(self.user)
        self.assertEqual(resolved, {"enable_alpha": True, "enable_beta": False})

    def test_group_override_beats_default(self):
        GroupFeatureOverride.objects.create(
            flag=self.flag_off, group=self.group, enabled=True
        )
        GroupFeatureOverride.objects.create(
            flag=self.flag_on, group=self.group, enabled=False
        )
        resolved = resolve_flags_for_user(self.user)
        self.assertEqual(resolved, {"enable_alpha": False, "enable_beta": True})

    def test_user_override_beats_group_override(self):
        GroupFeatureOverride.objects.create(
            flag=self.flag_off, group=self.group, enabled=True
        )
        UserFeatureOverride.objects.create(
            flag=self.flag_off, user=self.user, enabled=False
        )
        resolved = resolve_flags_for_user(self.user)
        self.assertFalse(resolved["enable_beta"])

    def test_user_with_no_group(self):
        # User without an access_code_group should still resolve defaults.
        loner = User.objects.create_user(email="bob@example.com", password="pw")
        resolved = resolve_flags_for_user(loner)
        self.assertEqual(resolved, {"enable_alpha": True, "enable_beta": False})


class MyFeatureFlagsViewTests(TestCase):
    def setUp(self):
        FeatureFlag.objects.all().delete()
        FeatureFlag.objects.create(key="enable_byok", default_enabled=True)
        self.user = User.objects.create_user(email="api@example.com", password="pw")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_returns_resolved_flags(self):
        url = reverse("feature_flags:feature_flags_api:my-feature-flags")
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Internal data keeps snake_case; the camelCase renderer transforms
        # keys at render time, which is what the frontend actually sees.
        self.assertEqual(response.data, {"flags": {"enable_byok": True}})
        self.assertEqual(response.json(), {"flags": {"enableByok": True}})

    def test_requires_authentication(self):
        anon = APIClient()
        url = reverse("feature_flags:feature_flags_api:my-feature-flags")
        response = anon.get(url)
        self.assertIn(
            response.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )
