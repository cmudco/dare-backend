"""MCP client for hosted Streamable HTTP servers."""

import json
import logging
from typing import Optional

import httpx

from mcp.services.base_client import BaseMCPClient
from mcp.services.client_dtos import MCPConnectionConfig
from mcp.services.mcp_client import (
    MCPConnectionError,
    MCPProtocolError,
    MCPTimeoutError,
)

logger = logging.getLogger(__name__)


class StreamableHTTPMCPClient(BaseMCPClient):
    """
    JSON-RPC client for remote MCP Streamable HTTP servers.

    The MCP spec allows direct JSON responses or text/event-stream responses.
    This client accepts both so hosted providers can be added without a
    provider-specific bridge process.
    """

    def __init__(self, url: str, config: MCPConnectionConfig):
        self.url = url
        self.config = config
        self.request_id = 0
        self._initialized = False
        self._session_id: Optional[str] = None
        self._client = httpx.AsyncClient(timeout=config.timeout)

    async def initialize(self) -> dict:
        response = await self._send_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {
                "name": "DARE",
                "version": "1.0.0",
            },
        })
        await self._send_notification("notifications/initialized", {})
        self._initialized = True
        return response

    async def list_tools(self) -> list[dict]:
        if not self._initialized:
            await self.initialize()

        response = await self._send_request("tools/list", {})
        return response.get("tools", [])

    async def call_tool(self, name: str, arguments: dict) -> dict:
        if not self._initialized:
            await self.initialize()

        response = await self._send_request("tools/call", {
            "name": name,
            "arguments": arguments,
        }, timeout=self.config.tool_call_timeout)
        return response

    async def close(self):
        await self._client.aclose()

    async def _send_request(
        self,
        method: str,
        params: Optional[dict] = None,
        timeout: Optional[float] = None,
    ) -> dict:
        self.request_id += 1
        request_id = self.request_id
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params is not None:
            payload["params"] = params

        try:
            response = await self._client.post(
                self.url,
                json=payload,
                headers=self._build_headers(),
                timeout=timeout,
            )
        except httpx.TimeoutException as error:
            raise MCPTimeoutError(f"Timeout waiting for response to {method}") from error
        except httpx.HTTPError as error:
            raise MCPConnectionError(f"Remote MCP request failed: {error}") from error

        self._capture_session_id(response)
        rpc_response = self._parse_response(response)
        self._validate_response(rpc_response, request_id)
        logger.debug(f"Remote MCP response: {method} (id={request_id}) success")
        return rpc_response.get("result", {})

    async def _send_notification(self, method: str, params: Optional[dict] = None):
        payload = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params is not None:
            payload["params"] = params

        try:
            response = await self._client.post(
                self.url,
                json=payload,
                headers=self._build_headers(),
            )
            self._capture_session_id(response)
        except httpx.HTTPError as error:
            logger.warning(f"Failed to send remote MCP notification {method}: {error}")

    def _build_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": "2024-11-05",
            **self.config.remote_headers,
        }
        if self.config.access_token:
            headers["Authorization"] = f"Bearer {self.config.access_token}"
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    def _capture_session_id(self, response: httpx.Response):
        session_id = response.headers.get("mcp-session-id")
        if session_id:
            self._session_id = session_id

    def _parse_response(self, response: httpx.Response) -> dict:
        if response.status_code == 401:
            raise MCPConnectionError("Remote MCP server rejected authentication")
        if response.status_code >= 400:
            raise MCPConnectionError(
                f"Remote MCP server returned HTTP {response.status_code}: {response.text[:500]}"
            )

        content_type = response.headers.get("content-type", "")
        body = response.text.strip()
        if "text/event-stream" in content_type or body.startswith(("event:", "data:")):
            return self._parse_sse_body(body)

        try:
            return response.json()
        except json.JSONDecodeError as error:
            raise MCPProtocolError(f"Invalid remote MCP JSON response: {error}") from error

    def _parse_sse_body(self, body: str) -> dict:
        data_lines: list[str] = []
        for line in body.splitlines():
            if line.startswith("data:"):
                value = line.removeprefix("data:").strip()
                if value and value != "[DONE]":
                    data_lines.append(value)

        if not data_lines:
            raise MCPProtocolError("Remote MCP SSE response did not include data")

        try:
            return json.loads("\n".join(data_lines))
        except json.JSONDecodeError as error:
            raise MCPProtocolError(f"Invalid remote MCP SSE JSON response: {error}") from error

    def _validate_response(self, response: dict, request_id: int):
        if response.get("id") != request_id:
            raise MCPProtocolError(
                f"Response ID mismatch: expected {request_id}, got {response.get('id')}"
            )

        if "error" in response:
            error = response["error"]
            raise MCPProtocolError(
                f"MCP error {error.get('code', 'unknown')}: {error.get('message', 'Unknown error')}"
            )
