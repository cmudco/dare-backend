import secrets

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from users.constants import AccessCodeProvisionerChoice, RoleChoice
from users.models import AccessCodeGroup

User = get_user_model()

ENSURE_URL = "/users/api/internal/voice-access-codes/"
CHECK_URL = "/users/api/access-codes/check/"
REGISTER_URL = "/users/api/dj-rest-auth/registration/"
KEY_HEADERS = {"HTTP_X_INTERNAL_KEY": "shared-secret"}
# Generated per run so no credential-looking literal lives in the repo.
TEST_PASSWORD = secrets.token_urlsafe(18)


@override_settings(DARE_INTERNAL_KEY="shared-secret")
class InternalVoiceAccessCodeViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def ensure(self, code, **extra):
        return self.client.post(
            ENSURE_URL,
            {"access_code": code, "action": "ensure", **extra},
            format="json",
            **KEY_HEADERS,
        )

    def test_rejects_missing_or_wrong_internal_key(self):
        missing = self.client.post(
            ENSURE_URL, {"access_code": "VOICE-1"}, format="json"
        )
        wrong = self.client.post(
            ENSURE_URL,
            {"access_code": "VOICE-1"},
            format="json",
            HTTP_X_INTERNAL_KEY="nope",
        )

        self.assertEqual(missing.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(wrong.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(AccessCodeGroup.objects.filter(access_code="VOICE-1").exists())

    def test_ensure_creates_researcher_group_and_is_idempotent(self):
        first = self.ensure("VOICE-1")
        second = self.ensure("VOICE-1")

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertTrue(first.data["created"])
        self.assertFalse(second.data["created"])
        group = AccessCodeGroup.objects.get(access_code="VOICE-1")
        self.assertEqual(group.default_role, RoleChoice.RESEARCHER)
        self.assertEqual(
            group.provisioned_by, AccessCodeProvisionerChoice.SOCRATIC_VOICE
        )
        self.assertTrue(group.is_available)
        self.assertEqual(
            AccessCodeGroup.objects.filter(access_code="VOICE-1").count(), 1
        )

    def test_ensure_never_touches_a_group_it_did_not_provision(self):
        AccessCodeGroup.objects.create(
            access_code="CLASS-42", max_capacity=10, default_role=RoleChoice.SUPERVISOR
        )

        response = self.ensure("CLASS-42", activate=True)

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        group = AccessCodeGroup.objects.get(access_code="CLASS-42")
        self.assertEqual(group.default_role, RoleChoice.SUPERVISOR)
        self.assertEqual(group.provisioned_by, AccessCodeProvisionerChoice.ADMIN)

    def test_ensure_only_reactivates_when_explicitly_asked(self):
        self.ensure("VOICE-1")
        AccessCodeGroup.objects.filter(access_code="VOICE-1").update(is_active=False)

        passive = self.ensure("VOICE-1")
        self.assertFalse(passive.data["is_available"])
        self.assertFalse(AccessCodeGroup.objects.get(access_code="VOICE-1").is_active)

        active = self.ensure("VOICE-1", activate=True)
        self.assertTrue(active.data["is_available"])
        self.assertTrue(AccessCodeGroup.objects.get(access_code="VOICE-1").is_active)

    def test_deactivate_only_affects_voice_groups(self):
        self.ensure("VOICE-1")
        AccessCodeGroup.objects.create(
            access_code="CLASS-42", max_capacity=10, default_role=RoleChoice.USER
        )

        voice = self.client.post(
            ENSURE_URL,
            {"access_code": "VOICE-1", "action": "deactivate"},
            format="json",
            **KEY_HEADERS,
        )
        admin = self.client.post(
            ENSURE_URL,
            {"access_code": "CLASS-42", "action": "deactivate"},
            format="json",
            **KEY_HEADERS,
        )
        unknown = self.client.post(
            ENSURE_URL,
            {"access_code": "MISSING", "action": "deactivate"},
            format="json",
            **KEY_HEADERS,
        )

        self.assertEqual(voice.status_code, status.HTTP_200_OK)
        self.assertFalse(AccessCodeGroup.objects.get(access_code="VOICE-1").is_active)
        self.assertEqual(admin.status_code, status.HTTP_409_CONFLICT)
        self.assertTrue(AccessCodeGroup.objects.get(access_code="CLASS-42").is_active)
        self.assertEqual(unknown.status_code, status.HTTP_200_OK)

    def test_rejects_blank_code_and_unknown_action(self):
        blank = self.ensure("   ")
        bad_action = self.client.post(
            ENSURE_URL,
            {"access_code": "VOICE-1", "action": "promote"},
            format="json",
            **KEY_HEADERS,
        )

        self.assertEqual(blank.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(bad_action.status_code, status.HTTP_400_BAD_REQUEST)


@override_settings(DARE_INTERNAL_KEY="shared-secret")
class VoiceAccessCodeRegistrationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.post(
            ENSURE_URL,
            {"access_code": "VOICE-1", "action": "ensure"},
            format="json",
            **KEY_HEADERS,
        )

    def test_check_exposes_provisioner(self):
        response = self.client.post(
            CHECK_URL, {"access_code": "VOICE-1"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["exists"])
        self.assertEqual(response.data["default_role"], RoleChoice.RESEARCHER)
        self.assertEqual(
            response.data["provisioned_by"], AccessCodeProvisionerChoice.SOCRATIC_VOICE
        )

    @override_settings(
        SOCRATIC_BOTS_FRONTEND_URL="http://sb.example",
        ACCOUNT_EMAIL_VERIFICATION="none",
    )
    def test_registration_with_voice_code_assigns_researcher_role(self):
        response = self.client.post(
            REGISTER_URL,
            {
                "email": "new-researcher@example.com",
                "password1": TEST_PASSWORD,
                "password2": TEST_PASSWORD,
                "name": "New Researcher",
                "access_code": "VOICE-1",
            },
            format="json",
            HTTP_ORIGIN="http://sb.example",
        )

        self.assertIn(
            response.status_code,
            (status.HTTP_200_OK, status.HTTP_201_CREATED),
            response.data,
        )
        user = User.objects.get(email="new-researcher@example.com")
        self.assertEqual(user.platform_role, RoleChoice.RESEARCHER)
        self.assertEqual(user.access_code_group.access_code, "VOICE-1")
        self.assertEqual(user.access_code_group.current_usage, 1)
