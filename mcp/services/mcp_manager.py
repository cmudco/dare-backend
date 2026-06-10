"""
MCP Manager Service.

Manages MCP server subprocesses, tool discovery with Redis caching,
and tool execution with audit logging.
"""

import asyncio
import hashlib
import json
import logging
import os
import time
from typing import Optional

import redis
from channels.db import database_sync_to_async
from django.conf import settings
from django.utils import timezone

from mcp.constants import (
    MCP_REMOTE_REQUEST_TIMEOUT,
    MCP_REMOTE_TOOL_CALL_TIMEOUT,
    MCP_SUBPROCESS_TIMEOUT,
    MCP_USE_DOCKER,
    TOOL_CACHE_KEY_PREFIX,
    TOOL_CACHE_TTL,
    ExecutionStatus,
    MCPTransport,
)
from mcp.models import MCPToolExecution, UserMCPConnection
from mcp.services.client_dtos import MCPConnectionConfig
from mcp.services.credential_service import MCPCredentialService
from mcp.services.mcp_client import MCPClient, MCPClientError
from mcp.services.streamable_http_client import StreamableHTTPMCPClient

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
                decode_responses=True,
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
        cache_key = self._tools_cache_key(server, credentials)

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

        # Cache in Redis. Never cache an empty list: a remote that answers
        # mid-warmup with no tools would otherwise pin "no tools" for the
        # whole TTL and make reconnect attempts look broken until it expires.
        if tools:
            try:
                self.redis.setex(cache_key, TOOL_CACHE_TTL, json.dumps(tools))
                logger.debug(f"Cached {len(tools)} tools for {server.slug}")
            except redis.RedisError as e:
                logger.warning(f"Redis error caching tools: {e}")
        else:
            logger.warning(
                f"Tool discovery for {server.slug} returned no tools; not caching"
            )

        return tools

    async def call_tool(
        self, user, server, tool_name: str, arguments: dict, credentials: dict
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

        start_time = time.time()
        status = ExecutionStatus.PENDING
        result = None
        error_message = ""

        try:
            client = await self._build_client(server, credentials)
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
                execution_time_ms=execution_time_ms,
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
            client = await self._build_client(server, credentials)

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

        Retries transient client failures (timeouts, cold remote, brief network
        flaps) with a short backoff — a single-attempt discovery is what made
        first-time connects intermittently fail until the user reconnected.
        """
        attempts = 3
        for attempt in range(1, attempts + 1):
            client = await self._build_client(server, credentials)
            try:
                return await client.list_tools()
            except MCPClientError as e:
                if attempt == attempts:
                    raise
                logger.warning(
                    f"Tool discovery for {server.slug} failed "
                    f"(attempt {attempt}/{attempts}): {e}; retrying"
                )
                await asyncio.sleep(attempt)
            finally:
                await client.close()

    async def _build_client(self, server, credentials: dict):
        """Build a transport-specific MCP client for this server."""
        if server.transport == MCPTransport.STREAMABLE_HTTP:
            if not server.remote_url:
                raise MCPManagerError(f"No remote URL configured for {server.slug}")
            remote_headers = (
                server.remote_headers if isinstance(server.remote_headers, dict) else {}
            )
            config = MCPConnectionConfig(
                timeout=MCP_REMOTE_REQUEST_TIMEOUT,
                tool_call_timeout=MCP_REMOTE_TOOL_CALL_TIMEOUT,
                access_token=MCPCredentialService.get_access_token(credentials),
                remote_headers={
                    str(key): str(value)
                    for key, value in remote_headers.items()
                    if value is not None
                },
            )
            return StreamableHTTPMCPClient(server.remote_url, config)

        process = await self._spawn_subprocess(server, credentials)
        return MCPClient(process, timeout=MCP_SUBPROCESS_TIMEOUT)

    def _tools_cache_key(self, server, credentials: dict) -> str:
        if server.transport != MCPTransport.STREAMABLE_HTTP:
            return f"{TOOL_CACHE_KEY_PREFIX}{server.slug}"

        access_token = MCPCredentialService.get_access_token(credentials)
        token_hash = (
            hashlib.sha256(access_token.encode("utf-8")).hexdigest()[:16]
            if access_token
            else "anonymous"
        )
        return f"{TOOL_CACHE_KEY_PREFIX}{server.slug}:{token_hash}"

    async def _spawn_subprocess(
        self, server, credentials: dict
    ) -> asyncio.subprocess.Process:
        """
        Spawn MCP server subprocess with credentials as environment variables.

        In Docker mode: runs `docker run -i --rm -e CREDS image`
        In dev mode: runs `npx -y package` (existing behavior)

        Args:
            server: MCPServer instance with command and args
            credentials: Decrypted credentials to pass as env vars

        Returns:
            asyncio subprocess with stdin/stdout pipes
        """

        # Get extra env vars from server config
        extra_vars = (
            server.extra_env_vars if isinstance(server.extra_env_vars, dict) else {}
        )

        if MCP_USE_DOCKER:
            # Docker mode: run containerized MCP server
            if not server.docker_image:
                raise MCPManagerError(f"No Docker image configured for {server.slug}")
            image = server.docker_image

            # Build docker run command
            command = "docker"
            args = ["run", "-i", "--rm"]

            # Pass credentials as -e flags (uppercase keys)
            for key, value in credentials.items():
                args.extend(["-e", f"{key.upper()}={value}"])

            # Pass extra env vars as -e flags
            for key, value in extra_vars.items():
                args.extend(["-e", f"{key}={value}"])

            # Add the image name
            args.append(image)

            # Log without exposing credential values
            logger.debug(f"Spawning Docker MCP: docker run -i --rm [env vars] {image}")

            # Docker mode doesn't need env passed to subprocess (uses -e flags)
            env = None
        else:
            # Dev mode: existing npx behavior
            # Build environment with credentials (uppercase keys)
            env = os.environ.copy()
            for key, value in credentials.items():
                env[key.upper()] = value

            # Add server-configured extra env vars
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
                env=env,
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
        execution_time_ms: int,
    ):
        """Create MCPToolExecution record asynchronously."""

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
                execution_time_ms=execution_time_ms,
            )

        return await create_record()

    async def _update_connection_last_used(self, user, server):
        """Update last_used_at on UserMCPConnection."""

        @database_sync_to_async
        def update_last_used():
            UserMCPConnection.all_objects.filter(user=user, server=server).update(
                last_used_at=timezone.now()
            )

        await update_last_used()


# Global manager instance
mcp_manager = MCPManager()
