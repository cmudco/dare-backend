from rest_framework.permissions import BasePermission

from users.constants import RoleChoice


class IsOwner(BasePermission):
    """
    Custom permission to only allow owners of an object to access it.
    """

    def has_object_permission(self, request, view, obj):
        return obj.user == request.user


class IsSuperAdmin(BasePermission):
    """
    Permission class that only allows SuperAdmins or Django superusers.
    """
    message = "Only super administrators can perform this action."

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        return (
            request.user.is_superuser or
            request.user.platform_role == RoleChoice.SUPERADMIN
        )


class IsResearcherOrAbove(BasePermission):
    """
    Permission class that allows Researchers and above (Researchers, SuperAdmins).
    """
    message = "Only researchers or administrators can perform this action."

    ALLOWED_ROLES = [RoleChoice.SUPERADMIN, RoleChoice.RESEARCHER]

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        return request.user.platform_role in self.ALLOWED_ROLES


class IsCreatorOrAbove(BasePermission):
    """
    Permission class that allows Creators and above (Creators, Researchers, SuperAdmins).
    """
    message = "Only creators or higher can perform this action."

    ALLOWED_ROLES = [RoleChoice.SUPERADMIN, RoleChoice.RESEARCHER, RoleChoice.CREATOR]

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        return request.user.platform_role in self.ALLOWED_ROLES


class HasRole(BasePermission):
    """
    Generic role check permission. Set 'required_role' on the view to use.

    Example:
        class MyView(APIView):
            permission_classes = [IsAuthenticated, HasRole]
            required_role = RoleChoice.CREATOR
    """
    message = "You do not have the required role to perform this action."

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True

        required_role = getattr(view, 'required_role', None)
        if required_role is None:
            return True  # No role required

        from users.role_service import has_role_or_higher
        return has_role_or_higher(request.user, required_role)