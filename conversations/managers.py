"""Custom managers for the conversations app."""

from __future__ import annotations

from django.db import models


class MessageWithFeedbackManager(models.Manager):
    """Manager returning only messages that have associated feedback."""

    def get_queryset(self):  # type: ignore[override]
        return (
            super()
            .get_queryset()
            .filter(feedback_type__isnull=False)
            .select_related("conversation", "conversation__user", "llm")
        )
