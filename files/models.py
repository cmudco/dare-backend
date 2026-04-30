import logging

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from common.managers import ActiveObjectsManager
from common.models import BaseModel, TimeStampMixin
from core.storage.constants import StorageBackendChoice
from core.storage.fields import DynamicStorageFileField
from users.constants import VectorDBChoice

from .constants import FileStatus

logger = logging.getLogger(__name__)


class Tag(TimeStampMixin):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tags",
        blank=True,
        null=True,
    )
    label = models.CharField(max_length=255, unique=True)

    def __str__(self):
        return self.label


class Folder(TimeStampMixin):
    """
    Model for organizing files into folders.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="folders",
        help_text="The user who owns this folder",
    )
    name = models.CharField(max_length=255, help_text="Name of the folder")
    files = models.ManyToManyField(
        "File",
        related_name="folders",
        blank=True,
        help_text="Files contained in this folder",
    )

    objects = models.Manager()

    class Meta:
        unique_together = ("user", "name")

    def __str__(self):
        return self.name


class File(BaseModel):
    """
    Model for user-uploaded files, tracking metadata, tags and file type.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="files",
        help_text="The user who owns this file",
    )
    file = DynamicStorageFileField(
        upload_to="files/", help_text="The actual file content", max_length=255
    )
    name = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="Custom name for the file (defaults to filename if not provided)",
    )
    file_type = models.CharField(
        max_length=150,
        blank=True,
        null=True,
        help_text="MIME type of the file (e.g., application/pdf, image/jpeg)",
    )
    size = models.PositiveIntegerField(
        null=True, blank=True, help_text="File size in bytes"
    )
    tags = models.ManyToManyField(
        Tag,
        related_name="files",
        blank=True,
        help_text="Custom tags for categorizing and filtering files",
    )
    job_id = models.CharField(
        max_length=36,
        blank=True,
        null=True,
        help_text="Redis Queue Job ID for tracking background processing",
    )
    status = models.IntegerField(
        choices=FileStatus.choices,
        default=FileStatus.PROCESSING,
        help_text="Processing status of the file",
    )
    vector_db_source = models.IntegerField(
        choices=VectorDBChoice.choices,
        null=True,
        blank=True,
        verbose_name=_("Vector DB Source"),
        help_text=_("Vector database where this file's chunks are stored"),
    )
    error_message = models.TextField(
        blank=True, null=True, help_text="Error message if file processing failed"
    )
    is_media = models.BooleanField(
        default=False,
        help_text="Flag indicating if this file is a media file (image/video/audio) that should not be vectorized",
    )
    media_type = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        choices=[
            ("image", "Image"),
            ("video", "Video"),
            ("audio", "Audio"),
            ("document", "Document"),
            ("generated_image", "Generated Image"),
        ],
        help_text="Type of media file: image, video, audio, document, or generated_image",
    )

    # AI Image Generation Fields
    is_generated = models.BooleanField(
        default=False,
        help_text="Flag indicating if this file was AI-generated (e.g., via DALL-E)",
    )
    generation_prompt = models.TextField(
        blank=True,
        null=True,
        help_text="Original prompt used to generate this image (for AI-generated images)",
    )
    revised_prompt = models.TextField(
        blank=True,
        null=True,
        help_text="Revised/enhanced prompt returned by DALL-E during generation",
    )
    generation_params = models.JSONField(
        blank=True,
        null=True,
        help_text="Generation parameters: model, size, quality, style, etc.",
    )
    generation_cost = models.DecimalField(
        max_digits=10,
        decimal_places=6,
        null=True,
        blank=True,
        help_text="Cost in USD for generating this image",
    )

    # SyftBox Storage Fields
    storage_backend = models.IntegerField(
        choices=StorageBackendChoice.choices,
        default=StorageBackendChoice.LOCAL,
        verbose_name=_("Storage Backend"),
        help_text=_("Storage backend for this file (local or SyftBox)"),
    )
    syftbox_etag = models.CharField(
        max_length=128,
        blank=True,
        null=True,
        verbose_name=_("SyftBox ETag"),
        help_text=_("Last known SyftBox ETag used to detect remote content changes"),
    )

    # Lineage tracking
    source_file = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="copies",
        verbose_name=_("Source File"),
        help_text=_("Original file this was copied/imported from (lineage tracking)"),
    )

    active_objects = ActiveObjectsManager()

    class Meta:
        indexes = [
            models.Index(
                fields=["user", "is_deleted", "is_active"], name="file_user_status_idx"
            ),
        ]

    def delete(self, *args, **kwargs):
        # Delete the actual file from storage (local or SyftBox)
        if self.file:
            try:
                self.file.delete(save=False)
            except Exception as e:
                logger.warning(f"Failed to delete file from storage: {e}")
        super().delete(*args, **kwargs)

    def __str__(self):
        return self.name if self.name else self.file.name


class FileShare(TimeStampMixin):
    """
    Tracks sharing of SyftBox files between platform users.

    This DB record enables discovery ("shared with me" queries).
    Actual access enforcement is handled by SyftBox (syft-perm).

    shared_with=None means the file is shared with all registered platform users.
    Only files with storage_backend=SYFTBOX can be shared.
    """

    file = models.ForeignKey(
        File,
        on_delete=models.CASCADE,
        related_name="shares",
        help_text="The SyftBox file being shared",
    )
    shared_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="shared_files",
        help_text="User who shared the file",
    )
    shared_with = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="received_shares",
        help_text="User who received access. Null means shared with everyone (all platform users).",
    )

    objects = models.Manager()

    class Meta:
        unique_together = ("file", "shared_with")

    def __str__(self):
        target = self.shared_with.email if self.shared_with else "everyone"
        return f"{self.file.name} → {target}"
