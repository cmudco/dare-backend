"""
Credential resolution, auth recovery, and health tracking for user MCP connections.

Every real discovery or tool call goes through ``call_with_auth_retry``, so a
connection's health is observed for free — there is no polling. A rejected
token gets one forced OAuth refresh and one retry; if that still fails the
connection is marked ``needs_reauth`` and later turns skip the network
entirely until the user reconnects. A server that is down or times out is
skipped for ``SERVER_DOWN_COOLDOWN`` seconds, so a hung server costs at most
one timeout per cooldown window. Health is written only when it changes.
"""

import logging
import time
from typing import Awaitable, Callable, Optional, TypeVar

import redis
from asgiref.sync import sync_to_async
from django.utils import timezone

from mcp.constants import (
    SERVER_DOWN_COOLDOWN,
    SERVER_DOWN_KEY_PREFIX,
    ConnectionHealth,
    MCPAuthType,
    MCPTransport,
)
from mcp.models import UserMCPConnection
from mcp.services.credential_service import MCPCredentialService
from mcp.services.mcp_client import (
    MCPAuthError,
    MCPConnectionError,
    MCPServerDownError,
    MCPTimeoutError,
)
from mcp.services.mcp_manager import (
    MCPManagerAuthError,
    MCPManagerUnreachableError,
    mcp_manager,
)
from mcp.services.oauth_service import MCPOAuthError, mcp_oauth_service

logger = logging.getLogger(__name__)

T = TypeVar("T")

REFRESH_SKEW_SECONDS = 60

_AUTH_ERRORS = (MCPAuthError, MCPManagerAuthError)
# Evidence the server itself is down for everyone: refused, 502-504, or a
# discovery timeout (tool-call timeouts never reach here as these types).
_SERVER_DOWN_ERRORS = (MCPServerDownError, MCPTimeoutError, MCPManagerUnreachableError)


class MCPReauthRequired(Exception):
    """The connection's credentials are rejected and could not be refreshed."""

    def __init__(self, server):
        self.server = server
        super().__init__(
            f"Your {server.name} connection has expired. "
            "Reconnect it to keep using its tools."
        )


class MCPServerUnavailable(Exception):
    """The server failed recently and is inside its cooldown window."""

    def __init__(self, server):
        self.server = server
        super().__init__(
            f"{server.name} isn't responding right now, so its tools are skipped."
        )


async def resolve_credentials(connection: UserMCPConnection) -> dict:
    """Decrypted credentials, proactively refreshed when an OAuth token is near expiry."""
    credentials = MCPCredentialService.decrypt_credentials(
        connection.encrypted_credentials
    )
    expires_at = (connection.auth_metadata or {}).get("expires_at")
    if expires_at and expires_at <= time.time() + REFRESH_SKEW_SECONDS:
        return await _refresh(connection, credentials) or credentials
    return credentials


async def call_with_auth_retry(
    connection: UserMCPConnection,
    operation: Callable[[dict], Awaitable[T]],
    recheck: bool = False,
) -> T:
    """Run ``operation(credentials)``, recovering once from rejected auth and recording health.

    ``recheck`` bypasses the expired and down short-circuits (explicit user tests).
    """
    server = connection.server
    if not recheck:
        if connection.health_status == ConnectionHealth.NEEDS_REAUTH:
            raise MCPReauthRequired(server)
        if _is_cooling_down(server):
            raise MCPServerUnavailable(server)

    credentials = await resolve_credentials(connection)
    try:
        try:
            result = await operation(credentials)
        except _AUTH_ERRORS:
            mcp_manager.forget_tools(server, credentials)
            refreshed = await _refresh(connection, credentials)
            if refreshed is None:
                raise
            result = await operation(refreshed)
    except _AUTH_ERRORS as error:
        if server.auth_type == MCPAuthType.NONE:
            # Nothing to reconnect; the server is misconfigured for everyone.
            await record_health(connection, ConnectionHealth.UNREACHABLE, str(error))
            raise
        await record_health(connection, ConnectionHealth.NEEDS_REAUTH, str(error))
        raise MCPReauthRequired(server) from error
    except _SERVER_DOWN_ERRORS as error:
        # A stdio server is a per-user subprocess, so its failure is not shared.
        if server.transport == MCPTransport.STREAMABLE_HTTP:
            _start_cooldown(server)
        await record_health(connection, ConnectionHealth.UNREACHABLE, str(error))
        raise
    except MCPConnectionError as error:
        # Other HTTP errors (403, 429, ...) or a crashed subprocess: specific
        # to this user's connection, so no server-wide cooldown.
        await record_health(connection, ConnectionHealth.UNREACHABLE, str(error))
        raise

    if recheck:
        _end_cooldown(server)
    await record_health(connection, ConnectionHealth.HEALTHY)
    return result


