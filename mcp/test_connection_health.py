"""Tests for MCP connection auth recovery and health tracking."""

from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.test import TestCase

from mcp.constants import ConnectionHealth, MCPAuthType, MCPTransport
from mcp.models import MCPServer, UserMCPConnection
from mcp.services.connection_health import (
    MCPReauthRequired,
    MCPServerUnavailable,
    call_with_auth_retry,
    record_health,
)
from mcp.services.credential_service import MCPCredentialService
from mcp.services.mcp_client import MCPAuthError, MCPConnectionError, MCPTimeoutError
from mcp.services.mcp_manager import mcp_manager
from mcp.services.oauth_service import MCPOAuthError, MCPOAuthToken
from users.models import User

REFRESH = "mcp.services.connection_health.mcp_oauth_service.refresh_access_token"


class CallWithAuthRetryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="health@example.com", password="x")
        self.server = MCPServer.all_objects.create(
            name="Scite",
            slug="scite-health-test",
            transport=MCPTransport.STREAMABLE_HTTP,
            auth_type=MCPAuthType.OAUTH2,
            remote_url="https://example.test/mcp",
        )
        self.connection = UserMCPConnection.all_objects.create(
            user=self.user,
            server=self.server,
            encrypted_credentials=MCPCredentialService.encrypt_credentials(
                {"access_token": "old", "refresh_token": "keep-me"}
            ),
        )
        down_key = f"mcp:down:{self.server.slug}"
        mcp_manager.redis.delete(down_key)
        self.addCleanup(mcp_manager.redis.delete, down_key)

    def _run(self, operation, **kwargs):
        connection = UserMCPConnection.all_objects.select_related("server").get(
            id=self.connection.id
        )
        return async_to_sync(call_with_auth_retry)(connection, operation, **kwargs)

    def _stored_credentials(self):
        self.connection.refresh_from_db()
        return MCPCredentialService.decrypt_credentials(
            self.connection.encrypted_credentials
        )

    def test_rejected_token_is_refreshed_and_retried(self):
        seen = []

        async def operation(credentials):
            seen.append(credentials["access_token"])
            if credentials["access_token"] == "old":
                raise MCPAuthError("401")
            return "ok"

        token = MCPOAuthToken("new", "", 3600, "Bearer", "")
        with patch(REFRESH, AsyncMock(return_value=token)):
            self.assertEqual(self._run(operation), "ok")

        self.assertEqual(seen, ["old", "new"])
        stored = self._stored_credentials()
        self.assertEqual(stored["access_token"], "new")
        self.assertEqual(stored["refresh_token"], "keep-me")
        self.assertEqual(self.connection.health_status, ConnectionHealth.HEALTHY)

    def test_failed_refresh_marks_needs_reauth_and_short_circuits(self):
        operation = AsyncMock(side_effect=MCPAuthError("401"))
        with patch(REFRESH, AsyncMock(side_effect=MCPOAuthError("invalid_grant"))):
            with self.assertRaises(MCPReauthRequired):
                self._run(operation)

        self.connection.refresh_from_db()
        self.assertEqual(self.connection.health_status, ConnectionHealth.NEEDS_REAUTH)

        operation.reset_mock()
        with self.assertRaises(MCPReauthRequired):
            self._run(operation)
        operation.assert_not_called()

        operation.side_effect = None
        operation.return_value = "back"
        self.assertEqual(self._run(operation, recheck=True), "back")
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.health_status, ConnectionHealth.HEALTHY)

    def test_timeout_marks_unreachable_and_cools_down(self):
        operation = AsyncMock(side_effect=MCPTimeoutError("slow"))
        with self.assertRaises(MCPTimeoutError):
            self._run(operation)
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.health_status, ConnectionHealth.UNREACHABLE)

        operation.reset_mock()
        with self.assertRaises(MCPServerUnavailable):
            self._run(operation)
        operation.assert_not_called()

        operation.side_effect = None
        operation.return_value = "up"
        self.assertEqual(self._run(operation, recheck=True), "up")
        self.assertEqual(self._run(operation), "up")

    def test_per_connection_http_error_does_not_cool_down_server(self):
        with self.assertRaises(MCPConnectionError):
            self._run(AsyncMock(side_effect=MCPConnectionError("HTTP 429")))
        self.assertEqual(self._run(AsyncMock(return_value="ok")), "ok")

    def test_stale_needs_reauth_is_not_written_over_refreshed_credentials(self):
        connection = UserMCPConnection.all_objects.select_related("server").get(
            id=self.connection.id
        )
        UserMCPConnection.all_objects.filter(id=connection.id).update(
            encrypted_credentials=MCPCredentialService.encrypt_credentials(
                {"access_token": "rotated", "refresh_token": "new"}
            )
        )
        async_to_sync(record_health)(connection, ConnectionHealth.NEEDS_REAUTH, "401")
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.health_status, ConnectionHealth.UNKNOWN)
