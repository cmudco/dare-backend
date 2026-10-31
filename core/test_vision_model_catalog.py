from django.contrib.auth import get_user_model
from django.test import TestCase

from billing.constants import UserWalletPreferenceTypeChoice
from conversations.models import LLM
from core.services.vision_model_service import _catalog_routes


class CatalogVisionRoutesTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(email="vision@example.com")
        LLM.objects.filter(is_active=True).update(is_active=False)

    def identifiers(self):
        return [
            route.model.identifier
            for route in _catalog_routes(self.user, UserWalletPreferenceTypeChoice.DARE)
        ]

    def test_custom_model_flagged_for_vision_is_offered(self):
        LLM.objects.create(
            name="Campus VL",
            identifier="campus-vl",
            provider="custom",
            base_url="http://vllm.local/v1",
            supports_vision=True,
        )
        LLM.objects.create(
            name="Campus text",
            identifier="campus-text",
            provider="custom",
            base_url="http://vllm.local/v1",
            supports_vision=False,
        )

        self.assertEqual(self.identifiers(), ["campus-vl"])

    def test_known_providers_still_need_a_vision_family(self):
        LLM.objects.create(
            name="GPT-6 Test",
            identifier="gpt-6-test-vision",
            provider="openai",
            supports_vision=True,
        )
        LLM.objects.create(
            name="Embedder",
            identifier="text-embedding-9-test",
            provider="openai",
            supports_vision=True,
        )

        self.assertEqual(self.identifiers(), ["gpt-6-test-vision"])
