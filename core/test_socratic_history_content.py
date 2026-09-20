"""Socratic prompt construction accepts the shared history builder's content blocks."""

import tempfile
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TransactionTestCase, override_settings

from conversations.constants import SenderType
from conversations.models import Conversation, Message
from core.services.dtos import ContextConfig, LLMQueryRequest
from core.services.file_upload_service import FileUploadService
from core.services.llm_helpers.history_helpers import get_conversation_history
from core.services.llm_helpers.socratic_helpers import (
    _format_transcript,
    build_advanced_socratic_messages,
    build_classic_socratic_messages,
)
from core.test_image_chat import IMAGE

IMAGE_PART = {
    "type": "image_url",
    "image_url": {"url": "data:image/png;base64,test-image"},
}


class SocraticHistoryContentTests(SimpleTestCase):
    def test_text_blocks_without_images_are_supported(self):
        self.assertEqual(
            _format_transcript(
                [{"role": "user", "content": [{"type": "text", "text": "Hello"}]}]
            ),
            "User: Hello",
        )

    def test_mixed_history_preserves_text_and_marks_images_without_dumping_data(self):
        history = [
            {"role": "user", "content": "  Hello  "},
            {"role": "assistant", "content": None},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Look at this diagram"},
                    IMAGE_PART,
                    {"type": "text", "text": "Explain its labels"},
                ],
            },
            {"role": "assistant", "content": "An explanation"},
        ]
        original = deepcopy(history)
        transcript = _format_transcript(history)
        self.assertIn("User: Hello", transcript)
        self.assertIn("Look at this diagram", transcript)
        self.assertIn("Explain its labels", transcript)
        self.assertIn("[Image attachment]", transcript)
        self.assertIn("Assistant: An explanation", transcript)
        self.assertNotIn("base64", transcript)
        self.assertEqual(history, original)

    def test_image_only_history_remains_a_turn(self):
        self.assertEqual(
            _format_transcript([{"role": "user", "content": [IMAGE_PART]}]),
            "User: [Image attachment]",
        )

    def test_empty_and_plain_text_history_keep_existing_behavior(self):
        for history in (
            [],
            [{"role": "user", "content": None}],
            [{"role": "user", "content": []}],
        ):
            self.assertEqual(_format_transcript(history), "No previous messages.")
        self.assertEqual(
            _format_transcript(
                [
                    {"role": "user", "content": "Hello"},
                    {"role": "assistant", "content": "Hi"},
                ]
            ),
            "User: Hello\n\nAssistant: Hi",
        )

    async def test_both_builders_preserve_actual_images_outside_text_transcript(self):
        history = [
            {
                "role": "user",
                "content": [{"type": "text", "text": "Earlier diagram"}, IMAGE_PART],
            }
        ]
        original = deepcopy(history)
        for build in (
            build_classic_socratic_messages,
            build_advanced_socratic_messages,
        ):
            request = LLMQueryRequest(
                message="Explain that image",
                conversation=SimpleNamespace(title="Tutor"),
            )
            with patch(
                "core.services.llm_helpers.socratic_helpers.get_conversation_history",
                AsyncMock(return_value=history),
            ):
                result = await build(request, None)
            images = [
                part
                for message in result.messages
                if isinstance(message["content"], list)
                for part in message["content"]
                if part["type"] == "image_url"
            ]
            self.assertEqual(images, [IMAGE_PART])
            self.assertIn("Explain that image", result.messages[-1]["content"])
            self.assertFalse(
                any(
                    "base64" in message["content"]
                    for message in result.messages
                    if isinstance(message["content"], str)
                )
            )
        self.assertEqual(history, original)


class PersistedSocraticHistoryTests(TransactionTestCase):
    """Exercise saved attachments through real history and both prompt builders."""

    def setUp(self):
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        settings = override_settings(MEDIA_ROOT=media.name)
        settings.enable()
        self.addCleanup(settings.disable)
        self.user = get_user_model().objects.create_user(
            email="socratic-history@example.test", password="test-only"
        )
        self.conversation = Conversation.active_objects.create(
            user=self.user, conversation_id="socratic-history", title="Tutor"
        )
        self.earlier = self.add_message(SenderType.PLAYER, "Describe this diagram")
        self.add_message(SenderType.AI_ASSISTANT, "What do you notice?")
        self.current = self.add_message(SenderType.PLAYER, "Explain it again")
        self.add_message(SenderType.AI_ASSISTANT, "")

    def add_message(self, sender, text):
        return Message.active_objects.create(
            conversation=self.conversation, sender_type=sender, message=text
        )

    def attach_image(self, message):
        attachment = FileUploadService.save_base64_image(
            IMAGE["preview"], IMAGE["name"], IMAGE["type"], self.user
        )
        message.files.add(attachment)
        return attachment

    def assert_builders(self, *, has_image, history_limit=10):
        request = LLMQueryRequest(
            message=self.current.message,
            user=self.user,
            conversation=self.conversation,
            context=ContextConfig(history_limit=history_limit),
        )
        for build in (
            build_classic_socratic_messages,
            build_advanced_socratic_messages,
        ):
            with self.subTest(mode=build.__name__):
                result = async_to_sync(build)(request, None)
                images = [
                    part["image_url"]["url"]
                    for turn in result.messages
                    if isinstance(turn["content"], list)
                    for part in turn["content"]
                    if part["type"] == "image_url"
                ]
                self.assertEqual(images, [IMAGE["preview"]] if has_image else [])
                self.assertIn("Explain it again", result.messages[-1]["content"])

    def test_plain_text_history_builds_without_images(self):
        history = async_to_sync(get_conversation_history)(self.conversation)
        self.assertTrue(all(isinstance(turn["content"], str) for turn in history))
        self.assert_builders(has_image=False)

    def test_text_followup_to_saved_image_builds_in_both_modes(self):
        self.attach_image(self.earlier)
        history = async_to_sync(get_conversation_history)(self.conversation)
        self.assertIsInstance(history[0]["content"], list)
        self.assertFalse(self.current.files.exists())
        self.assert_builders(has_image=True)

    def test_text_followup_to_image_only_message_builds_in_both_modes(self):
        self.earlier.message = ""
        self.earlier.save(update_fields=["message"])
        self.attach_image(self.earlier)
        self.assert_builders(has_image=True)

    def test_deleted_image_does_not_create_multimodal_history(self):
        attachment = self.attach_image(self.earlier)
        attachment.is_deleted = True
        attachment.save(update_fields=["is_deleted"])
        self.assert_builders(has_image=False)

    def test_image_outside_history_window_does_not_replay(self):
        self.attach_image(self.earlier)
        self.assert_builders(has_image=False, history_limit=1)
