from django.apps import AppConfig


class ApiKeysConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "api_keys"
    verbose_name = "API Keys Management"

    def ready(self):
        """Import signal handlers when app is ready"""
        import api_keys.signals  # noqa
