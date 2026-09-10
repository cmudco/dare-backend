import tempfile
from unittest.mock import AsyncMock

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from rest_framework.test import APIRequestFactory

from conversations.api.serializers import MessageSerializer
from conversations.constants import SenderType
from conversations.models import Conversation, Message
from conversations.services.message_helpers.regeneration_helpers import (
    prepare_regeneration_data,
)
from core.services.dtos import LLMQueryRequest, MediaConfig
from core.services.file_upload_service import FileUploadService
from core.services.llm_helpers.history_helpers import get_conversation_history
from core.services.llm_utils.provider_message_converters import ClaudeMessageConverter
from core.services.llm_utils.vision_handlers import ClaudeVisionHandler

PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="
IMAGE = {
    "preview": "data:image/png;base64," + PNG,
    "name": "pixel.png",
    "type": "image/png",
}


class ImageOnlyProviderTests(SimpleTestCase):
    def test_request_accepts_image_only_and_saved_media_retry(self):
        for media in (MediaConfig(images=[IMAGE]), MediaConfig(media_ids=[1])):
            self.assertEqual(LLMQueryRequest(message="", media=media).message, "")

    def test_request_still_rejects_empty_text_without_media(self):
        for text in ("", "   "):
            with self.assertRaisesRegex(ValueError, "Message cannot be empty"):
                LLMQueryRequest(message=text)

    def test_claude_image_only_turn_has_no_empty_text_blocks(self):
        for text in ("", "   "):
            messages = ClaudeVisionHandler.add_images_to_messages(
                [{"role": "user", "content": text}], [IMAGE]
            )
            _, messages = ClaudeMessageConverter.convert(messages)
            self.assertTrue(any(p["type"] == "image" for p in messages[0]["content"]))
            self.assertFalse(
                any(
                    p["type"] == "text" and not p["text"].strip()
                    for p in messages[0]["content"]
                )
            )

    def test_claude_converts_restored_openai_images(self):
        _, messages = ClaudeMessageConverter.convert(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": IMAGE["preview"]}}
                    ],
                }
            ]
        )
        self.assertEqual(messages[0]["content"][0]["type"], "image")
        self.assertEqual(messages[0]["content"][0]["source"]["data"], PNG)


class PersistedImageChatTests(TransactionTestCase):
    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        settings = override_settings(MEDIA_ROOT=self.media.name)
        settings.enable()
        self.addCleanup(settings.disable)
        self.user = get_user_model().objects.create_user(
            email="image-test@example.test", password="test-only"
        )
        self.conversation = Conversation.active_objects.create(
            user=self.user, conversation_id="image-test"
        )
        self.file = FileUploadService.save_base64_image(
            IMAGE["preview"], IMAGE["name"], IMAGE["type"], self.user
        )
        self.message = Message.active_objects.create(
            conversation=self.conversation, sender_type=SenderType.PLAYER, message=""
        )
        self.message.files.add(self.file)
        self.ai = Message.active_objects.create(
            conversation=self.conversation,
            sender_type=SenderType.AI_ASSISTANT,
            message="A pixel.",
        )

    def test_reopened_history_contains_persisted_attachment(self):
        message = Message.active_objects.get(pk=self.message.pk)
        request = APIRequestFactory().get("/conversations/")
        data = MessageSerializer(message, context={"request": request}).data
        self.assertEqual(data["message"], "")
        self.assertEqual(data["files"][0]["id"], self.file.pk)
        self.assertTrue(data["files"][0]["file"].startswith("http://testserver/media/"))
        with self.file.file.open("rb") as image:
            self.assertGreater(len(image.read()), 0)

    def test_followup_replays_saved_image(self):
        Message.active_objects.create(
            conversation=self.conversation,
            sender_type=SenderType.PLAYER,
            message="What color?",
        )
        Message.active_objects.create(
            conversation=self.conversation,
            sender_type=SenderType.AI_ASSISTANT,
            message="",
        )
        history = async_to_sync(get_conversation_history)(self.conversation)
        self.assertIsInstance(history[0]["content"], list)
        self.assertEqual(history[0]["content"][0]["image_url"]["url"], IMAGE["preview"])

    def test_retry_restores_original_image_even_with_empty_prompt(self):
        _, data = async_to_sync(prepare_regeneration_data)(
            self.ai, None, {"media_ids": []}, self.message, AsyncMock()
        )
        self.assertEqual(data["message"], "")
        self.assertIn(self.file.pk, data["media_ids"])

    def test_deleted_image_is_not_replayed_or_restored_on_retry(self):
        self.file.is_deleted = True
        self.file.save(update_fields=["is_deleted"])
        Message.active_objects.create(
            conversation=self.conversation,
            sender_type=SenderType.PLAYER,
            message="Next",
        )
        Message.active_objects.create(
            conversation=self.conversation,
            sender_type=SenderType.AI_ASSISTANT,
            message="",
        )
        history = async_to_sync(get_conversation_history)(self.conversation)
        self.assertEqual(history, [{"role": "assistant", "content": "A pixel."}])
        _, data = async_to_sync(prepare_regeneration_data)(
            self.ai, None, {"media_ids": []}, self.message, AsyncMock()
        )
        self.assertEqual(data["media_ids"], [])

    def test_text_and_image_survive_followup_together(self):
        self.message.message = "Describe this"
        self.message.save(update_fields=["message"])
        Message.active_objects.create(
            conversation=self.conversation,
            sender_type=SenderType.PLAYER,
            message="Next",
        )
        Message.active_objects.create(
            conversation=self.conversation,
            sender_type=SenderType.AI_ASSISTANT,
            message="",
        )
        history = async_to_sync(get_conversation_history)(self.conversation)
        self.assertEqual(
            history[0]["content"][0], {"type": "text", "text": "Describe this"}
        )
        self.assertEqual(history[0]["content"][1]["image_url"]["url"], IMAGE["preview"])
