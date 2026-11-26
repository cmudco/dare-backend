"""A file's tokens on each provider, counted once and kept on the file."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from conversations.models import LLM
from core.services import token_counting
from core.services.token_counting import count_tokens
from files.models import File

TEXT = "The river carries sediment downstream. " * 40


class FileTokensTests(TestCase):
    def setUp(self):
        users = get_user_model().objects
        self.user = users.create_user(email="tokens@example.test", password="x")
        self.other = users.create_user(email="tokens-other@example.test", password="x")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.openai = LLM.objects.create(
            name="Terra", identifier="terra", provider="openai", max_input_tokens=1000
        )

    def make_file(self, user):
        return File.active_objects.create(
            user=user, file="files/notes.md", name="notes.md", extracted_text=TEXT
        )

    def test_returns_and_keeps_each_providers_count(self):
        file = self.make_file(self.user)

        with patch.object(
            token_counting, "provider_models", return_value=[self.openai]
        ):
            response = self.client.get(f"/api/files/{file.id}/tokens/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            [
                {
                    "provider": "openai",
                    "model": "Terra",
                    "tokens": count_tokens(TEXT),
                    "estimated": False,
                }
            ],
        )
        file.refresh_from_db()
        self.assertEqual(file.token_counts, {"o200k": count_tokens(TEXT)})

    def test_another_users_file_is_not_found(self):
        theirs = self.make_file(self.other)

        response = self.client.get(f"/api/files/{theirs.id}/tokens/")

        self.assertEqual(response.status_code, 404)
