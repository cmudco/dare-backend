from allauth.account.models import EmailAddress, EmailConfirmationHMAC
from django.contrib.admin.sites import site
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory, TestCase
from rest_framework import status
from rest_framework.test import APIClient

User = get_user_model()


class VerifyEmailRegistrationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = "/users/api/dj-rest-auth/registration/verify-email/"
        self.user = User.objects.create_user(
            email="verify-email@example.com",
            password="password",
        )
        self.email_address = EmailAddress.objects.create(
            user=self.user,
            email=self.user.email,
            primary=True,
            verified=False,
        )
        self.key = EmailConfirmationHMAC.create(self.email_address).key

    def test_verify_email_returns_tokens_for_fresh_dare_user(self):
        response = self.client.post(self.url, {"key": self.key}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["detail"], "ok")
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)

        self.email_address.refresh_from_db()
        self.assertTrue(self.email_address.verified)

    def test_reused_verified_email_link_returns_success_without_tokens(self):
        self.client.post(self.url, {"key": self.key}, format="json")

        response = self.client.post(self.url, {"key": self.key}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["detail"], "Email already verified")
        self.assertTrue(response.data["already_verified"])
        self.assertTrue(response.json()["alreadyVerified"])
        self.assertNotIn("access", response.data)
        self.assertNotIn("refresh", response.data)

    def test_invalid_verification_key_still_returns_not_found(self):
        response = self.client.post(self.url, {"key": "invalid-key"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class MarkEmailVerifiedAdminActionTests(TestCase):
    def test_creates_verified_primary_address_for_user_without_one(self):
        user = User.objects.create_superuser(
            email="admin-no-address@example.com", password="pw"
        )
        request = RequestFactory().post("/")
        request.user = user
        request.session = {}
        request._messages = FallbackStorage(request)

        site._registry[User].mark_email_verified(
            request, User.objects.filter(pk=user.pk)
        )

        address = EmailAddress.objects.get(user=user)
        self.assertTrue(address.verified)
        self.assertTrue(address.primary)


class LoginEmailCaseTests(TestCase):
    url = "/users/api/dj-rest-auth/login/"

    def setUp(self):
        self.client = APIClient()

    def make_user(self, email, password="correct-pw"):
        user = User.objects.create_user(email=email, password=password)
        EmailAddress.objects.create(user=user, email=email, primary=True, verified=True)
        return user

    def login(self, email, password="correct-pw"):
        return self.client.post(
            self.url, {"email": email, "password": password}, format="json"
        )

    def test_capitalized_email_logs_into_lowercase_account(self):
        user = self.make_user("jane@school.edu")

        response = self.login("Jane@school.edu")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["user"]["email"], user.email)

    def test_surrounding_spaces_are_ignored(self):
        self.make_user("jane@school.edu")

        response = self.login("  Jane@school.edu ")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

    def test_lowercase_email_logs_into_account_stored_with_capitals(self):
        self.make_user("Jane@school.edu")

        response = self.login("jane@school.edu")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

    def test_wrong_password_still_fails(self):
        self.make_user("jane@school.edu")

        response = self.login("Jane@school.edu", password="wrong-pw")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_accounts_differing_only_by_case_log_in_by_exact_email(self):
        lower = self.make_user("jane@school.edu", password="lower-pw")
        upper = self.make_user("Jane@school.edu", password="upper-pw")

        lower_login = self.login("jane@school.edu", password="lower-pw")
        upper_login = self.login("Jane@school.edu", password="upper-pw")

        self.assertEqual(lower_login.data["user"]["email"], lower.email)
        self.assertEqual(upper_login.data["user"]["email"], upper.email)

    def test_ambiguous_case_variant_fails_without_error(self):
        self.make_user("jane@school.edu")
        self.make_user("Jane@school.edu")

        response = self.login("JANE@school.edu")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
