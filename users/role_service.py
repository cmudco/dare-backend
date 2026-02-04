"""
Role hierarchy service for managing platform roles.

This module provides utilities for checking role permissions and hierarchy.
"""
from users.constants import RoleChoice


# Role hierarchy mapping - higher number means higher privileges
ROLE_HIERARCHY = {
    RoleChoice.SUPERADMIN: 4,
    RoleChoice.RESEARCHER: 3,
    RoleChoice.CREATOR: 2,
    RoleChoice.USER: 1,
}


def get_role_level(role: str) -> int:
    """
    Get the numeric level for a role.

    Args:
        role: The role string (e.g., 'CREATOR', 'USER')

    Returns:
        The numeric level of the role, or 0 if unknown
    """
    return ROLE_HIERARCHY.get(role, 0)


def has_role_or_higher(user, required_role: str) -> bool:
    """
    Check if a user has the required role or a higher one.

    Args:
        user: The user object to check
        required_role: The minimum required role

    Returns:
        True if the user has the required role or higher, False otherwise
    """
    # Superusers always have permission
    if user.is_superuser:
        return True

    user_level = get_role_level(user.platform_role)
    required_level = get_role_level(required_role)

    return user_level >= required_level


def is_superadmin(user) -> bool:
    """Check if user is a SuperAdmin."""
    return user.is_superuser or user.platform_role == RoleChoice.SUPERADMIN


def is_researcher_or_above(user) -> bool:
    """Check if user is a Researcher or higher."""
    return has_role_or_higher(user, RoleChoice.RESEARCHER)


def is_creator_or_above(user) -> bool:
    """Check if user is a Creator or higher."""
    return has_role_or_higher(user, RoleChoice.CREATOR)


def get_role_display(role: str) -> str:
    """
    Get the human-readable display name for a role.

    Args:
        role: The role string (e.g., 'CREATOR')

    Returns:
        The display name (e.g., 'Creator')
    """
    role_displays = {
        RoleChoice.SUPERADMIN: "Super Admin",
        RoleChoice.RESEARCHER: "Researcher",
        RoleChoice.CREATOR: "Creator",
        RoleChoice.USER: "User",
    }
    return role_displays.get(role, "Unknown")
