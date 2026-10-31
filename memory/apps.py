from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured

from config import env
from memory.constants import EMBED_DIMS


class MemoryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "memory"
    verbose_name = "Cross-Conversation Memory"

    def ready(self):
        if env.MEMORY_EMBEDDING_DIMENSIONS != EMBED_DIMS:
            raise ImproperlyConfigured(
                f"MEMORY_EMBEDDING_DIMENSIONS={env.MEMORY_EMBEDDING_DIMENSIONS}, but "
                f"the memory embedding column is vector({EMBED_DIMS}). Use a memory "
                f"embedding model that returns {EMBED_DIMS} dimensions; changing the "
                "column needs a schema migration and re-embedding every memory row."
            )
