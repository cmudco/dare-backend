"""
Storage service factory for selecting appropriate storage backend.

Follows the same pattern as core/services/vector_service.py
"""
import logging
from typing import Optional

from django.conf import settings
from django.core.files.storage import FileSystemStorage, Storage

from .constants import StorageBackendChoice
from .backends import SyftBoxStorage

logger = logging.getLogger(__name__)


def get_storage_service(
    user_id: Optional[int] = None,
    user_email: Optional[str] = None
) -> Storage:
    """
    Factory function to get appropriate storage backend based on user preference.

    Args:
        user_id: User ID to look up preferences
        user_email: User email (required for SyftBox storage)

    Returns:
        Django Storage instance (FileSystemStorage or SyftBoxStorage)
    """
    # If SyftBox is globally disabled, always use local storage
    if not settings.SYFTBOX.get('ENABLED', False):
        return FileSystemStorage(
            location=settings.MEDIA_ROOT,
            base_url=settings.MEDIA_URL
        )

    # If no user specified, use local storage
    if user_id is None:
        return FileSystemStorage(
            location=settings.MEDIA_ROOT,
            base_url=settings.MEDIA_URL
        )

    try:
        from django.contrib.auth import get_user_model
        User = get_user_model()
        user = User.objects.get(id=user_id)

        # Check user's storage preference
        if user.storage_backend == StorageBackendChoice.SYFTBOX:
            email = user_email or user.email
            return SyftBoxStorage(user_email=email)

        # Default to local storage
        return FileSystemStorage(
            location=settings.MEDIA_ROOT,
            base_url=settings.MEDIA_URL
        )

    except Exception as e:
        logger.warning(f"Error getting storage service for user {user_id}: {e}, falling back to local")
        return FileSystemStorage(
            location=settings.MEDIA_ROOT,
            base_url=settings.MEDIA_URL
        )


def get_file_storage(file_instance) -> Storage:
    """
    Get storage backend for an existing File instance.

    Uses the file's recorded storage_backend field to determine
    which storage to use for reading/accessing the file.

    Args:
        file_instance: File model instance

    Returns:
        Django Storage instance
    """
    if file_instance.storage_backend == StorageBackendChoice.SYFTBOX:
        return SyftBoxStorage(user_email=file_instance.user.email)

    return FileSystemStorage(
        location=settings.MEDIA_ROOT,
        base_url=settings.MEDIA_URL
    )


def get_storage_for_user(user) -> Storage:
    """
    Get storage backend for a user based on their preference.

    Convenience function that takes a User instance directly.

    Args:
        user: User model instance

    Returns:
        Django Storage instance
    """
    return get_storage_service(user_id=user.id, user_email=user.email)
