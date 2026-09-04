from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from core.services.vision_model_service import (
    VisionModelCandidate,
    VisionModelNotOffered,
)

CANDIDATES = [
    VisionModelCandidate(
        "gemini-3.1-flash-lite", "Gemini 3.1 Flash-Lite", Decimal("0.00425"), True
    ),
    VisionModelCandidate("gpt-4o-mini", "GPT-4o Mini", Decimal("0.00195"), False),
]


def route_for(identifier):
    return SimpleNamespace(model=SimpleNamespace(identifier=identifier))


@patch(
    "files.api.views.resolve_vision_model",
    side_effect=lambda user, requested="": route_for(
        requested or "gemini-3.1-flash-lite"
    ),
)
@patch(
    "files.api.views.select_vision_model",
    side_effect=lambda user, identifier: route_for(identifier),
)
@patch("files.api.views.list_vision_models", return_value=CANDIDATES)
class VisionModelsApiTests(APITestCase):
    url = "/api/files/vision-models/"

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="vision@example.com", password="pw"
        )

    def test_requires_authentication(self, _list, _select, _resolve):
        self.assertEqual(
            self.client.get(self.url).status_code, status.HTTP_401_UNAUTHORIZED
        )

    def test_lists_candidates_and_the_recommendation_when_nothing_is_chosen(
        self, _list, _select, _resolve
    ):
        self.client.force_authenticate(user=self.user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["selected"], "gemini-3.1-flash-lite")
        self.assertEqual(
            [(m["identifier"], m["recommended"]) for m in response.data["models"]],
            [("gemini-3.1-flash-lite", True), ("gpt-4o-mini", False)],
        )
        self.assertEqual(
            response.data["models"][1]["estimated_cost_per_page"], "0.00195000"
        )

    def test_patch_stores_an_offered_model_as_the_default(
        self, _list, _select, _resolve
    ):
        self.client.force_authenticate(user=self.user)

        response = self.client.patch(
            self.url, {"modelIdentifier": "gpt-4o-mini"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["selected"], "gpt-4o-mini")
        self.user.refresh_from_db()
        self.assertEqual(self.user.vision_model, "gpt-4o-mini")

    def test_patch_rejects_a_model_the_wallet_does_not_offer(
        self, _list, select, _resolve
    ):
        select.side_effect = VisionModelNotOffered("text-only is unavailable")
        self.client.force_authenticate(user=self.user)

        response = self.client.patch(
            self.url, {"modelIdentifier": "text-only"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.user.refresh_from_db()
        self.assertEqual(self.user.vision_model, "")
