from django.contrib.auth.base_user import AbstractBaseUser

from conversations.models import Conversation


class ConversationPreferenceError(Exception):
    """Raised when a conversation preference change is not allowed."""


class ConversationPreferenceService:
    """Handles owner-only conversation preference updates."""

    @staticmethod
    def toggle_favorite(
        conversation: Conversation,
        user: AbstractBaseUser,
    ) -> Conversation:
        """Toggle the favorite flag for a conversation owned by the user."""
        if conversation.user != user:
            raise ConversationPreferenceError(
                "You can only favorite your own conversations."
            )

        conversation.is_favorite = not conversation.is_favorite
        conversation.save(update_fields=["is_favorite", "updated_at"])
        return conversation
