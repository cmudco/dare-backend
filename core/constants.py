from django.db import models
from django.utils.translation import gettext_lazy as _


class BackgroundTask(models.TextChoices):
    TITLES = "titles", _("Conversation titles")
    SUMMARIES = "summaries", _("Conversation summaries")
    MEMORY = "memory", _("Memory extraction")
    QUERY_ANALYSIS = "query_analysis", _("Retrieval query analysis")


class BackgroundModelSource(models.TextChoices):
    LITELLM_KEY = "litellm_key", _("Your LiteLLM key")
    USER = "user", _("Your choice")
    TASK_OVERRIDE = "task_override", _("Platform setting for this task")
    PLATFORM_DEFAULT = "platform_default", _("Platform default")
    ENVIRONMENT = "environment", _("Server default")
    UNAVAILABLE = "unavailable", _("Not configured")
