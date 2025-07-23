"""
Utility functions for platform detection and authentication.
"""
from urllib.parse import urlparse
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


def get_platform_access_permission(user, platform):
    """
    Check if a user has access to a specific platform.

    Args:
        user: User instance
        platform: Platform name (AuthSourceChoice.DARE or AuthSourceChoice.SOCRATIC_BOTS)

    Returns:
        bool: True if user has access to the platform
    """
    if platform == AuthSourceChoice.DARE:
        return user.is_dare_accessible
    elif platform == AuthSourceChoice.SOCRATIC_BOTS:
        return user.is_socratic_bots_accessible

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