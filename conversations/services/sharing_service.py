"""
Conversation Sharing Service

Handles publish, unpublish, and fork operations for conversations.
Centralizes sharing business logic outside of views.
"""
import logging
from typing import Optional

from django.db import transaction
from django.utils import timezone

from conversations.constants import (
    FORK_TITLE_PREFIX,
    DEFAULT_FORK_TITLE,
    SharingErrorCode,
    SharingErrorMessage,
)
from conversations.models import Conversation
from sharing.services.sharing_service import SharingService

logger = logging.getLogger(__name__)


class SharingValidationError(Exception):
    """Raised when a sharing operation fails validation."""
    def __init__(self, message: str, error_code: str):
        super().__init__(message)
        self.error_code = error_code


class ConversationSharingService:
    """Service for conversation publish/unpublish/fork operations."""

    @staticmethod
    def toggle_publish(conversation: Conversation, user) -> Conversation:
        """
        Toggle the published status of a conversation.

        Args:
            conversation: The conversation to publish/unpublish.
            user: The requesting user (must be the owner).

        Returns:
            The updated conversation.

        Raises:
            SharingValidationError: If the user is not the owner or the conversation is forked.
        """
        if conversation.user != user:
            raise SharingValidationError(
                SharingErrorMessage.PERMISSION_DENIED,
                SharingErrorCode.PERMISSION_DENIED,
            )

        if conversation.file_owner_id is not None:
            raise SharingValidationError(
                SharingErrorMessage.CANNOT_PUBLISH_FORKED,
                SharingErrorCode.CANNOT_PUBLISH_FORKED,
            )

        conversation.is_published = not conversation.is_published
        conversation.published_at = timezone.now() if conversation.is_published else None
        conversation.save(update_fields=['is_published', 'published_at', 'updated_at'])

        return conversation

    @staticmethod
    def fork(conversation_id: str, user) -> Conversation:
        """
        Fork a published conversation for the given user.

        Args:
            conversation_id: The ID of the conversation to fork.
            user: The user who will own the forked copy.

        Returns:
            The newly created forked conversation.

        Raises:
            SharingValidationError: If the conversation is not found or not published.
        """
        conversation = Conversation.active_objects.filter(
            conversation_id=conversation_id,
            is_published=True,
        ).first()

        # Also allow forking if directly shared with the user
        if not conversation:
            candidate = Conversation.active_objects.filter(
                conversation_id=conversation_id,
            ).first()
            if candidate and SharingService.can_access(
                user,
                "conversation",
                candidate.conversation_id,
            ):
                conversation = candidate

        if not conversation:
            raise SharingValidationError(
                SharingErrorMessage.CONVERSATION_NOT_PUBLISHED,
                SharingErrorCode.NOT_FOUND,
            )

        with transaction.atomic():
            is_cross_user = conversation.user != user
            file_owner_id = conversation.user.id if is_cross_user else None
            fork_title = f"{FORK_TITLE_PREFIX}{conversation.title or DEFAULT_FORK_TITLE}"

            forked = conversation.clone(
                user=user,
                custom_title=fork_title,
                file_owner_id=file_owner_id,
            )

        return forked

    @staticmethod
    def can_view_messages(conversation: Conversation, user) -> bool:
        """
        Check if a user can view messages for a conversation.

        Returns True if the user is the owner, the conversation is published,
        or the conversation has been directly shared with the user.
        """
        is_owner = (
            hasattr(user, 'is_authenticated')
            and user.is_authenticated
            and conversation.user == user
        )
        if is_owner or conversation.is_published:
            return True

        return SharingService.can_access(user, "conversation", conversation.conversation_id)
