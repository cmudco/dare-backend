"""Proxy models for the conversations app."""

from __future__ import annotations

from .managers import MessageWithFeedbackManager
from .models import Message


class MessageWithFeedback(Message):
    """Proxy model exposing only messages that contain user feedback."""

    objects = MessageWithFeedbackManager()

    class Meta:
        proxy = True
        verbose_name = "Message with Feedback"
        verbose_name_plural = "Messages with Feedback"
