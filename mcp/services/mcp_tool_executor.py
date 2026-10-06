"""
MCP Tool Executor Service.

Bridges MCP tools with LLM tool calling format.
Handles tool discovery, format conversion, and execution routing
for use within chat conversations.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from asgiref.sync import sync_to_async

from mcp.constants import ConnectionHealth, MCPAuthType
from mcp.models import MCPServer, MCPToolExecution, UserMCPConnection
from mcp.services.connection_health import (
    MCPReauthRequired,
    MCPServerUnavailable,
    call_with_auth_retry,
)
from mcp.services.mcp_manager import MCPManagerError, mcp_manager

logger = logging.getLogger(__name__)


class MCPToolExecutorError(Exception):
    """Base exception for MCP tool executor errors."""
    pass


class MCPToolReauthError(MCPToolExecutorError):
    """The server rejected the user's connection; they must reconnect."""

    def __init__(self, server):
        self.server = server
        super().__init__(str(MCPReauthRequired(server)))


@dataclass
class MCPToolDiscovery:
    """Discovery outcome for one turn.

    ``tools`` go to the model, ``issues`` are the failures the user must hear
    about, and ``servers`` is the per-server report for the activity timeline.
    """

    tools: list[dict] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)
    servers: list[dict] = field(default_factory=list)


def connection_issue(server, status: str, message: str) -> dict:
    return {
        "server_slug": server.slug,
        "server_name": server.name,
        "auth_type": server.auth_type,
        "status": status,
        "message": message,
    }


