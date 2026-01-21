"""
Utility functions for Socket.IO namespace handlers.

This module provides helper functions for WebSocket connection handling,
including platform detection based on HTTP headers.
"""

import logging
from typing import Optional

from config.env import (
    DARE_FRONTEND_URL,
    DARE_BACKEND_URL,
    SOCRATIC_BOTS_FRONTEND_URL,
    SOCRATIC_BOTS_BACKEND_URL,
)

logger = logging.getLogger(__name__)


def detect_platform_from_socketio_environ(environ: dict) -> str:
    """
    Detect the client platform from Socket.IO connection headers.

    Socket.IO passes HTTP headers through the environ dict during the initial
    handshake. This function extracts Origin/Referer headers and matches them
    against configured platform URLs to determine if the connection is from
    DARE or SocraticBots.

    Header Formats:
    ---------------
    Socket.IO can pass headers in two formats depending on the server setup:

    1. ASGI format (uvicorn/daphne):
       environ['asgi.http_headers'] = [(b'origin', b'http://localhost:5174'), ...]

    2. WSGI format (gunicorn/eventlet):
       environ['HTTP_ORIGIN'] = 'http://localhost:5174'
       environ['HTTP_REFERER'] = 'http://localhost:5174/'

    Args:
        environ: The Socket.IO environ dict from the connection handshake.
                 Contains HTTP headers and connection metadata.

    Returns:
        Platform identifier string:
        - "SocraticBots" if Origin/Referer matches SOCRATIC_BOTS_* URLs
        - "DARE" if Origin/Referer matches DARE_* URLs or no match found

    Example:
        >>> environ = {'HTTP_ORIGIN': 'http://localhost:5174'}
        >>> platform = detect_platform_from_socketio_environ(environ)
        >>> print(platform)  # "SocraticBots" (if 5174 is SocraticBots frontend)
    """
    origin, referer = _extract_headers_from_environ(environ)

    logger.info(f"[Socket.IO Platform Detection] Origin: {origin or 'N/A'}, Referer: {referer or 'N/A'}")

    platform = _match_url_to_platform(origin, referer)

    logger.info(f"[Socket.IO Platform Detection] Result: {platform}")

    return platform


def _extract_headers_from_environ(environ: dict) -> tuple[Optional[str], Optional[str]]:
    """
    Extract Origin and Referer headers from Socket.IO environ.

    Handles both ASGI and WSGI header formats.

    Args:
        environ: Socket.IO environ dict

    Returns:
        Tuple of (origin, referer) - either or both may be None
    """
    origin = None
    referer = None

    # Try ASGI format: headers as list of byte tuples
    asgi_headers = environ.get('asgi.http_headers', [])
    if asgi_headers:
        try:
            headers_dict = {
                k.decode('latin1').lower(): v.decode('latin1')
                for k, v in asgi_headers
            }
            origin = headers_dict.get('origin')
            referer = headers_dict.get('referer')
        except (AttributeError, UnicodeDecodeError):
            # Headers not in expected format, fall through to WSGI check
            pass

    # Try WSGI format: headers as HTTP_* keys
    if not origin:
        origin = environ.get('HTTP_ORIGIN')
    if not referer:
        referer = environ.get('HTTP_REFERER')

    return origin, referer


def _match_url_to_platform(origin: Optional[str], referer: Optional[str]) -> str:
    """
    Match Origin/Referer URL against configured platform URLs.

    Checks URLs in order: origin first, then referer.
    SocraticBots URLs are checked before DARE URLs to ensure
    SocraticBots connections are correctly identified.

    Args:
        origin: Origin header value (may be None)
        referer: Referer header value (may be None)

    Returns:
        "SocraticBots" or "DARE"
    """
    # Build URL mappings: normalized_url -> platform
    url_to_platform = {}

    # Add SocraticBots URLs first (checked with priority)
    if SOCRATIC_BOTS_FRONTEND_URL:
        url_to_platform[_normalize_url(SOCRATIC_BOTS_FRONTEND_URL)] = "SocraticBots"
    if SOCRATIC_BOTS_BACKEND_URL:
        url_to_platform[_normalize_url(SOCRATIC_BOTS_BACKEND_URL)] = "SocraticBots"

    # Add DARE URLs
    if DARE_FRONTEND_URL:
        url_to_platform[_normalize_url(DARE_FRONTEND_URL)] = "DARE"
    if DARE_BACKEND_URL:
        url_to_platform[_normalize_url(DARE_BACKEND_URL)] = "DARE"

    # Check origin and referer against mappings
    for url in [origin, referer]:
        if url:
            normalized = _normalize_url(url)
            if normalized in url_to_platform:
                return url_to_platform[normalized]

    # Default to DARE if no match
    return "DARE"


def _normalize_url(url: str) -> str:
    """
    Normalize URL for comparison: lowercase and strip trailing slash.

    Args:
        url: URL string to normalize

    Returns:
        Normalized URL string
    """
    return url.rstrip('/').lower()
