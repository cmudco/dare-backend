"""
DARE MCP gateway — exposes a user's connected MCP tools to an external agent
(Hermes) over the MCP Streamable HTTP protocol, while credentials and audit stay
in DARE.

Hermes connects once (`hermes mcp add dare --url <gateway>`); every tool from the
user's connected servers (Consensus, Scite, Scholar, …) becomes available,
namespaced `<server>__<tool>`. A tools/call is routed through DARE's existing
executor, which decrypts creds and logs an MCPToolExecution. No creds leave DARE.
"""

import json
import logging

from asgiref.sync import async_to_sync

from mcp.models import UserMCPConnection
from mcp.services.mcp_tool_executor import mcp_tool_executor

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2024-11-05"
_SEP = "__"  # namespace separator: <server_slug>__<tool_name>


def list_user_tools(user):
    """Namespaced tool definitions from the user's active connections."""
    tools = []
    connections = UserMCPConnection.all_objects.filter(
        user=user, is_active=True, is_deleted=False
    ).select_related("server")
    for conn in connections:
        slug = conn.server.slug
        for tool in conn.cached_tools or []:
            name = tool.get("name")
            if not name:
                continue
            tools.append(
                {
                    "name": f"{slug}{_SEP}{name}",
                    "description": tool.get("description", "") or "",
                    "inputSchema": tool.get("inputSchema")
                    or tool.get("input_schema")
                    or {"type": "object"},
                }
            )
    return tools


def _result(rpc_id, result):
    return {"jsonrpc": "2.0", "id": rpc_id, "result": result}


def _error(rpc_id, code, message):
    return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}


def handle_jsonrpc(user, payload):
    """
    Handle one MCP JSON-RPC message. Returns the response dict, or None for
    notifications (which take no response).
    """
    method = payload.get("method")
    rpc_id = payload.get("id")

    if method == "initialize":
        return _result(
            rpc_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "dare-mcp-gateway", "version": "1.0.0"},
            },
        )

    # Notifications (no id) — acknowledge with no body.
    if rpc_id is None or method == "notifications/initialized":
        return None

    if method == "tools/list":
        return _result(rpc_id, {"tools": list_user_tools(user)})

    if method == "tools/call":
        params = payload.get("params") or {}
        name = params.get("name", "")
        arguments = params.get("arguments") or {}
        if _SEP not in name:
            return _error(rpc_id, -32602, f"Unknown tool: {name!r}")
        server_slug, tool_name = name.split(_SEP, 1)
        try:
            result = async_to_sync(mcp_tool_executor.execute_tool_call)(
                user, server_slug, tool_name, arguments
            )
        except Exception as exc:  # noqa: BLE001 - surface as a tool error
            logger.warning("MCP gateway tool %s failed: %s", name, exc)
            return _error(rpc_id, -32000, str(exc))

        # The executor returns the tool result; normalise to MCP content.
        if isinstance(result, dict) and "content" in result:
            return _result(rpc_id, result)
        text = result if isinstance(result, str) else json.dumps(result)
        return _result(rpc_id, {"content": [{"type": "text", "text": text}]})

    return _error(rpc_id, -32601, f"Method not found: {method}")
