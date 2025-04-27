from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.utils.translation import gettext_lazy as _

from users.models import User
from users.constants import VectorDBChoice


class UserAdmin(DjangoUserAdmin):
    fieldsets = (
        (None, {"fields": ("email", "password", "is_active", "is_staff", "is_superuser")}),
        (
            _("Personal info"),
            {
                "fields": (
                    "first_name",
                    "last_name",
                )
            },
        ),
        (_("Vector Database Settings"), {"fields": ("vector_db",)}),
        (_("Important dates"), {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "password1", "password2", "vector_db", "is_superuser", "is_staff", "is_active"),
            },
        ),
    )
    list_display = ("email", "is_staff", "is_active", "is_superuser", "vector_db")
    list_filter = ("is_staff", "is_superuser", "is_active", "vector_db")
    search_fields = ("email", "first_name", "last_name")
    ordering = ("email",)


admin.site.register(User, UserAdmin)
