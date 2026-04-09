from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from common.managers import ActiveObjectsManager
from common.models import BaseModel


class SharedItem(BaseModel):
    """
    Tracks user-specific sharing of conversations, workflows, and prompts.

    Uses Django's ContentType framework for polymorphic references so a single
    table handles sharing across all entity types.

    Supports two sharing modes:
    - Individual: shared_with is set, shared_with_group is null.
    - Access code group: shared_with_group is set, shared_with is null.
      Everyone who registered with that access code can access the item.
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
        null=True,
        blank=True,
        related_name="items_shared_with_me",
        help_text=_("Specific user who received the share (null for group shares)"),
    )
    shared_with_group = models.ForeignKey(
        "users.AccessCodeGroup",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="shared_items",
        help_text=_("Access code group this item is shared with (null for individual shares)"),
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
        constraints = [
            models.UniqueConstraint(
                fields=["content_type", "object_id", "shared_with"],
                condition=Q(shared_with__isnull=False),
                name="unique_individual_share",
            ),
            models.UniqueConstraint(
                fields=["content_type", "object_id", "shared_with_group"],
                condition=Q(shared_with_group__isnull=False),
                name="unique_group_share",
            ),
        ]
        indexes = [
            models.Index(fields=["shared_with", "content_type"]),
            models.Index(fields=["shared_with_group", "content_type"]),
            models.Index(fields=["shared_by", "content_type"]),
            models.Index(fields=["content_type", "object_id"]),
        ]

    def __str__(self):
        if self.shared_with_group_id:
            return (
                f"{self.shared_by.email} shared {self.content_type.model} "
                f"#{self.object_id} with group {self.shared_with_group}"
            )
        return (
            f"{self.shared_by.email} shared {self.content_type.model} "
            f"#{self.object_id} with {self.shared_with.email}"
        )
