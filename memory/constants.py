"""Constants for the memory app."""

from django.db import models


class MemoryType(models.TextChoices):
    """Memory item types supported by the MemU store."""

    PROFILE = "profile", "Profile"
    EVENT = "event", "Event"
    KNOWLEDGE = "knowledge", "Knowledge"
    BEHAVIOR = "behavior", "Behavior"


DEFAULT_MEMORY_TYPE = MemoryType.PROFILE.value

# Guardrails for user-provided import payloads. Each imported item triggers a
# MemU write (and embedding), so unbounded batches are a cost/timeout risk.
MEMORY_IMPORT_MAX_ITEMS = 200
MEMORY_IMPORT_MAX_CONTENT_LENGTH = 4000
MEMORY_IMPORT_MAX_CATEGORIES = 10
MEMORY_IMPORT_MAX_CATEGORY_LENGTH = 100
