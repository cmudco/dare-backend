from django.db import models
from django.conf import settings

from common.managers import ActiveObjectsManager
from common.models import BaseModel

class Prompt(BaseModel):
    """
    Model for user prompts that can be saved and reused.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="prompts",
        help_text="User who owns this prompt."
    )
    title = models.CharField(
        max_length=255,
        help_text="Title of the prompt."
    )
    content = models.TextField(
        help_text="The prompt content."
    )
    version = models.PositiveIntegerField(
        default=1,
        help_text="Version number of the prompt. Increments when cloned."
    )
    parent = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='children',
        help_text="Original prompt this was cloned from."
    )

    active_objects = ActiveObjectsManager()

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.title} ({self.user.email})"


class PublishedPrompt(BaseModel):
    """
    A prompt published to the public library.
    
    When a user publishes a prompt, a PublishedPrompt record is created.
    When they unpublish, this record is deleted. This allows tracking
    publication metadata without modifying the original Prompt model.
    """
    prompt = models.OneToOneField(
        'Prompt',
        on_delete=models.CASCADE,
        related_name='published',
        help_text="The original prompt being published."
    )
    description = models.TextField(
        blank=True,
        help_text="Optional description for the library."
    )
    published_at = models.DateTimeField(auto_now_add=True)

    objects = models.Manager()
    active_objects = ActiveObjectsManager()

    class Meta:
        ordering = ['-published_at']
        verbose_name = "Published Prompt"
        verbose_name_plural = "Published Prompts"

    def __str__(self):
        return f"Published: {self.prompt.title}"