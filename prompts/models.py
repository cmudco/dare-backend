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