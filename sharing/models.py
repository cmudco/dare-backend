from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils.translation import gettext_lazy as _

from common.managers import ActiveObjectsManager
from common.models import BaseModel


class SharedItem(BaseModel):
    """
    Tracks user-specific sharing of conversations, workflows, and prompts.

    Uses Django's ContentType framework for polymorphic references so a single
    table handles sharing across all entity types.
    """

    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        help_text=_("Type of the shared entity (Conversation, Workflow, Prompt)"),
    )
    object_id = models.CharField(
        max_length=100,
        help_text=_("Identifier of the shared entity (PK or conversation_id)"),
    )
    content_object = GenericForeignKey("content_type", "object_id")

    shared_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="items_shared_by_me",
        help_text=_("User who shared this item"),
    )
    shared_with = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="items_shared_with_me",
        help_text=_("User who received the share"),
    )
    message = models.TextField(
        blank=True,
        default="",
        help_text=_("Optional message from the sharer"),
    )

    objects = models.Manager()
    active_objects = ActiveObjectsManager()

    class Meta:
        verbose_name = _("Shared Item")
        verbose_name_plural = _("Shared Items")
        unique_together = ("content_type", "object_id", "shared_with")
        indexes = [
            models.Index(fields=["shared_with", "content_type"]),
            models.Index(fields=["shared_by", "content_type"]),
            models.Index(fields=["content_type", "object_id"]),
        ]

    def __str__(self):
        return (
            f"{self.shared_by.email} shared {self.content_type.model} "
            f"#{self.object_id} with {self.shared_with.email}"
        )
