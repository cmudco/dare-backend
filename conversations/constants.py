from enum import Enum
from django.db import models

APP_NAME = "conversations"

class SenderType(models.IntegerChoices):
    PLAYER = 1, "Player"
    AI_ASSISTANT = 2, "AI Assistant"

class Provider(Enum):
    OPENAI = "openai"
    CLAUDE = "claude"
    GEMINI = "gemini"
    LLAMA = "llama"

    @classmethod
    def choices(cls):
        return [(provider.value, provider.name.replace("_", " ").title()) for provider in cls]

class FeedbackType(models.TextChoices):
    LIKE = 'like', 'Like'
    DISLIKE = 'dislike', 'Dislike'
