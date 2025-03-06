from django.db import models

APP_NAME = "chats"

class SenderType(models.IntegerChoices):
    PLAYER = 1, "Player"
    AI_ASSISTANT = 2, "AI Assistant"
