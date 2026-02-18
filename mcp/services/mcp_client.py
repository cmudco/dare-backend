"""
MCP JSON-RPC Client.

Handles communication with MCP servers via JSON-RPC 2.0 protocol
over stdin/stdout of subprocess.
"""

import asyncio
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class MCPClientError(Exception):
    """Base exception for MCP client errors."""
    pass


class MCPConnectionError(MCPClientError):
    """Raised when connection to MCP server fails."""
    pass


class MCPTimeoutError(MCPClientError):
    """Raised when MCP operation times out."""
    pass


class MCPProtocolError(MCPClientError):
    """Raised when MCP server returns an error."""
    pass


class MCPClient:
    """
    JSON-RPC client for MCP server communication.
    
    Communicates with MCP servers via stdin/stdout using JSON-RPC 2.0 protocol.
    
    Usage:
        client = MCPClient(process)
        await client.initialize()
        tools = await client.list_tools()
        result = await client.call_tool("send_message", {"channel": "C123", "text": "Hello"})
    """

    def __init__(self, process: asyncio.subprocess.Process, timeout: float = 30.0, startup_delay: float = 10.0):
        """
        Initialize MCP client with a subprocess.
        
        Args:
            process: asyncio subprocess with stdin/stdout pipes
            timeout: Default timeout for operations in seconds
            startup_delay: Time to wait for server to cache users/channels before first request
        """
        self.process = process
        self.timeout = timeout
        self._startup_delay = startup_delay
        self.request_id = 0
        self._initialized = False

    async def initialize(self) -> dict:
        """
        Send initialize request to MCP server.
        
        Waits for server to be ready (cache users/channels) before sending.
        
        Returns:
            Server capabilities dict
        """
        # Wait for server to start and cache users/channels
        # slack-mcp-server needs time to sync before accepting tool calls
        await asyncio.sleep(self._startup_delay)
        
        response = await self._send_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {
                "name": "DARE",
                "version": "1.0.0"
            }
        })

        # Send initialized notification (no response expected)
        await self._send_notification("notifications/initialized", {})

        self._initialized = True
        return response

    async def list_tools(self) -> list[dict]:
        """
        Get list of available tools from the MCP server.
        
        Returns:
            List of tool definitions with name, description, inputSchema
        """
        if not self._initialized:
            await self.initialize()

        response = await self._send_request("tools/list", {})
        return response.get("tools", [])

    async def call_tool(self, name: str, arguments: dict) -> dict:
        """
        Execute a tool on the MCP server.
        
        Args:
            name: Tool name
            arguments: Tool arguments
        
        Returns:
            Tool execution result
        """
        if not self._initialized:
            await self.initialize()

        response = await self._send_request("tools/call", {
            "name": name,
            "arguments": arguments
        })
        return response

    async def close(self):
        """
        Close the MCP connection gracefully.
        """
        if self.process and self.process.returncode is None:
            try:
                self.process.terminate()
                await asyncio.wait_for(self.process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self.process.kill()
            except Exception as e:
                logger.warning(f"Error closing MCP process: {e}")

    async def _send_request(self, method: str, params: Optional[dict] = None) -> dict:
        """
        Send a JSON-RPC request and wait for response.
        
        Args:
            method: RPC method name
            params: Method parameters
        
        Returns:
            Response result
        
        Raises:
            MCPProtocolError: If server returns an error
            MCPTimeoutError: If operation times out
            MCPConnectionError: If connection fails
        """
        self.request_id += 1
        request_id = self.request_id

        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params is not None:
            request["params"] = params

        try:
            # Write request to stdin
            request_line = json.dumps(request) + "\n"
            self.process.stdin.write(request_line.encode())
            await self.process.stdin.drain()

            logger.debug(f"MCP Request: {method} (id={request_id})")

            # Read response from stdout
            response_line = await asyncio.wait_for(
                self.process.stdout.readline(),
                timeout=self.timeout
            )

            if not response_line:
                raise MCPConnectionError("MCP server closed connection")

            response = json.loads(response_line.decode().strip())

            # Validate response
            if response.get("id") != request_id:
                raise MCPProtocolError(f"Response ID mismatch: expected {request_id}, got {response.get('id')}")

            if "error" in response:
                error = response["error"]
                raise MCPProtocolError(f"MCP error {error.get('code', 'unknown')}: {error.get('message', 'Unknown error')}")

            logger.debug(f"MCP Response: {method} (id={request_id}) success")
            return response.get("result", {})

        except asyncio.TimeoutError:
            raise MCPTimeoutError(f"Timeout waiting for response to {method}")
        except json.JSONDecodeError as e:
            raise MCPProtocolError(f"Invalid JSON response: {e}")
        except BrokenPipeError:
            raise MCPConnectionError("MCP server process pipe broken")

    async def _send_notification(self, method: str, params: Optional[dict] = None):
        """
        Send a JSON-RPC notification (no response expected).
        
        Args:
            method: RPC method name
            params: Method parameters
        """
        notification = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params is not None:
            notification["params"] = params

        try:
            notification_line = json.dumps(notification) + "\n"
            self.process.stdin.write(notification_line.encode())
            await self.process.stdin.drain()
            logger.debug(f"MCP Notification: {method}")
        except Exception as e:
            logger.warning(f"Failed to send notification {method}: {e}")
