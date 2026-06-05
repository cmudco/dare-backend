from rest_framework.permissions import BasePermission

from feature_flags.services import is_flag_enabled_for_user
from research.constants import ENABLE_RESEARCH_FLAG
from users.constants import RoleChoice


class CanAccessResearch(BasePermission):
    message = "Research is not enabled for this account."

    allowed_roles = {
        RoleChoice.SUPERADMIN,
        RoleChoice.SUPERVISOR,
        RoleChoice.RESEARCHER,
    }

    def has_permission(self, request, view) -> bool:
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser:
            return True
        if user.platform_role in self.allowed_roles:
            return True
        return is_flag_enabled_for_user(user, ENABLE_RESEARCH_FLAG)


__all__ = ["CanAccessResearch"]
