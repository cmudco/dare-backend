from unittest.mock import Mock, patch

import httpx
from django.core.files.base import ContentFile
from django.test import TestCase

from conversations.models import LLM
from core.services import token_counting
from core.services.api_key_service import ProviderKeyMissing
from core.services.ingestion_lifecycle import persist_ingestion_file
from core.services.token_counting import count_tokens, measure_files
from files.models import File
from users.models import User

TEXT = "The river carries sediment. " * 50


class MeasureFilesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="tokens@example.test", password="x")
        self.file = self.make_file("draft.txt", text=TEXT)
        self.files = File.active_objects.filter(user=self.user)
        self.claude = LLM(provider="claude", identifier="claude-haiku-5-5")

    def make_file(self, name, *, text=None, size=0):
        return File.active_objects.create(
            user=self.user,
            file=f"files/{name}",
            name=name,
            size=size,
            extracted_text=text,
        )

    def test_openai_counts_o200k_once_and_keeps_it(self):
        terra = LLM(provider="openai", identifier="gpt-6.1-terra")

        size = measure_files(self.files, terra)

        self.assertEqual(
            (size.estimated_tokens, size.estimated), (count_tokens(TEXT), False)
        )
        self.file.refresh_from_db()
        self.assertEqual(self.file.token_counts, {"o200k": count_tokens(TEXT)})

    def test_claude_counts_once_through_its_api_and_reuses_the_count(self):
        counter = Mock(return_value=777)
        with patch.object(token_counting, "_counter", return_value=counter):
            first = measure_files(self.files, self.claude)
            second = measure_files(self.files, self.claude)

        self.assertEqual((first.estimated_tokens, first.estimated), (777, False))
        self.assertEqual(second.estimated_tokens, 777)
        counter.assert_called_once()

    def test_without_a_key_claude_gets_a_labelled_estimate(self):
        with patch.object(
            token_counting, "_counter", side_effect=ProviderKeyMissing("claude")
        ):
            size = measure_files(self.files, self.claude)

        self.assertTrue(size.estimated)
        self.assertEqual(size.estimated_tokens, -(-count_tokens(TEXT) * 16 // 10))

    def test_an_unparsed_file_counts_the_text_the_chat_reads(self):
        pending = File.active_objects.create(
            user=self.user, name="pending.txt", size=10**6, extracted_text=None
        )
        pending.file.save("pending.txt", ContentFile(TEXT.encode()), save=True)
        self.addCleanup(pending.file.delete, save=False)

        size = measure_files(self.files, None)

        self.assertEqual(size.file_count, 2)
        self.assertEqual(size.estimated_tokens, 2 * count_tokens(TEXT))
        self.assertFalse(size.estimated)

    def test_new_text_clears_the_counts(self):
        measure_files(self.files, None)
        self.file.refresh_from_db()
        self.file.extracted_text = "A different text."

        persist_ingestion_file(self.file, ["extracted_text"])

        self.file.refresh_from_db()
        self.assertEqual(self.file.token_counts, {})

    def test_a_network_failure_leaves_the_count_for_later(self):
        counter = Mock(side_effect=httpx.ConnectError("offline"))
        with patch.object(token_counting, "_counter", return_value=counter):
            size = measure_files(self.files, self.claude)

        self.assertTrue(size.estimated)
        self.file.refresh_from_db()
        self.assertNotIn("claude:claude-haiku-5-5", self.file.token_counts)
