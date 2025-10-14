"""Custom admin filters for the users app."""

from datetime import timedelta

from django.contrib import admin
from django.utils import timezone


class LastLoginFilter(admin.SimpleListFilter):
    """Filter users by last login activity."""

    title = "last login activity"
    parameter_name = "last_login_activity"

    def lookups(self, request, model_admin):
        return (
            ("never", "Never logged in"),
            ("7days", "Last 7 days"),
            ("30days", "Last 30 days"),
            ("90days", "Last 90 days"),
            ("inactive_30", "Inactive 30+ days"),
            ("inactive_90", "Inactive 90+ days"),
            ("inactive_180", "Inactive 180+ days"),
        )

    def queryset(self, request, queryset):
        now = timezone.now()
        value = self.value()

        if value == "never":
            return queryset.filter(last_login__isnull=True)
        if value == "7days":
            return queryset.filter(last_login__gte=now - timedelta(days=7))
        if value == "30days":
            return queryset.filter(last_login__gte=now - timedelta(days=30))
        if value == "90days":
            return queryset.filter(last_login__gte=now - timedelta(days=90))
        if value == "inactive_30":
            return queryset.filter(last_login__lt=now - timedelta(days=30))
        if value == "inactive_90":
            return queryset.filter(last_login__lt=now - timedelta(days=90))
        if value == "inactive_180":
            return queryset.filter(last_login__lt=now - timedelta(days=180))
        return queryset