class MCPToolExecutor:
    """
    Executes MCP tools in the context of LLM conversations.
    
    Bridges MCP tool schemas (JSON-RPC) with LLM function calling formats
    (OpenAI, Claude, Gemini) for use during chat message streaming.
    
    Responsibilities:
    - Discover tools from user's connected MCP servers
    - Convert MCP tool schemas to OpenAI function calling format
    - Execute tool calls and return results
    - Log executions with message/conversation context
    
    Usage:
        executor = MCPToolExecutor()
        discovery = await executor.get_tools_for_server_ids(user, [server.id])
        result = await executor.execute_tool_call(
            user, "slack", "send_message", {"channel": "C123", "text": "Hi"},
            message=message_obj, conversation=conversation_obj
        )
    """

    async def get_tools_for_server_ids(
        self,
        user,
        server_ids: list[int],
        llm_provider: str = "openai",
    ) -> MCPToolDiscovery:
        """
        Get tools from the given MCP server IDs, reporting servers that failed.

        A failing server never blocks the others: its tools are omitted and a
        connection issue is returned so the host can tell the user.
        """
        discovery = MCPToolDiscovery()
        if not server_ids or not user:
            return discovery

        connections = await self._get_user_connections(user, server_ids)
        results = await asyncio.gather(
            *(self._timed_discovery(connection) for connection in connections)
        )
        for connection, (result, ms) in zip(connections, results):
            server = connection.server
            if isinstance(result, MCPReauthRequired):
                status = ConnectionHealth.NEEDS_REAUTH
                discovery.issues.append(connection_issue(server, status, str(result)))
            elif isinstance(result, Exception):
                status = ConnectionHealth.UNREACHABLE
                logger.warning(
                    f"[MCPToolExecutor] Failed to get tools from {server.slug}: {result}"
                )
                discovery.issues.append(
                    connection_issue(
                        server,
                        ConnectionHealth.UNREACHABLE,
                        f"Couldn't reach {server.name}, so its tools are "
                        "unavailable for this message.",
                    )
                )
            else:
                status = ConnectionHealth.HEALTHY
                discovery.tools.extend(result)
            discovery.servers.append(
                {
                    "slug": server.slug,
                    "name": server.name,
                    "status": status,
                    "tools": 0 if isinstance(result, Exception) else len(result),
                    "ms": ms,
                }
            )

        logger.info(
            f"[MCPToolExecutor] Collected {len(discovery.tools)} tools, "
            f"{len(discovery.issues)} connection issues"
        )
        return discovery

    async def execute_tool_call(
        self,
        user,
        server_slug: str,
        tool_name: str,
        arguments: dict,
        message=None,
        conversation=None,
    ) -> dict:
        """
        Execute an MCP tool call and log execution with context.
        
        Args:
            user: User instance
            server_slug: Slug of the MCP server (extracted from tool name prefix)
            tool_name: Name of the tool (without server prefix)
            arguments: Tool arguments dict
            message: Optional Message instance for audit trail
            conversation: Optional Conversation instance for audit trail
        
        Returns:
            Tool execution result dict
        
        Raises:
            MCPToolExecutorError: If execution fails
        """
        # Get server and connection
        server = await self._get_server_by_slug(server_slug)
        if not server:
            raise MCPToolExecutorError(f"MCP server not found: {server_slug}")

        connection = await self._get_user_connection(user, server)
        if not connection or not self._connection_has_auth(connection):
            raise MCPToolExecutorError(
                f"No active connection to {server.name}. User must connect first."
            )

        logger.info(
            f"[MCPToolExecutor] Executing {tool_name} on {server_slug} "
            f"for user {user.email}"
        )

        try:
            result = await call_with_auth_retry(
                connection,
                lambda credentials: mcp_manager.call_tool(
                    user=user,
                    server=server,
                    tool_name=tool_name,
                    arguments=arguments,
                    credentials=credentials,
                ),
            )

            # Update execution record with message/conversation context if provided
            if message or conversation:
                await self._update_execution_context(
                    user, server, tool_name, message, conversation
                )

            return result

        except MCPReauthRequired:
            raise MCPToolReauthError(server)
        except (MCPManagerError, MCPServerUnavailable) as e:
            raise MCPToolExecutorError(str(e))

    def convert_to_openai_function(
        self,
        mcp_tool: dict,
        server_slug: str
    ) -> dict:
        """
        Convert MCP tool definition to OpenAI function calling format.
        
        Prefixes the tool name with server slug for routing:
        "send_message" -> "slack__send_message"
        
        Args:
            mcp_tool: MCP tool definition with name, description, inputSchema
            server_slug: Server slug for prefixing
        
        Returns:
            OpenAI function definition dict
        """
        tool_name = mcp_tool.get("name", "unknown_tool")
        prefixed_name = f"{server_slug}__{tool_name}"

        # Extract input schema (MCP uses JSON Schema format like OpenAI)
        input_schema = mcp_tool.get("inputSchema", {})

        return {
            "type": "function",
            "function": {
                "name": prefixed_name,
                "description": mcp_tool.get("description", f"Tool from {server_slug}"),
                "parameters": input_schema,
            }
        }

    @staticmethod
    def parse_tool_call_name(prefixed_name: str) -> tuple[str, str]:
        """
        Parse a prefixed tool name back to server_slug and tool_name.
        
        Args:
            prefixed_name: Tool name like "slack__send_message"
        
        Returns:
            Tuple of (server_slug, tool_name)
        
        Raises:
            ValueError: If name format is invalid
        """
        if "__" not in prefixed_name:
            raise ValueError(f"Invalid tool name format: {prefixed_name}")

        parts = prefixed_name.split("__", 1)
        return (parts[0], parts[1])

    # ========== Private Helper Methods ==========

    @sync_to_async
    def _get_server_by_slug(self, slug: str) -> Optional[MCPServer]:
        """Get MCP server by slug."""
        return MCPServer.active_objects.filter(slug=slug).first()

    @sync_to_async
    def _get_user_connections(
        self, user, server_ids: list[int]
    ) -> list[UserMCPConnection]:
        """The user's usable connections to the given active servers, in one query."""
        connections = UserMCPConnection.active_objects.select_related("server").filter(
            user=user,
            server_id__in=server_ids,
            server__is_active=True,
            server__is_deleted=False,
        )
        return [c for c in connections if self._connection_has_auth(c)]

    @sync_to_async
    def _get_user_connection(self, user, server) -> Optional[UserMCPConnection]:
        """Get user's connection to an MCP server."""
        return (
            UserMCPConnection.active_objects.select_related("server")
            .filter(user=user, server=server)
            .first()
        )

    async def _timed_discovery(
        self, connection: UserMCPConnection
    ) -> tuple[list[dict] | Exception, int]:
        """Discover one server, returning its tools (or the failure) and elapsed ms."""
        start = time.monotonic()
        try:
            result = await self._discover_tools(connection)
        except Exception as error:  # reported per server, never fails the turn
            result = error
        return result, int((time.monotonic() - start) * 1000)

    async def _discover_tools(self, connection: UserMCPConnection) -> list[dict]:
        """One server's tools in OpenAI format; raises on auth or reachability failure."""
        server = connection.server
        mcp_tools = await call_with_auth_retry(
            connection,
            lambda credentials: mcp_manager.get_available_tools(server, credentials),
        )
        return [self.convert_to_openai_function(tool, server.slug) for tool in mcp_tools]

    def _connection_has_auth(self, connection: UserMCPConnection) -> bool:
        if connection.server.auth_type == MCPAuthType.NONE:
            return True
        return bool(connection.encrypted_credentials)

    @sync_to_async
    def _update_execution_context(
        self,
        user,
        server,
        tool_name: str,
        message,
        conversation
    ):
        """
        Update the most recent execution record with message/conversation context.
        
        Called after mcp_manager.call_tool() creates the execution record.
        """
        # Get the most recent execution for this user/server/tool
        execution = MCPToolExecution.all_objects.filter(
            user=user,
            server=server,
            tool_name=tool_name
        ).order_by('-created_at').first()

        if execution:
            if message:
                execution.message = message
            if conversation:
                execution.conversation = conversation
            execution.save(update_fields=['message', 'conversation'])


# Global executor instance
mcp_tool_executor = MCPToolExecutor()
