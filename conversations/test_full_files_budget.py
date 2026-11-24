from unittest.mock import AsyncMock

from django.test import TransactionTestCase

from conversations.models import LLM, Conversation
from conversations.services.message_coordinator import MessageCoordinator
from files.models import File
from users.models import User


class FullFilesBudgetTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="reader@example.test", password="x")
        conversation = Conversation.active_objects.create(user=self.user, title="Draft")
        self.coordinator = MessageCoordinator(
            conversation=conversation, user=self.user, send_callback=AsyncMock()
        )
        self.draft = self.file("draft.pdf", text="x" * 400_000)
        self.photo = self.file("photo.png", is_media=True)

    def file(self, name, *, text=None, is_media=False):
        return File.active_objects.create(
            user=self.user,
            file=f"files/{name}",
            name=name,
            is_media=is_media,
            extracted_text=text,
        )

    async def read(self, max_input_tokens):
        message_data = {
            "file_ids": [self.draft.id, self.photo.id],
            "embedding_ids": [],
        }
        await self.coordinator._search_files_too_large_to_read(
            message_data, LLM(max_input_tokens=max_input_tokens)
        )
        return message_data

    async def test_documents_that_fit_are_read_in_full(self):
        message_data = await self.read(1_000_000)

        self.assertEqual(message_data["file_ids"], [self.draft.id, self.photo.id])
        self.assertEqual(message_data["embedding_ids"], [])

    async def test_documents_too_large_for_the_model_are_searched(self):
        message_data = await self.read(128_000)

        self.assertEqual(message_data["file_ids"], [self.photo.id])
        self.assertEqual(message_data["embedding_ids"], [self.draft.id])
