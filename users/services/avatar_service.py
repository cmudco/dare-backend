"""
Avatar service for handling avatar upload and management.
"""
import os
import uuid

from django.conf import settings
from django.core.files.storage import default_storage

from users.constants import (
    AVATAR_ALLOWED_TYPES,
    AVATAR_MAX_SIZE_BYTES,
    AvatarTypeChoice,
)


class AvatarValidationError(Exception):
    """Custom exception for avatar validation errors."""
    pass


class AvatarService:
    """Service for managing user avatars."""

    @staticmethod
    def validate_file(avatar_file) -> None:
        """
        Validate avatar file type and size.
        
        Raises:
            AvatarValidationError: If validation fails.
        """
        if not avatar_file:
            raise AvatarValidationError("No avatar file provided")

        if avatar_file.content_type not in AVATAR_ALLOWED_TYPES:
            raise AvatarValidationError("Invalid file type. Allowed: JPEG, PNG, GIF, WebP")

        if avatar_file.size > AVATAR_MAX_SIZE_BYTES:
            raise AvatarValidationError("File too large. Maximum size is 5MB")

    @staticmethod
    def generate_filename(user_id: int, original_filename: str) -> str:
        """Generate unique filename for avatar storage."""
        ext = os.path.splitext(original_filename)[1].lower() or ".jpg"
        return f"avatars/{user_id}/{uuid.uuid4().hex}{ext}"

    @staticmethod
    def delete_old_avatar(avatar_url: str) -> None:
        """Delete existing avatar file from storage if it exists."""
        if not avatar_url:
            return
        
        old_path = avatar_url.replace(settings.MEDIA_URL, "")
        if default_storage.exists(old_path):
            default_storage.delete(old_path)

    @classmethod
    def upload_avatar(cls, user, avatar_file, request) -> str:
        """
        Upload new avatar for user.
        
        Args:
            user: User instance
            avatar_file: Uploaded file
            request: HTTP request (for building absolute URL)
            
        Returns:
            Absolute URL of the uploaded avatar
        """
        cls.validate_file(avatar_file)
        
        # Delete old avatar
        cls.delete_old_avatar(user.avatar_url)
        
        # Save new avatar
        filename = cls.generate_filename(user.id, avatar_file.name)
        saved_path = default_storage.save(filename, avatar_file)
        relative_url = f"{settings.MEDIA_URL}{saved_path}"
        
        # Update user
        user.avatar_type = AvatarTypeChoice.CUSTOM
        user.avatar_url = relative_url
        user.avatar_preset = None
        user.save(update_fields=["avatar_type", "avatar_url", "avatar_preset"])
        
        return request.build_absolute_uri(relative_url)

    @classmethod
    def remove_avatar(cls, user) -> None:
        """
        Remove user's custom avatar and reset to initials.
        
        Args:
            user: User instance
        """
        if user.avatar_type == AvatarTypeChoice.CUSTOM:
            cls.delete_old_avatar(user.avatar_url)
        
        user.avatar_type = AvatarTypeChoice.INITIALS
        user.avatar_url = None
        user.avatar_preset = None
        user.save(update_fields=["avatar_type", "avatar_url", "avatar_preset"])
