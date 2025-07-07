"""
Utility functions for platform detection and authentication.
"""
from urllib.parse import urlparse
from config.env import (
    DARE_FRONTEND_URL,
    SOCRATIC_BOOKS_FRONTEND_URL,
    DARE_BACKEND_URL,
    SOCRATIC_BOOKS_BACKEND_URL
)
from users.constants import AuthSourceChoice, CallbackChoice


def detect_platform_from_request(request):
    """
    Detect the platform source from the request headers.

    Args:
        request: Django request object

    Returns:
        str: Either 'DARE' or 'SocraticBooks' based on the request origin
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

                # Check against SocraticBooks URLs
                elif _url_matches(base_url, SOCRATIC_BOOKS_FRONTEND_URL) or _url_matches(base_url, SOCRATIC_BOOKS_BACKEND_URL):
                    return AuthSourceChoice.SOCRATIC_BOOKS

            except Exception:
                # If URL parsing fails, continue to next header
                continue

    # Default to DARE if no match found
    return AuthSourceChoice.DARE


def _url_matches(url1, url2):
    """
    Helper function to compare URLs by normalizing them.

    Args:
        url1: First URL string
        url2: Second URL string

    Returns:
        bool: True if URLs match after normalization
    """
    try:
        parsed1 = urlparse(url1)
        parsed2 = urlparse(url2)

        # Compare scheme and netloc (host:port)
        return (parsed1.scheme == parsed2.scheme and
                parsed1.netloc == parsed2.netloc)
    except Exception:
        return False


def get_platform_access_permission(user, platform):
    """
    Check if a user has access to a specific platform.

    Args:
        user: User instance
        platform: Platform name (AuthSourceChoice.DARE or AuthSourceChoice.SOCRATIC_BOOKS)

    Returns:
        bool: True if user has access to the platform
    """
    if platform == AuthSourceChoice.DARE:
        return user.is_dare_accessible
    elif platform == AuthSourceChoice.SOCRATIC_BOOKS:
        return user.is_socratic_books_accessible

    return False


def get_callback_parameter(platform):
    """
    Get callback URL based on platform using frontend environment variables.

    Args:
        platform: Platform name (AuthSourceChoice.DARE or AuthSourceChoice.SOCRATIC_BOOKS)

    Returns:
        str: Full frontend URL for the platform
    """
    if platform == AuthSourceChoice.DARE:
        return DARE_FRONTEND_URL or "http://localhost:5173"
    elif platform == AuthSourceChoice.SOCRATIC_BOOKS:
        return SOCRATIC_BOOKS_FRONTEND_URL or "http://localhost:5174"

    return DARE_FRONTEND_URL or "http://localhost:5173"  # Default to DARE