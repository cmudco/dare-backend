"""
MCP Manager Service.

Manages MCP server subprocesses, tool discovery with Redis caching,
and tool execution with audit logging.
"""

import asyncio
import json
import logging
import os
import time
from typing import Optional

import redis
from django.conf import settings
from django.utils import timezone

from mcp.constants import (
    TOOL_CACHE_KEY_PREFIX,
    TOOL_CACHE_TTL,
    MCP_SUBPROCESS_TIMEOUT,
)
from mcp.services.mcp_client import MCPClient, MCPClientError

logger = logging.getLogger(__name__)


class MCPManagerError(Exception):
    """Base exception for MCP manager errors."""
    pass


class MCPManager:
    """
    Manages MCP server subprocesses and tool execution.
    
    Responsibilities:
    - Spawn/manage subprocess lifecycle
    - Handle JSON-RPC communication (stdin/stdout)  
    - Discover tools via tools/list with Redis caching
    - Execute tools via tools/call with audit logging
    
    Usage:
        manager = MCPManager()
        tools = await manager.get_available_tools(server, credentials)
        result = await manager.call_tool(user, server, "send_message", {"text": "Hi"}, credentials)
    """

    def __init__(self):
        """Initialize MCP manager with Redis connection."""
        self._redis: Optional[redis.Redis] = None

    @property
    def redis(self) -> redis.Redis:
        """Lazy Redis connection."""
        if self._redis is None:
            self._redis = redis.Redis(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                db=settings.REDIS_DB,
                password=settings.REDIS_PASSWORD or None,
                decode_responses=True
            )
        return self._redis

    async def get_available_tools(self, server, credentials: dict) -> list[dict]:
        """
        Get available tools for an MCP server.
        
        Checks Redis cache first, spawns subprocess to discover if cache miss.
        
        Args:
            server: MCPServer instance
            credentials: Decrypted credentials dict for env vars
        
        Returns:
            List of tool definitions
        """
        cache_key = f"{TOOL_CACHE_KEY_PREFIX}{server.slug}"

        # Check Redis cache first
        try:
            cached = self.redis.get(cache_key)
            if cached:
                logger.debug(f"Tools cache hit for {server.slug}")
                return json.loads(cached)
        except redis.RedisError as e:
            logger.warning(f"Redis error checking cache: {e}")

        # Cache miss - discover tools from subprocess
        logger.info(f"Discovering tools for {server.slug}")
        tools = await self._discover_tools(server, credentials)

        # Cache in Redis
        try:
            self.redis.setex(cache_key, TOOL_CACHE_TTL, json.dumps(tools))
            logger.debug(f"Cached {len(tools)} tools for {server.slug}")
        except redis.RedisError as e:
            logger.warning(f"Redis error caching tools: {e}")

        return tools

    async def call_tool(
        self,
        user,
        server,
        tool_name: str,
        arguments: dict,
        credentials: dict
    ) -> dict:
        """
        Execute a tool and log the execution.
        
        Args:
            user: User instance
            server: MCPServer instance
            tool_name: Name of tool to call
            arguments: Tool arguments
            credentials: Decrypted credentials dict
        
        Returns:
            Tool execution result
        """
        from mcp.models import MCPToolExecution, UserMCPConnection
        from mcp.constants import ExecutionStatus

        start_time = time.time()
        status = ExecutionStatus.PENDING
        result = None
        error_message = ""

        try:
            # Spawn subprocess and execute tool
            process = await self._spawn_subprocess(server, credentials)
            client = MCPClient(process, timeout=MCP_SUBPROCESS_TIMEOUT)

            try:
                result = await client.call_tool(tool_name, arguments)
                status = ExecutionStatus.SUCCESS
            finally:
                await client.close()

        except MCPClientError as e:
            status = ExecutionStatus.ERROR
            error_message = str(e)
            logger.error(f"MCP tool call failed: {e}")
        except Exception as e:
            status = ExecutionStatus.ERROR
            error_message = f"Unexpected error: {e}"
            logger.exception(f"Unexpected error in MCP tool call")

        execution_time_ms = int((time.time() - start_time) * 1000)

        # Log execution to database
        try:
            execution = await self._create_execution_record(
                user=user,
                server=server,
                tool_name=tool_name,
                tool_arguments=arguments,
                status=status,
                result=result,
                error_message=error_message,
                execution_time_ms=execution_time_ms
            )

            # Update last_used_at on connection
            await self._update_connection_last_used(user, server)

            logger.info(f"Tool execution logged: {execution.id} ({status})")
        except Exception as e:
            logger.error(f"Failed to log tool execution: {e}")

        if status == ExecutionStatus.ERROR:
            raise MCPManagerError(error_message)

        return result

    async def invalidate_tools_cache(self, server_slug: str):
        """
        Invalidate cached tools for a server.
        
        Called when admin updates MCPServer configuration.
        """
        cache_key = f"{TOOL_CACHE_KEY_PREFIX}{server_slug}"
        try:
            self.redis.delete(cache_key)
            logger.info(f"Invalidated tools cache for {server_slug}")
        except redis.RedisError as e:
            logger.warning(f"Redis error invalidating cache: {e}")

    async def test_connection(self, server, credentials: dict) -> tuple[bool, str]:
        """
        Test that credentials work by initializing connection.
        
        Args:
            server: MCPServer instance
            credentials: Decrypted credentials dict
        
        Returns:
            Tuple of (success, message)
        """
        try:
            process = await self._spawn_subprocess(server, credentials)
            client = MCPClient(process, timeout=MCP_SUBPROCESS_TIMEOUT)

            try:
                await client.initialize()
                tools = await client.list_tools()
                return True, f"Connection successful. {len(tools)} tools available."
            finally:
                await client.close()

        except MCPClientError as e:
            return False, str(e)
        except Exception as e:
            return False, f"Unexpected error: {e}"

    async def _discover_tools(self, server, credentials: dict) -> list[dict]:
        """
        Discover tools by spawning subprocess and calling tools/list.
        """
        process = await self._spawn_subprocess(server, credentials)
        client = MCPClient(process, timeout=MCP_SUBPROCESS_TIMEOUT)

        try:
            tools = await client.list_tools()
            return tools
        finally:
            await client.close()

    async def _spawn_subprocess(self, server, credentials: dict) -> asyncio.subprocess.Process:
        """
        Spawn MCP server subprocess with credentials as environment variables.
        
        Args:
            server: MCPServer instance with command and args
            credentials: Decrypted credentials to pass as env vars
        
        Returns:
            asyncio subprocess with stdin/stdout pipes
        """
        # Build environment with credentials
        # MCP servers expect UPPERCASE environment variable names
        env = os.environ.copy()
        for key, value in credentials.items():
            env[key.upper()] = value

        # Add server-configured extra env vars (already stored as uppercase keys)
        extra_vars = server.extra_env_vars if isinstance(server.extra_env_vars, dict) else {}
        env.update(extra_vars)

        # Get command and args from server config
        command = server.command
        args = server.args if isinstance(server.args, list) else []

        logger.debug(f"Spawning MCP subprocess: {command} {' '.join(args)}")

        try:
            process = await asyncio.create_subprocess_exec(
                command,
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env
            )
            return process
        except FileNotFoundError:
            raise MCPManagerError(f"Command not found: {command}")
        except Exception as e:
            raise MCPManagerError(f"Failed to spawn subprocess: {e}")

    async def _create_execution_record(
        self,
        user,
        server,
        tool_name: str,
        tool_arguments: dict,
        status: str,
        result: Optional[dict],
        error_message: str,
        execution_time_ms: int
    ):
        """Create MCPToolExecution record asynchronously."""
        from channels.db import database_sync_to_async
        from mcp.models import MCPToolExecution

        @database_sync_to_async
        def create_record():
            return MCPToolExecution.all_objects.create(
                user=user,
                server=server,
                tool_name=tool_name,
                tool_arguments=tool_arguments,
                status=status,
                result=result,
                error_message=error_message,
                execution_time_ms=execution_time_ms
            )

        return await create_record()

    async def _update_connection_last_used(self, user, server):
        """Update last_used_at on UserMCPConnection."""
        from channels.db import database_sync_to_async
        from mcp.models import UserMCPConnection

        @database_sync_to_async
        def update_last_used():
            UserMCPConnection.all_objects.filter(
                user=user,
                server=server
            ).update(last_used_at=timezone.now())

        await update_last_used()


# Global manager instance
mcp_manager = MCPManager()
