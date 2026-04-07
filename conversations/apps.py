from django.apps import AppConfig


class ConversationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "conversations"

    def ready(self):
        import conversations.signals  # noqa: F401
