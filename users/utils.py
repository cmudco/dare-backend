"""
Utility functions for platform detection and authentication.
"""
import time
from urllib.parse import urlparse

import jwt
from config.env import (
    DARE_FRONTEND_URL,
    SOCRATIC_BOTS_FRONTEND_URL,
    DARE_BACKEND_URL,
    SOCRATIC_BOTS_BACKEND_URL
)
from users.constants import AuthSourceChoice


def detect_platform_from_request(request):
    """
    Detect the platform source from the request headers.

    Args:
        request: Django request object

    Returns:
        str: Either 'DARE' or 'SocraticBots' based on the request origin
    """
    # Check Origin header first
    origin = request.META.get('HTTP_ORIGIN', '')

    # Check Referer header as fallback
    referer = request.META.get('HTTP_REFERER', '')

    # Check both origin and referer against platform URLs
    for header_value in [origin, referer]:
        if header_value:
            try:
                parsed_url = urlparse(header_value)
                base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"

                # Check against DARE URLs
                if _url_matches(base_url, DARE_FRONTEND_URL) or _url_matches(base_url, DARE_BACKEND_URL):
                    return AuthSourceChoice.DARE

                # Check against SocraticBots URLs
                elif _url_matches(base_url, SOCRATIC_BOTS_FRONTEND_URL) or _url_matches(base_url, SOCRATIC_BOTS_BACKEND_URL):
                    return AuthSourceChoice.SOCRATIC_BOTS

            except Exception:
                # If URL parsing fails, continue to next header
                continue

    # Default to DARE if no match found
    return AuthSourceChoice.DARE


def _url_matches(url1, url2):
    """
    Helper function to compare URLs by normalizing them.
    
    Args:
        url1: First URL to compare
        url2: Second URL to compare
        
    Returns:
        bool: True if URLs match after normalization
    """
    if not url1 or not url2:
        return False
    
    # Remove trailing slashes and convert to lowercase for comparison
    return url1.rstrip('/').lower() == url2.rstrip('/').lower()


# New: ASGI-safe platform detection for WebSocket scope
# This mirrors detect_platform_from_request but uses scope headers.
def detect_platform_from_scope(scope):
    """Detect platform from ASGI scope headers (Origin/Referer). Defaults to DARE."""
    try:
        headers = dict(
            (k.decode('latin1'), v.decode('latin1'))
            for k, v in (scope.get('headers') or [])
        )
        origin = headers.get('origin', '')
        referer = headers.get('referer', '')
        for header_value in [origin, referer]:
            if not header_value:
                continue
            parsed_url = urlparse(header_value)
            base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
            # DARE
            if _url_matches(base_url, DARE_FRONTEND_URL) or _url_matches(base_url, DARE_BACKEND_URL):
                return AuthSourceChoice.DARE
            # SocraticBots
            if _url_matches(base_url, SOCRATIC_BOTS_FRONTEND_URL) or _url_matches(base_url, SOCRATIC_BOTS_BACKEND_URL):
                return AuthSourceChoice.SOCRATIC_BOTS
    except Exception:
        pass
    return AuthSourceChoice.DARE


# New: Optional gate for learning progress
# Honors explicit flag if provided; otherwise only enables for Socratic FE.
def should_run_learning_progress(platform: str, explicit_flag: bool | None = None) -> bool:
    if explicit_flag is not None:
        return bool(explicit_flag)
    return platform == AuthSourceChoice.SOCRATIC_BOTS


def get_platform_access_permission(user, platform):
    """
    Check if a user has access to a specific platform based on their role.

    Role-based access matrix:
    - SUPERADMIN: DARE + SB
    - RESEARCHER: DARE + SB
    - USER: DARE + SB (consumer only in SB)
    - CREATOR: SB only
    - SB_USER: SB only

    Args:
        user: User instance
        platform: Platform name (AuthSourceChoice.DARE or AuthSourceChoice.SOCRATIC_BOTS)

    Returns:
        bool: True if user has access to the platform
    """
    from users.constants import RoleChoice

    role = getattr(user, 'platform_role', RoleChoice.USER)

    # Roles with DARE access
    dare_roles = {RoleChoice.SUPERADMIN, RoleChoice.SUPERVISOR, RoleChoice.RESEARCHER, RoleChoice.USER}

    # All roles have SB access (at minimum as consumer)
    sb_roles = {RoleChoice.SUPERADMIN, RoleChoice.SUPERVISOR, RoleChoice.RESEARCHER, RoleChoice.USER, RoleChoice.CREATOR, RoleChoice.SB_USER}

    if platform == AuthSourceChoice.DARE:
        return role in dare_roles
    elif platform == AuthSourceChoice.SOCRATIC_BOTS:
        return role in sb_roles

    return False


def get_platform_frontend_url(platform):
    """
    Get the frontend URL for a specific platform.

    Args:
        platform: Platform name (AuthSourceChoice.DARE or AuthSourceChoice.SOCRATIC_BOTS)

    Returns:
        str: Frontend URL for the platform
    """
    if platform == AuthSourceChoice.DARE:
        return DARE_FRONTEND_URL
    elif platform == AuthSourceChoice.SOCRATIC_BOTS:
        return SOCRATIC_BOTS_FRONTEND_URL

    return None


def syftbox_jwt_expired(access_token: str, leeway_seconds: int = 60) -> bool:
    """
    Return True if a SyftBox JWT access token is missing ``exp`` or is past expiry
    (with optional leeway), without verifying the signature.
    """
    try:
        payload = jwt.decode(
            access_token,
            algorithms=["RS256", "HS256", "ES256"],
            options={"verify_signature": False},
        )
    except jwt.exceptions.InvalidTokenError:
        return False
    exp = payload.get("exp")
    if exp is None:
        return False
    return time.time() >= (float(exp) - leeway_seconds)