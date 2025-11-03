"""Custom managers for the conversations app."""

from __future__ import annotations

from django.db import models
from django.db.models import Q


class MessageWithFeedbackManager(models.Manager):
    """Manager returning only messages that have associated feedback."""

    def get_queryset(self):  # type: ignore[override]
        return (
            super()
            .get_queryset()
            .filter(
              Q(feedback_type__isnull=False) |
              Q(feedback_text__isnull=False, feedback_text__gt='')
            )
            .select_related("conversation", "conversation__user", "llm")
        )
