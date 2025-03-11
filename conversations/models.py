import random
import string

from django.db import models
from django.conf import settings

from common.managers import ActiveObjectsManager
from common.models import BaseModel, TimeStampMixin
from .constants import Provider, SenderType


class LLM(models.Model):
    name = models.CharField(max_length=255, help_text="Display name of the Language Model.")
    identifier = models.CharField(max_length=255, unique=True, help_text="Technical identifier used in API calls (e.g., claude-3.5-sonnet-20240307).")
    description = models.TextField(blank=True, null=True, help_text="Description of the language model capabilities.")
    provider = models.CharField(
        max_length=20,
        choices=Provider.choices(),
        default="openai",
        help_text="Provider of the LLM (e.g., OpenAI, Claude)."
    )

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = "LLMs"


class Conversation(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="conversations",
        help_text="User who owns this conversation."
    )
    conversation_id = models.CharField(max_length=10, unique=True, help_text="Unique conversation ID.")
    title = models.CharField(max_length=255, blank=True, null=True, help_text="Title of the conversation.")

    active_objects = ActiveObjectsManager()


    def save(self, *args, **kwargs):
        if not self.conversation_id:
            self.conversation_id = "".join(
                random.choices(string.ascii_uppercase + string.digits, k=5)
            )
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Conversation {self.conversation_id}"

class Message(BaseModel):
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="messages",
        help_text="Corresponding conversation."
    )
    sender_type = models.IntegerField(
        choices=SenderType.choices,
        help_text="Type of sender (User or AI)."
    )
    sender = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="Name or identifier of the sender."
    )
    message = models.TextField(help_text="Content of the message.")

    files = models.ManyToManyField(
        'files.File',
        blank=True,
        related_name='chat_messages',
        help_text="Files referenced in this message"
    )

    active_objects = ActiveObjectsManager()

    @property
    def sender_name(self):
        """
        Returns the display name of the sender.
        If sender is provided, use that.
        Otherwise fall back to predefined labels based on sender_type.
        """
        if self.sender:
            return self.sender
        elif self.sender_type == SenderType.AI_ASSISTANT:
            return SenderType.AI_ASSISTANT.label
        else:
            return self.conversation.user.email

    @property
    def short_message(self):
        return self.message[:30] + "..." if len(self.message) > 30 else self.message

    def __str__(self):
        return f"{self.sender_name} ({self.short_message})"
