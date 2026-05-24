"""
MCP Tool Executor Service.

Bridges MCP tools with LLM tool calling format.
Handles tool discovery, format conversion, and execution routing
for use within chat conversations.
"""

import logging
import time
from typing import Optional

from asgiref.sync import sync_to_async

from mcp.models import MCPServer, UserMCPConnection, MCPToolExecution
from mcp.constants import ExecutionStatus, MCPAuthType
from mcp.services.mcp_manager import mcp_manager, MCPManagerError
from mcp.services.credential_service import MCPCredentialService
from mcp.services.oauth_service import mcp_oauth_service, MCPOAuthError

logger = logging.getLogger(__name__)


class MCPToolExecutorError(Exception):
    """Base exception for MCP tool executor errors."""
    pass


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
        tools = await executor.get_tools_for_conversation(user, conversation)
        result = await executor.execute_tool_call(
            user, "slack", "send_message", {"channel": "C123", "text": "Hi"},
            message=message_obj, conversation=conversation_obj
        )
    """

    async def get_tools_for_conversation(
        self,
        user,
        conversation,
    ) -> list[dict]:
        """
        Get all available tools from selected MCP servers for a conversation.
        
        Args:
            user: User instance
            conversation: Conversation instance with selected_mcp_servers
        
        Returns:
            List of tools in OpenAI function calling format
        """
        if not conversation or not user:
            return []

        # Get selected MCP servers for this conversation
        selected_servers = await self._get_selected_servers(conversation)
        if not selected_servers:
            return []

        logger.info(
            f"[MCPToolExecutor] Getting tools for {len(selected_servers)} selected servers"
        )

        all_tools = []
        for server in selected_servers:
            try:
                tools = await self._get_tools_for_server(user, server)
                all_tools.extend(tools)
            except Exception as e:
                logger.warning(
                    f"[MCPToolExecutor] Failed to get tools from {server.slug}: {e}"
                )
                # Continue with other servers if one fails

        logger.info(f"[MCPToolExecutor] Collected {len(all_tools)} total tools")
        return all_tools

    async def get_tools_for_server_ids(
        self,
        user,
        server_ids: list[int],
        llm_provider: str = "openai",
    ) -> list[dict]:
        """
        Get all available tools from specified MCP server IDs.
        
        This is the preferred method for LLM service integration.
        Fetches tools directly by ID without needing a conversation.
        
        Args:
            user: User instance
            server_ids: List of MCP server IDs
            llm_provider: LLM provider name for format conversion (openai/claude/gemini)
        
        Returns:
            List of tools in the appropriate LLM format
        """
        if not server_ids or not user:
            return []

        # Get servers by IDs
        servers = await self._get_servers_by_ids(server_ids)
        if not servers:
            return []

        logger.info(
            f"[MCPToolExecutor] Getting tools for {len(servers)} servers by ID"
        )

        all_tools = []
        for server in servers:
            try:
                tools = await self._get_tools_for_server(user, server)
                all_tools.extend(tools)
            except Exception as e:
                logger.warning(
                    f"[MCPToolExecutor] Failed to get tools from {server.slug}: {e}"
                )

        logger.info(f"[MCPToolExecutor] Collected {len(all_tools)} total tools")
        return all_tools

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

        credentials = await self._get_connection_credentials(connection)

        logger.info(
            f"[MCPToolExecutor] Executing {tool_name} on {server_slug} "
            f"for user {user.email}"
        )

        try:
            # Execute via MCPManager (handles subprocess, audit logging)
            result = await mcp_manager.call_tool(
                user=user,
                server=server,
                tool_name=tool_name,
                arguments=arguments,
                credentials=credentials,
            )

            # Update execution record with message/conversation context if provided
            if message or conversation:
                await self._update_execution_context(
                    user, server, tool_name, message, conversation
                )

            return result

        except MCPManagerError as e:
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
    def _get_selected_servers(self, conversation) -> list:
        """Get selected MCP servers for a conversation."""
        return list(conversation.selected_mcp_servers.filter(is_active=True))

    @sync_to_async
    def _get_server_by_slug(self, slug: str) -> Optional[MCPServer]:
        """Get MCP server by slug."""
        return MCPServer.active_objects.filter(slug=slug).first()

    @sync_to_async
    def _get_servers_by_ids(self, server_ids: list[int]) -> list[MCPServer]:
        """Get MCP servers by their IDs."""
        return list(MCPServer.active_objects.filter(id__in=server_ids))

    @sync_to_async
    def _get_user_connection(self, user, server) -> Optional[UserMCPConnection]:
        """Get user's connection to an MCP server."""
        return (
            UserMCPConnection.active_objects.select_related("server")
            .filter(user=user, server=server)
            .first()
        )

    async def _get_tools_for_server(self, user, server) -> list[dict]:
        """
        Get tools from a single MCP server and convert to OpenAI format.
        
        Args:
            user: User instance
            server: MCPServer instance
        
        Returns:
            List of OpenAI-format tool definitions
        """
        connection = await self._get_user_connection(user, server)
        if not connection or not self._connection_has_auth(connection):
            logger.debug(
                f"[MCPToolExecutor] User {user.email} has no connection to {server.slug}"
            )
            return []

        credentials = await self._get_connection_credentials(connection)

        # Get tools from cache or subprocess
        try:
            mcp_tools = await mcp_manager.get_available_tools(server, credentials)
        except MCPManagerError as e:
            logger.warning(f"[MCPToolExecutor] Failed to get tools from {server.slug}: {e}")
            return []

        # Convert to OpenAI format with server prefix
        openai_tools = [
            self.convert_to_openai_function(tool, server.slug)
            for tool in mcp_tools
        ]

        logger.debug(
            f"[MCPToolExecutor] Got {len(openai_tools)} tools from {server.slug}"
        )
        return openai_tools

    def _connection_has_auth(self, connection: UserMCPConnection) -> bool:
        if connection.server.auth_type == MCPAuthType.NONE:
            return True
        return bool(connection.encrypted_credentials)

    async def _get_connection_credentials(self, connection: UserMCPConnection) -> dict:
        credentials = MCPCredentialService.decrypt_credentials(
            connection.encrypted_credentials
        )
        if connection.server.auth_type != MCPAuthType.OAUTH2:
            return credentials

        expires_at = connection.auth_metadata.get("expires_at")
        refresh_token = MCPCredentialService.get_refresh_token(credentials)
        if not expires_at or not refresh_token or expires_at > int(time.time()) + 60:
            return credentials

        try:
            token = await mcp_oauth_service.refresh_access_token(
                connection.server,
                refresh_token,
            )
        except MCPOAuthError as error:
            logger.warning(
                f"[MCPToolExecutor] Failed to refresh OAuth token for {connection.server.slug}: {error}"
            )
            return credentials

        encrypted_credentials = MCPCredentialService.encrypt_credentials(
            token.to_credentials()
        )
        auth_metadata = token.to_metadata()
        await self._update_connection_auth(
            connection.id,
            encrypted_credentials,
            auth_metadata,
        )
        return token.to_credentials()

    @sync_to_async
    def _update_connection_auth(
        self,
        connection_id: int,
        encrypted_credentials: dict,
        auth_metadata: dict,
    ):
        UserMCPConnection.all_objects.filter(id=connection_id).update(
            encrypted_credentials=encrypted_credentials,
            auth_metadata=auth_metadata,
        )

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
