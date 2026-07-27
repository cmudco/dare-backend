"""Permissions specific to Research Mode."""

from rest_framework.permissions import BasePermission

from feature_flags.services import is_flag_enabled_for_user


class IsResearchFeatureEnabled(BasePermission):
    """Require the global Research Mode release flag for every research API."""

    message = "Research Mode is not enabled for your account."

    def has_permission(self, request, view):
        return is_flag_enabled_for_user(request.user, "enable_research")
