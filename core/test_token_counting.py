from unittest.mock import Mock, patch

from django.test import TestCase

from conversations.models import LLM
from core.services import token_counting
from core.services.api_key_service import ProviderKeyMissing
from files.models import File
from users.models import User


class MeasureFilesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="tokens@example.test", password="x")
        self.file = File(user=self.user, file="files/draft.txt", name="draft.txt")
        self.file.set_extracted_text("The river carries sediment. " * 50)
        self.file.save()
        self.files = File.active_objects.filter(user=self.user)
        self.claude = LLM(provider="claude", identifier="claude-haiku-5-5")

    def test_openai_uses_the_stored_o200k_count(self):
        size = token_counting.measure_files(
            self.files, LLM(provider="openai", identifier="gpt-6.1-terra")
        )

        self.assertEqual(size.estimated_tokens, self.file.token_count)
        self.assertFalse(size.estimated)

    def test_claude_counts_once_through_its_api_and_reuses_the_count(self):
        counter = Mock(return_value=777)
        with patch.object(token_counting, "_counter", return_value=counter):
            first = token_counting.measure_files(self.files, self.claude)
            second = token_counting.measure_files(self.files, self.claude)

        self.assertEqual((first.estimated_tokens, first.estimated), (777, False))
        self.assertEqual(second.estimated_tokens, 777)
        counter.assert_called_once()
        self.file.refresh_from_db()
        self.assertEqual(
            self.file.provider_token_counts, {"claude:claude-haiku-5-5": 777}
        )

    def test_without_a_key_claude_gets_a_labelled_estimate(self):
        with patch.object(
            token_counting, "_counter", side_effect=ProviderKeyMissing("claude")
        ):
            size = token_counting.measure_files(self.files, self.claude)

        self.assertTrue(size.estimated)
        self.assertEqual(size.estimated_tokens, -(-self.file.token_count * 16 // 10))

    def test_new_text_clears_the_provider_counts(self):
        self.file.provider_token_counts = {"claude:claude-haiku-5-5": 777}
        self.file.set_extracted_text("A different text.")

        self.assertEqual(self.file.provider_token_counts, {})
