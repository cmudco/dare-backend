from django.db import models
from django.conf import settings

from common.managers import ActiveObjectsManager
from common.models import BaseModel, TimeStampMixin
from users.constants import VectorDBChoice
from .constants import FileStatus
from django.utils.translation import gettext_lazy as _


class Tag(TimeStampMixin):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tags",
        blank=True,
        null=True
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
        related_name='folders',
        help_text="The user who owns this folder"
    )
    name = models.CharField(
        max_length=255,
        help_text="Name of the folder"
    )
    files = models.ManyToManyField(
        'File',
        related_name='folders',
        blank=True,
        help_text="Files contained in this folder"
    )

    objects = models.Manager()

    class Meta:
        unique_together = ('user', 'name')

    def __str__(self):
        return self.name


class File(BaseModel):
    """
    Model for user-uploaded files, tracking metadata, tags and file type.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='files',
        help_text="The user who owns this file"
    )
    file = models.FileField(
        upload_to='files/',
        help_text="The actual file content",
        max_length=255
    )
    name = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="Custom name for the file (defaults to filename if not provided)"
    )
    file_type = models.CharField(
        max_length=150,
        blank=True,
        null=True,
        help_text="MIME type of the file (e.g., application/pdf, image/jpeg)"
    )
    size = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="File size in bytes"
    )
    tags = models.ManyToManyField(
        Tag,
        related_name='files',
        blank=True,
        help_text="Custom tags for categorizing and filtering files"
    )
    job_id = models.CharField(
        max_length=36,
        blank=True,
        null=True,
        help_text="Redis Queue Job ID for tracking background processing"
    )
    status = models.IntegerField(
        choices=FileStatus.choices,
        default=FileStatus.PROCESSING,
        help_text="Processing status of the file"
    )
    vector_db_source = models.IntegerField(
        choices=VectorDBChoice.choices,
        null=True,
        blank=True,
        verbose_name=_("Vector DB Source"),
        help_text=_("Vector database where this file's chunks are stored")
    )
    error_message = models.TextField(
        blank=True,
        null=True,
        help_text="Error message if file processing failed"
    )
    is_media = models.BooleanField(
        default=False,
        help_text="Flag indicating if this file is a media file (image/video) that should not be vectorized"
    )
    media_type = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        choices=[
            ('image', 'Image'),
            ('video', 'Video'),
            ('document', 'Document'),
            ('generated_image', 'Generated Image')
        ],
        help_text="Type of media file: image, video, document, or generated_image"
    )

    # AI Image Generation Fields
    is_generated = models.BooleanField(
        default=False,
        help_text="Flag indicating if this file was AI-generated (e.g., via DALL-E)"
    )
    generation_prompt = models.TextField(
        blank=True,
        null=True,
        help_text="Original prompt used to generate this image (for AI-generated images)"
    )
    revised_prompt = models.TextField(
        blank=True,
        null=True,
        help_text="Revised/enhanced prompt returned by DALL-E during generation"
    )
    generation_params = models.JSONField(
        blank=True,
        null=True,
        help_text="Generation parameters: model, size, quality, style, etc."
    )
    generation_cost = models.DecimalField(
        max_digits=10,
        decimal_places=6,
        null=True,
        blank=True,
        help_text="Cost in USD for generating this image"
    )

    active_objects = ActiveObjectsManager()

    def __str__(self):
        return self.name if self.name else self.file.name
