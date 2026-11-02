import sys
import warnings

from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        # Importing weaviate turns on every warning process-wide; once it has
        # run, put back Python's default of ignoring ResourceWarning.
        import weaviate  # noqa: F401

        if not sys.warnoptions:
            warnings.filterwarnings("ignore", category=ResourceWarning)
