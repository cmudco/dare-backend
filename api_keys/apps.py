from django.apps import AppConfig


class ApiKeysConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "api_keys"
    verbose_name = "API Keys Management"

    def ready(self):
        """
        Import signal handlers when the app is ready.

        WHY THIS IMPORT IS NECESSARY:
        ------------------------------
        Unlike admin.py, Django does NOT autodiscover signals.py files.
        The @receiver decorators in signals.py only execute when that module
        is imported. Without this import, signal handlers are never registered.

        WHY IT'S IN ready() METHOD:
        ----------------------------
        1. Timing: ready() is called AFTER all models are loaded, preventing
           circular import errors that would occur if imported at module level.

        2. Order: Ensures signals reference models that definitely exist, since
           the app registry is fully populated before ready() is called.

        3. Single execution: Django guarantees ready() runs exactly once per app,
           preventing duplicate signal registration.

        ALTERNATIVES CONSIDERED:
        ------------------------
        - importlib.import_module('api_keys.signals'): Same effect, more verbose
        - Manual signal.connect(): Works but loses @receiver decorator benefits
        - Module-level import: BREAKS - causes circular imports

        This pattern is the official Django recommendation for signal registration.
        See: https://docs.djangoproject.com/en/stable/topics/signals/#connecting-receiver-functions
        """
        import api_keys.signals  # noqa: F401
