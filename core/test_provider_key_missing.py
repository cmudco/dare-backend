from unittest.mock import patch

from django.test import TestCase

from config import env
from conversations.constants import Provider
from core.services.api_key_service import (
    ProviderKeyMissing,
    get_provider_api_key_sync,
    has_provider_api_key,
)


class ProviderKeyMissingTests(TestCase):
    def test_provider_without_any_key_is_reported_missing(self):
        with patch.object(env, "OPENAI_API_KEY", ""):
            with self.assertRaises(ProviderKeyMissing):
                get_provider_api_key_sync(Provider.OPENAI.value)
            self.assertFalse(has_provider_api_key(Provider.OPENAI.value))

    def test_environment_key_is_used_when_set(self):
        with patch.object(env, "OPENAI_API_KEY", "sk-test"):
            self.assertEqual(
                get_provider_api_key_sync(Provider.OPENAI.value), "sk-test"
            )
