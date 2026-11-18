from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured

from config import env
from users.role_service import ROLE_HIERARCHY


class UsersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "users"

    def ready(self):
        import users.signals  # noqa

        unknown = set(env.OIDC_ROLE_MAP.values()) - set(ROLE_HIERARCHY)
        if unknown:
            raise ImproperlyConfigured(
                f"OIDC_ROLE_MAP names unknown roles {sorted(unknown)}; "
                f"use one of {sorted(role.value for role in ROLE_HIERARCHY)}"
            )
