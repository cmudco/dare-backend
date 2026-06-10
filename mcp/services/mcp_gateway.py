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
from mcp.services.web_fetch import fetch_page

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2024-11-05"
_SEP = "__"  # namespace separator: <server_slug>__<tool_name>

# DARE-native gateway tools — available to every agent regardless of which MCP
# servers the user connected. No namespace prefix (they're the gateway's own).
_BUILTIN_TOOL_DEFS = [
    {
        "name": "fetch_page",
        "description": (
            "Fetch a web page (article, abstract, paper landing page) and "
            "return its readable text. Fast — prefer this over any browser or "
            "extract tool for reading links found by search tools."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The http(s) URL to read"}
            },
            "required": ["url"],
        },
    },
]

_BUILTIN_HANDLERS = {
    "fetch_page": lambda user, arguments: fetch_page(str(arguments.get("url") or "")),
}


def list_user_tools(user):
    """Namespaced tool definitions from the user's active connections."""
    tools = list(_BUILTIN_TOOL_DEFS)
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


def _result_text(result):
    """Flatten an MCP tool result into plain text (content blocks or JSON)."""
    if isinstance(result, dict):
        content = result.get("content")
        if isinstance(content, list):
            parts = [
                b.get("text", "")
                for b in content
                if isinstance(b, dict) and b.get("text")
            ]
            if parts:
                return "\n".join(parts)
        return json.dumps(result)
    return str(result)


def _compact_scholarly_hits(text):
    """
    Compact a scholarly search result (Scite-style JSON with `hits`) into one
    line per paper — title, authors, year, venue, DOI, citation tallies. The
    raw JSON buries ~1 paper in bloat per 4k chars; compacted, ten papers (with
    their DOIs, ready to fetch) fit in the same space. Returns None when the
    text isn't that shape.
    """
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    hits = data.get("hits") if isinstance(data, dict) else None
    if not isinstance(hits, list) or not hits:
        return None
    lines = []
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        authors = hit.get("authors") or []
        names = "; ".join(
            a.get("authorName", "") for a in authors[:3] if isinstance(a, dict)
        )
        tally = hit.get("tally") or {}
        cites = (
            f" | citations: {tally.get('supporting', 0)} supporting / "
            f"{tally.get('contrasting', 0)} contrasting"
            if tally
            else ""
        )
        doi = hit.get("doi") or ""
        lines.append(
            f"- {hit.get('title', '?')} ({names}, {hit.get('year', '?')}) "
            f"| {hit.get('journal', '?')}"
            + (f" | DOI: {doi} -> https://doi.org/{doi}" if doi else "")
            + cites
        )
    return "\n".join(lines) if lines else None


# Common names for a tool's free-text search parameter, in preference order.
_QUERY_PARAM_NAMES = ("query", "term", "q", "search", "keywords", "question")


def _query_param(tool):
    """
    Pick the parameter of `tool` that takes the free-text search query, from its
    input schema. Tools name it differently (Consensus `query`, Scite `term`);
    sending the wrong name silently degrades to an unfiltered search. Returns
    None when the tool has no string parameter to carry a query at all.
    """
    schema = tool.get("inputSchema") or tool.get("input_schema") or {}
    props = schema.get("properties") or {}
    string_props = [
        name
        for name, spec in props.items()
        if isinstance(spec, dict) and spec.get("type") == "string"
    ]
    for name in _QUERY_PARAM_NAMES:
        if name in string_props:
            return name
    for name in schema.get("required") or []:
        if name in string_props:
            return name
    return string_props[0] if string_props else None


def _search_with_primary_tool(user, conn, query, per_tool_chars):
    """
    Run one connection's primary (first cached) tool with the query. Returns a
    `{"slug", "tool", "text", "raw", "error"}` result — `text` is the prompt
    injection (compacted for scholarly results, capped), `raw` the complete
    untrimmed response for the audit record — or None if the connection has no
    usable search tool. MCP tool failures (`isError`) come back as `error`, not
    text: an error payload must never be injected as evidence.
    """
    slug = conn.server.slug
    tools = conn.cached_tools or []
    tool = tools[0] if tools else None
    tool_name = tool.get("name") if tool else None
    param = _query_param(tool) if tool_name else None
    if not param:
        logger.warning("gather_tool_results: %s has no usable search tool", slug)
        return None

    def entry(text="", raw="", error=""):
        return {
            "slug": slug,
            "tool": tool_name,
            "text": text,
            "raw": raw,
            "error": error,
        }

    try:
        result = async_to_sync(mcp_tool_executor.execute_tool_call)(
            user, slug, tool_name, {param: query}
        )
    except Exception as exc:  # noqa: BLE001 - audit the failure
        logger.warning("gather_tool_results %s.%s failed: %s", slug, tool_name, exc)
        return entry(error=str(exc)[:500])

    text = _result_text(result).strip()
    if isinstance(result, dict) and result.get("isError"):
        return entry(error=text or "Tool error")
    if not text:
        return None
    compact = _compact_scholarly_hits(text)
    return entry(text=(compact or text)[:per_tool_chars], raw=text)


def gather_tool_results(user, slugs, query, per_tool_chars=4000):
    """
    Run the primary tool of each of the user's connected servers whose slug is
    in `slugs` with the query — so a delegated run (Scout) can draw on
    credentialed tools (Consensus, Scite, …) that DARE executes on its behalf.
    Synchronous (for jobs); credentials and audit stay in DARE.
    """
    wanted = {s.lower() for s in (slugs or [])}
    if not wanted:
        return []
    connections = UserMCPConnection.all_objects.filter(
        user=user, is_active=True, is_deleted=False
    ).select_related("server")
    results = []
    for conn in connections:
        if conn.server.slug.lower() not in wanted:
            continue
        entry = _search_with_primary_tool(user, conn, query, per_tool_chars)
        if entry:
            results.append(entry)
    return results


def gather_tool_context(user, slugs, query, per_tool_chars=4000):
    """`gather_tool_results` as one text block (successes only), for prompt injection."""
    return "\n\n".join(
        f"### {r['slug']} · {r['tool']}\n{r['text']}"
        for r in gather_tool_results(user, slugs, query, per_tool_chars)
        if r["text"] and not r["error"]
    )


def _result(rpc_id, result):
    return {"jsonrpc": "2.0", "id": rpc_id, "result": result}


def _error(rpc_id, code, message):
    return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}


def _handle_tool_call(user, rpc_id, params):
    """Route one tools/call to a gateway builtin or the user's tool executor."""
    name = params.get("name", "")
    arguments = params.get("arguments") or {}

    if name in _BUILTIN_HANDLERS:
        try:
            text = _BUILTIN_HANDLERS[name](user, arguments)
        except Exception as exc:  # noqa: BLE001 - surface as a tool error
            logger.warning("MCP gateway builtin %s failed: %s", name, exc)
            return _error(rpc_id, -32000, str(exc))
        return _result(rpc_id, {"content": [{"type": "text", "text": text}]})

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
        return _handle_tool_call(user, rpc_id, payload.get("params") or {})
    return _error(rpc_id, -32601, f"Method not found: {method}")
