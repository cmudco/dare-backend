"""
Sharing Service

Handles sharing items (conversations, workflows, prompts) with specific users
by email or with all users in the same access code group.
Centralizes all sharing business logic.
"""
import logging
from typing import List, Optional

from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, transaction
from django.db.models import Q, QuerySet

from notifications.constants import (
    NotificationAction,
    NotificationCategory,
    NotificationDeliveryType,
)
from notifications.models import Notification
from sharing.constants import (
    SHAREABLE_MODELS,
    SharingErrorCode,
    SharingErrorMessage,
)
from sharing.models import SharedItem
from sharing.services.dtos import ShareFailure, ShareResult, ShareSuccess
from users.models import User

logger = logging.getLogger(__name__)


class SharingValidationError(Exception):
    """Raised when a sharing operation fails validation."""

    def __init__(self, message: str, error_code: str):
        super().__init__(message)
        self.error_code = error_code


class SharingService:
    """Service for user-specific sharing operations."""

    @staticmethod
    def _get_model_manager(model_class):
        """Return the preferred manager for shareable models."""
        manager = getattr(model_class, "active_objects", None)
        if manager is not None:
            return manager

        manager = getattr(model_class, "objects", None)
        if manager is not None:
            return manager

        raise SharingValidationError(
            SharingErrorMessage.NOT_FOUND,
            SharingErrorCode.NOT_FOUND,
        )

    @staticmethod
    def _get_content_type(entity_type: str) -> ContentType:
        """
        Resolve a string entity type label to a Django ContentType.

        Args:
            entity_type: One of 'conversation', 'workflow', 'prompt'.

        Returns:
            The corresponding ContentType object.

        Raises:
            SharingValidationError: If the entity type is not recognized.
        """
        mapping = SHAREABLE_MODELS.get(entity_type)
        if not mapping:
            raise SharingValidationError(
                SharingErrorMessage.INVALID_ENTITY_TYPE,
                SharingErrorCode.INVALID_ENTITY_TYPE,
            )
        app_label, model_name = mapping
        return ContentType.objects.get(app_label=app_label, model=model_name)

    @staticmethod
    def _get_entity(content_type: ContentType, object_id: str):
        """
        Retrieve the actual entity object. Uses active_objects if available,
        otherwise falls back to objects manager.

        For Conversation entities, looks up by the `conversation_id` field.
        For all other entities, looks up by integer PK.

        Returns:
            The entity instance.

        Raises:
            SharingValidationError: If the entity does not exist.
        """
        model_class = content_type.model_class()
        manager = SharingService._get_model_manager(model_class)
        try:
            if content_type.model == "conversation":
                return manager.get(conversation_id=object_id)
            return manager.get(pk=int(object_id))
        except (model_class.DoesNotExist, ValueError):
            raise SharingValidationError(
                SharingErrorMessage.NOT_FOUND,
                SharingErrorCode.NOT_FOUND,
            )

    @staticmethod
    def _get_entity_title(entity) -> str:
        """Extract a human-readable title from a shareable entity."""
        return getattr(entity, "title", None) or str(entity)

    @staticmethod
    def _is_forwarded_copy(entity_type: str, entity, owner: User) -> bool:
        """Return True when an item originated from another user."""
        if entity_type == "conversation":
            return entity.file_owner_id is not None

        if entity_type == "workflow":
            return entity.parent is not None and entity.parent.user_id != owner.id

        if entity_type == "prompt":
            return entity.forked_from_user_id is not None

        return False

    @staticmethod
    def share_item(
        entity_type: str,
        object_id: str,
        emails: List[str],
        shared_by: User,
        message: str = "",
    ) -> ShareResult:
        """
        Share an item with multiple users by email.

        Args:
            entity_type: One of 'conversation', 'workflow', 'prompt'.
            object_id: Primary key of the entity.
            emails: List of recipient email addresses.
            shared_by: The user performing the share.
            message: Optional message to include.

        Returns:
            ShareResult with lists of successes and failures.

        Raises:
            SharingValidationError: If the entity type is invalid or entity not found.
        """
        content_type = SharingService._get_content_type(entity_type)
        entity = SharingService._get_entity(content_type, object_id)

        # Validate ownership
        entity_owner = getattr(entity, "user", None)
        if entity_owner is None or entity_owner != shared_by:
            raise SharingValidationError(
                SharingErrorMessage.PERMISSION_DENIED,
                SharingErrorCode.PERMISSION_DENIED,
            )

        if SharingService._is_forwarded_copy(entity_type, entity, shared_by):
            raise SharingValidationError(
                SharingErrorMessage.FORWARDED_SHARE_NOT_ALLOWED,
                SharingErrorCode.FORWARDED_SHARE_NOT_ALLOWED,
            )

        entity_title = SharingService._get_entity_title(entity)
        successes: List[ShareSuccess] = []
        failures: List[ShareFailure] = []

        for email in emails:
            email = email.strip().lower()

            # Reject self-sharing
            if email == shared_by.email.lower():
                failures.append(ShareFailure(
                    email=email,
                    reason=SharingErrorMessage.SELF_SHARE,
                ))
                continue

            # Look up recipient
            try:
                recipient = User.objects.get(email__iexact=email, is_active=True)
            except User.DoesNotExist:
                failures.append(ShareFailure(
                    email=email,
                    reason=SharingErrorMessage.USER_NOT_FOUND,
                ))
                continue

            # Create or update the share
            try:
                with transaction.atomic():
                    shared_item, _ = SharedItem.objects.update_or_create(
                        content_type=content_type,
                        object_id=object_id,
                        shared_with=recipient,
                        defaults={
                            "shared_by": shared_by,
                            "message": message,
                            "is_active": True,
                            "is_deleted": False,
                        },
                    )
                successes.append(ShareSuccess(id=shared_item.id, email=email))

                # Create in-app notification for the recipient
                SharingService._create_notification(
                    recipient=recipient,
                    shared_by=shared_by,
                    entity_type=entity_type,
                    entity_title=entity_title,
                    message=message,
                )
            except IntegrityError:
                logger.exception(
                    "Failed to create share for %s on %s #%s",
                    email, entity_type, object_id,
                )
                failures.append(ShareFailure(
                    email=email,
                    reason="An unexpected error occurred.",
                ))

        return ShareResult(shared=successes, failed=failures)

    @staticmethod
    def share_with_access_code_group(
        entity_type: str,
        object_id: str,
        shared_by: User,
        message: str = "",
    ) -> SharedItem:
        """
        Share an item with all users belonging to the same access code group
        as the sharing user.

        Args:
            entity_type: One of 'conversation', 'workflow', 'prompt'.
            object_id: Primary key of the entity.
            shared_by: The user performing the share.
            message: Optional message to include.

        Returns:
            The created or updated SharedItem (group share record).

        Raises:
            SharingValidationError: If the user has no access code group, the
                entity type is invalid, the entity is not found, or the user
                is not the owner.
        """
        if not shared_by.access_code_group_id:
            raise SharingValidationError(
                "You are not part of an access code group.",
                SharingErrorCode.PERMISSION_DENIED,
            )

        content_type = SharingService._get_content_type(entity_type)
        entity = SharingService._get_entity(content_type, object_id)

        entity_owner = getattr(entity, "user", None)
        if entity_owner is None or entity_owner != shared_by:
            raise SharingValidationError(
                SharingErrorMessage.PERMISSION_DENIED,
                SharingErrorCode.PERMISSION_DENIED,
            )

        if SharingService._is_forwarded_copy(entity_type, entity, shared_by):
            raise SharingValidationError(
                SharingErrorMessage.FORWARDED_SHARE_NOT_ALLOWED,
                SharingErrorCode.FORWARDED_SHARE_NOT_ALLOWED,
            )

        with transaction.atomic():
            shared_item, _ = SharedItem.objects.update_or_create(
                content_type=content_type,
                object_id=object_id,
                shared_with_group=shared_by.access_code_group,
                defaults={
                    "shared_by": shared_by,
                    "shared_with": None,
                    "message": message,
                    "is_active": True,
                    "is_deleted": False,
                },
            )

        return shared_item

    @staticmethod
    def revoke_share(share_id: int, user: User) -> None:
        """
        Revoke a specific share. Only the user who shared it can revoke.

        Args:
            share_id: Primary key of the SharedItem.
            user: The requesting user.

        Raises:
            SharingValidationError: If the share is not found or user lacks permission.
        """
        try:
            shared_item = SharedItem.active_objects.get(pk=share_id)
        except SharedItem.DoesNotExist:
            raise SharingValidationError(
                SharingErrorMessage.NOT_FOUND,
                SharingErrorCode.NOT_FOUND,
            )

        if shared_item.shared_by != user:
            raise SharingValidationError(
                SharingErrorMessage.PERMISSION_DENIED,
                SharingErrorCode.PERMISSION_DENIED,
            )

        shared_item.soft_delete()

    @staticmethod
    def get_shared_with_me(
        user: User,
        entity_type: Optional[str] = None,
    ) -> QuerySet:
        """
        Get all items shared with a user, optionally filtered by entity type.

        Includes both direct (individual) shares and access code group shares
        where the user belongs to the target group.

        Args:
            user: The recipient user.
            entity_type: Optional filter ('conversation', 'workflow', 'prompt').

        Returns:
            QuerySet of SharedItem instances.
        """
        q = Q(shared_with=user)
        if user.access_code_group_id:
            q |= Q(shared_with_group=user.access_code_group)

        qs = (
            SharedItem.active_objects.filter(q)
            .exclude(shared_by=user)
            .select_related("content_type", "shared_by", "shared_with_group")
        )

        if entity_type:
            content_type = SharingService._get_content_type(entity_type)
            qs = qs.filter(content_type=content_type)

        return qs.order_by("-created_at")

    @staticmethod
    def get_shared_by_me(
        user: User,
        entity_type: Optional[str] = None,
    ) -> QuerySet:
        """
        Get all items shared by a user, optionally filtered by entity type.

        Args:
            user: The sharer.
            entity_type: Optional filter.

        Returns:
            QuerySet of SharedItem instances.
        """
        qs = SharedItem.active_objects.filter(
            shared_by=user,
        ).select_related("content_type", "shared_with", "shared_with_group")

        if entity_type:
            content_type = SharingService._get_content_type(entity_type)
            qs = qs.filter(content_type=content_type)

        return qs.order_by("-created_at")

    @staticmethod
    def get_recipients(
        entity_type: str,
        object_id: str,
        user: User,
    ) -> QuerySet:
        """
        Get all users an item is shared with. Only the owner can call this.

        Args:
            entity_type: The entity type string.
            object_id: Primary key of the entity.
            user: The requesting user (must be the owner).

        Returns:
            QuerySet of SharedItem instances for the given entity.

        Raises:
            SharingValidationError: If entity not found or user is not the owner.
        """
        content_type = SharingService._get_content_type(entity_type)
        entity = SharingService._get_entity(content_type, object_id)

        entity_owner = getattr(entity, "user", None)
        if entity_owner is None or entity_owner != user:
            raise SharingValidationError(
                SharingErrorMessage.PERMISSION_DENIED,
                SharingErrorCode.PERMISSION_DENIED,
            )

        return SharedItem.active_objects.filter(
            content_type=content_type,
            object_id=object_id,
        ).select_related("shared_with", "shared_with_group").order_by("-created_at")

    @staticmethod
    def can_access(user: User, entity_type: str, object_id) -> bool:
        """
        Check if a user has access to an item via direct sharing.

        Args:
            user: The user to check.
            entity_type: The entity type string.
            object_id: Primary key of the entity.

        Returns:
            True if the item is shared with the user.
        """
        try:
            content_type = SharingService._get_content_type(entity_type)
        except SharingValidationError:
            return False

        q = Q(shared_with=user)
        if user.access_code_group_id:
            q |= Q(shared_with_group=user.access_code_group)

        return SharedItem.active_objects.filter(
            q,
            content_type=content_type,
            object_id=str(object_id),
        ).exists()

    @staticmethod
    def _create_notification(
        recipient: User,
        shared_by: User,
        entity_type: str,
        entity_title: str,
        message: str = "",
    ) -> None:
        """Create an in-app notification for the share recipient."""
        entity_label = entity_type.replace("_", " ").title()
        notification_message = (
            message
            if message
            else f'"{entity_title}" has been shared with you.'
        )

        try:
            Notification.objects.create(
                user=recipient,
                title=f"{shared_by.email} shared a {entity_label} with you",
                message=notification_message,
                category=NotificationCategory.INFO,
                delivery_type=NotificationDeliveryType.PANEL,
                action_type=NotificationAction.NAVIGATE,
            )
        except Exception:
            logger.exception(
                "Failed to create notification for share with %s",
                recipient.email,
            )
