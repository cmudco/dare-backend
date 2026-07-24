from typing import Any

from django.db.models import Prefetch

from conversations import models as conversation_models
from conversations.constants import SenderType
from data_exports.services import serialization_helpers


class ConversationExportBuilder:
    """Build flat conversation export rows from active user-owned conversations."""

    def build(self, user: Any) -> list[dict[str, Any]]:
        conversations = self._get_conversations(user)
        return [
            self._serialize_conversation(conversation) for conversation in conversations
        ]

    def _get_conversations(self, user: Any) -> list[conversation_models.Conversation]:
        messages = conversation_models.Message.active_objects.prefetch_related(
            "files"
        ).order_by("created_at")

        queryset = (
            conversation_models.Conversation.active_objects.filter(user=user)
            .select_related("conversation_summary")
            .prefetch_related(Prefetch("messages", queryset=messages))
            .order_by("created_at")
        )
        return list(queryset)

    def _serialize_conversation(
        self, conversation: conversation_models.Conversation
    ) -> dict[str, Any]:
        summary = getattr(conversation, "conversation_summary", None)
        return {
            "conversationId": conversation.conversation_id,
            "name": conversation.title,
            "summary": summary.summary if summary else "",
            "createdAt": serialization_helpers.to_iso(conversation.created_at),
            "updatedAt": serialization_helpers.to_iso(conversation.updated_at),
            "account": {
                "accountId": conversation.user_id,
            },
            "chatMessages": [
                self._serialize_message(message, conversation.user_id)
                for message in conversation.messages.all()
            ],
        }

    def _serialize_message(
        self, message: conversation_models.Message, user_id: int
    ) -> dict[str, Any]:
        return {
            "id": message.id,
            "role": self._message_role(message),
            "sender": message.sender,
            "senderName": message.sender_name,
            "text": message.message,
            "createdAt": serialization_helpers.to_iso(message.created_at),
            "updatedAt": serialization_helpers.to_iso(message.updated_at),
            "files": [
                self._serialize_file(file_obj)
                for file_obj in message.files.all()
                if self._is_owned_file(file_obj, user_id)
            ],
        }

    def _message_role(self, message: conversation_models.Message) -> str:
        if message.sender_type == SenderType.PLAYER:
            return "user"
        if message.sender_type == SenderType.AI_ASSISTANT:
            return "assistant"
        return "unknown"

    def _serialize_file(self, file_obj: Any) -> dict[str, Any]:
        return {
            "id": file_obj.id,
            "name": file_obj.name or getattr(file_obj.file, "name", ""),
            "fileType": file_obj.file_type,
            "size": file_obj.size,
        }

    def _is_owned_file(self, file_obj: Any, user_id: int) -> bool:
        return getattr(file_obj, "user_id", None) == user_id
