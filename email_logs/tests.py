import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from config.email import EMAIL_BACKENDS, resolve_email_provider
from users.models import AccessCodeGroup

BASE_DIR = Path(__file__).resolve().parent.parent
TEST_PASSWORD = secrets.token_urlsafe(18)
EMAIL_ENV_KEYS = (
    "EMAIL_PROVIDER",
    "EMAIL_HOST",
    "EMAIL_PORT",
    "EMAIL_HOST_USER",
    "EMAIL_HOST_PASSWORD",
    "EMAIL_USE_TLS",
    "EMAIL_USE_SSL",
    "RESEND_API_KEY",
)


class ResolveEmailProviderTests(SimpleTestCase):
    def test_explicit_provider_wins_over_inference(self):
        for provider in EMAIL_BACKENDS:
            with self.subTest(provider=provider):
                self.assertEqual(
                    resolve_email_provider(provider, "smtp.example", "user", "secret"),
                    provider,
                )

    def test_explicit_provider_is_case_and_space_insensitive(self):
        self.assertEqual(resolve_email_provider(" SMTP ", None, None, None), "smtp")

    def test_unknown_provider_fails_loudly(self):
        with self.assertRaises(ImproperlyConfigured):
            resolve_email_provider("sendgrid", None, None, None)

    def test_unset_provider_keeps_legacy_inference(self):
        cases = [
            ((None, "smtp.example", "user", "secret"), "resend"),
            (("", None, "user", "secret"), "resend"),
            ((None, "smtp.example", "user", None), "smtp"),
            ((None, "smtp.example", None, None), "smtp"),
            ((None, None, "user", None), "console"),
            ((None, None, None, None), "console"),
        ]
        for args, expected in cases:
            with self.subTest(args=args):
                self.assertEqual(resolve_email_provider(*args), expected)


class EmailSettingsLoadTests(SimpleTestCase):
    """Loads the real settings module in a clean process with a given environment."""

    def load_settings(self, **email_env):
        env = {key: "" for key in EMAIL_ENV_KEYS}
        env.update(email_env)
        env = {**os.environ, **env, "DJANGO_SETTINGS_MODULE": "config.settings.local"}
        script = (
            "import json; from django.conf import settings as s; print(json.dumps({k: getattr(s, k) for k in "
            "['EMAIL_PROVIDER','EMAIL_BACKEND','EMAIL_HOST','EMAIL_PORT','EMAIL_HOST_USER',"
            "'EMAIL_HOST_PASSWORD','EMAIL_USE_TLS','EMAIL_USE_SSL','RESEND_API_KEY',"
            "'EMAIL_DELIVERY_CONFIGURED']}))"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=BASE_DIR,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_smtp_with_credentials_and_tls(self):
        loaded = self.load_settings(
            EMAIL_PROVIDER="smtp",
            EMAIL_HOST="smtp.example.edu",
            EMAIL_PORT="587",
            EMAIL_HOST_USER="mailer@example.edu",
            EMAIL_HOST_PASSWORD="smtp-secret",
            EMAIL_USE_TLS="True",
        )
        self.assertEqual(loaded["EMAIL_BACKEND"], EMAIL_BACKENDS["smtp"])
        self.assertEqual(loaded["EMAIL_HOST_USER"], "mailer@example.edu")
        self.assertEqual(loaded["EMAIL_HOST_PASSWORD"], "smtp-secret")
        self.assertEqual(loaded["EMAIL_PORT"], 587)
        self.assertTrue(loaded["EMAIL_USE_TLS"])
        self.assertFalse(loaded["EMAIL_USE_SSL"])
        self.assertTrue(loaded["EMAIL_DELIVERY_CONFIGURED"])

    def test_ssl_passthrough(self):
        loaded = self.load_settings(
            EMAIL_PROVIDER="smtp", EMAIL_HOST="smtp.example.edu", EMAIL_USE_SSL="true"
        )
        self.assertTrue(loaded["EMAIL_USE_SSL"])
        self.assertFalse(loaded["EMAIL_USE_TLS"])

    def test_unset_provider_with_user_and_password_still_selects_resend(self):
        loaded = self.load_settings(
            EMAIL_HOST_USER="resend", EMAIL_HOST_PASSWORD="re_legacy_key"
        )
        self.assertEqual(loaded["EMAIL_PROVIDER"], "resend")
        self.assertEqual(loaded["EMAIL_BACKEND"], EMAIL_BACKENDS["resend"])
        self.assertEqual(loaded["RESEND_API_KEY"], "re_legacy_key")

    def test_resend_api_key_takes_precedence(self):
        loaded = self.load_settings(
            EMAIL_PROVIDER="resend",
            EMAIL_HOST_PASSWORD="re_legacy_key",
            RESEND_API_KEY="re_new_key",
        )
        self.assertEqual(loaded["RESEND_API_KEY"], "re_new_key")

    def test_nothing_set_is_console_without_delivery(self):
        loaded = self.load_settings()
        self.assertEqual(loaded["EMAIL_BACKEND"], EMAIL_BACKENDS["console"])
        self.assertEqual(loaded["EMAIL_PORT"], 587)
        self.assertFalse(loaded["EMAIL_DELIVERY_CONFIGURED"])


class EmailDeliveryFlagResponseTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        AccessCodeGroup.objects.create(access_code="EMAIL-1", max_capacity=5)

    def register(self):
        return self.client.post(
            "/users/api/dj-rest-auth/registration/",
            {
                "email": "new-user@example.com",
                "password1": TEST_PASSWORD,
                "password2": TEST_PASSWORD,
                "name": "New User",
                "access_code": "EMAIL-1",
            },
            format="json",
        )

    def resend(self):
        return self.client.post(
            "/users/api/dj-rest-auth/registration/resend-email/",
            {"email": "new-user@example.com"},
            format="json",
        )

    @override_settings(EMAIL_DELIVERY_CONFIGURED=False)
    def test_console_provider_reports_no_delivery(self):
        register = self.register()
        resend = self.resend()

        self.assertEqual(register.status_code, 201, register.data)
        self.assertIs(register.data["email_delivery_configured"], False)
        self.assertEqual(resend.status_code, 200, resend.data)
        self.assertIs(resend.data["email_delivery_configured"], False)

    @override_settings(EMAIL_DELIVERY_CONFIGURED=True)
    def test_real_provider_reports_delivery(self):
        register = self.register()
        resend = self.resend()

        self.assertIs(register.data["email_delivery_configured"], True)
        self.assertIs(resend.data["email_delivery_configured"], True)
