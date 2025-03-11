from enum import Enum
from django.db import models

APP_NAME = "conversations"

class SenderType(models.IntegerChoices):
    PLAYER = 1, "Player"
    AI_ASSISTANT = 2, "AI Assistant"

class Provider(Enum):
    OPENAI = "openai"
    CLAUDE = "claude"

    @classmethod
    def choices(cls):
        return [(provider.value, provider.name.replace("_", " ").title()) for provider in cls]
