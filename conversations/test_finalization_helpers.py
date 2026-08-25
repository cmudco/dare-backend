from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.test import TransactionTestCase

from conversations.models import Conversation
from conversations.services.message_helpers.finalization_helpers import (
    _conversation_was_deleted,
    finalize_message,
)

FORMAT_MESSAGE = (
    "conversations.services.message_helpers.finalization_helpers"
    ".WebSocketResponseService.format_message"
)


class ConversationFinalizationTests(TransactionTestCase):
    def test_existing_conversation_allows_message_finalization(self):
        conversation = Conversation._base_manager.create(conversation_id="FINALIZE")
        message = SimpleNamespace(
            id=42,
            conversation_id=conversation.pk,
            message="",
            original_message=None,
        )
        finalized_message = SimpleNamespace(id=42, cost=0)
        finalize_ai_message = MagicMock(return_value=finalized_message)
        billing_service = SimpleNamespace(
            finalize_ai_message=finalize_ai_message,
        )
        send = AsyncMock()
        send_error = AsyncMock()

        with patch(
            FORMAT_MESSAGE,
            new=AsyncMock(return_value={"type": "message", "id": 42}),
        ):
            async_to_sync(finalize_message)(
                message_obj=message,
                ai_response="Completed answer",
                token_usage={"input_tokens": 3, "output_tokens": 2},
                regenerate=False,
                generated_image_data=None,
                generated_transcription_data=None,
                user=None,
                conversation=conversation,
                billing_service=billing_service,
                send_callback=send,
                send_error_callback=send_error,
                mark_as_regenerated_callback=AsyncMock(),
            )

        finalize_ai_message.assert_called_once_with(
            message,
            "Completed answer",
            {"input_tokens": 3, "output_tokens": 2},
        )
        send.assert_awaited_once_with({"type": "message", "id": 42})
        send_error.assert_not_awaited()

    def test_soft_deleted_conversation_still_exists(self):
        conversation = Conversation._base_manager.create(conversation_id="SOFT-DELETED")
        conversation.soft_delete()

        self.assertTrue(Conversation._base_manager.filter(pk=conversation.pk).exists())
        self.assertFalse(async_to_sync(_conversation_was_deleted)(conversation.pk))

    def test_hard_deleted_conversation_is_gone(self):
        conversation = Conversation._base_manager.create(conversation_id="HARD-DELETED")
        conversation_id = conversation.pk
        conversation.delete()

        self.assertTrue(async_to_sync(_conversation_was_deleted)(conversation_id))
