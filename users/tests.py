from allauth.account.models import EmailAddress, EmailConfirmationHMAC
from django.contrib.auth import get_user_model
from django.test import TestCase
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