async def record_health(
    connection: UserMCPConnection, status: str, error: str = ""
) -> None:
    """Persist a health transition; no write when the status is unchanged.

    ``needs_reauth`` is only written while the stored credentials are the ones
    that failed: if a concurrent request already refreshed them, the flag
    would be stale and is dropped.
    """
    if connection.health_status == status:
        return
    connection.health_status = status
    connection.health_error = error[:500]
    connection.health_changed_at = timezone.now()
    logger.info(
        "[MCPHealth] %s connection %s -> %s %s",
        connection.server.slug,
        connection.id,
        status,
        error[:200],
    )
    rows = UserMCPConnection.all_objects.filter(id=connection.id)
    if status == ConnectionHealth.NEEDS_REAUTH:
        rows = rows.filter(encrypted_credentials=connection.encrypted_credentials)
    await sync_to_async(rows.update)(
        health_status=connection.health_status,
        health_error=connection.health_error,
        health_changed_at=connection.health_changed_at,
    )


def reset_health(connection: UserMCPConnection) -> list[str]:
    """Clear health for a freshly (re)connected connection; returns fields to save."""
    connection.health_status = ConnectionHealth.UNKNOWN
    connection.health_error = ""
    connection.health_changed_at = timezone.now()
    _end_cooldown(connection.server)
    return ["health_status", "health_error", "health_changed_at"]


def _is_cooling_down(server) -> bool:
    try:
        return bool(mcp_manager.redis.exists(f"{SERVER_DOWN_KEY_PREFIX}{server.slug}"))
    except redis.RedisError:
        return False


def _start_cooldown(server) -> None:
    try:
        mcp_manager.redis.setex(
            f"{SERVER_DOWN_KEY_PREFIX}{server.slug}", SERVER_DOWN_COOLDOWN, 1
        )
    except redis.RedisError as error:
        logger.warning(
            "[MCPHealth] Could not start cooldown for %s: %s", server.slug, error
        )


def _end_cooldown(server) -> None:
    try:
        mcp_manager.redis.delete(f"{SERVER_DOWN_KEY_PREFIX}{server.slug}")
    except redis.RedisError:
        pass


async def _refresh(connection: UserMCPConnection, credentials: dict) -> Optional[dict]:
    """Exchange the refresh token; returns new credentials or None when impossible."""
    if connection.server.auth_type != MCPAuthType.OAUTH2:
        return None
    refresh_token = MCPCredentialService.get_refresh_token(credentials)
    if not refresh_token:
        return None

    try:
        token = await mcp_oauth_service.refresh_access_token(
            connection.server, refresh_token
        )
    except MCPOAuthError as error:
        logger.warning(
            "[MCPHealth] OAuth refresh failed for %s: %s",
            connection.server.slug,
            error,
        )
        # A concurrent request may have already rotated the refresh token.
        return await _adopt_concurrent_refresh(connection)

    refreshed = token.to_credentials()
    # Providers that don't rotate omit refresh_token; keep the one we have.
    refreshed.setdefault("refresh_token", refresh_token)
    connection.encrypted_credentials = MCPCredentialService.encrypt_credentials(
        refreshed
    )
    connection.auth_metadata = token.to_metadata()
    # Fresh credentials supersede any needs_reauth a concurrent request wrote
    # against the old ones; the operation's outcome records the real status.
    connection.health_status = ConnectionHealth.UNKNOWN
    await sync_to_async(UserMCPConnection.all_objects.filter(id=connection.id).update)(
        encrypted_credentials=connection.encrypted_credentials,
        auth_metadata=connection.auth_metadata,
        health_status=connection.health_status,
    )
    return refreshed


async def _adopt_concurrent_refresh(
    connection: UserMCPConnection,
) -> Optional[dict]:
    latest = await sync_to_async(
        UserMCPConnection.all_objects.filter(id=connection.id)
        .values("encrypted_credentials", "auth_metadata")
        .first
    )()
    if (
        not latest
        or latest["encrypted_credentials"] == connection.encrypted_credentials
    ):
        return None
    connection.encrypted_credentials = latest["encrypted_credentials"]
    connection.auth_metadata = latest["auth_metadata"]
    return MCPCredentialService.decrypt_credentials(connection.encrypted_credentials)
