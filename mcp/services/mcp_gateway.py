"""
DARE's MCP gateway: the tools a research agent (Hermes) calls.

Each project profile connects as its owner, naming its project in the
``X-DARE-Project`` header. It is offered DARE's own tools (search, papers,
page reader), the research-thread tools, and the tools of every MCP server
the owner connected and enabled for that project, namespaced
``<server>__<tool>``. DARE runs those calls with the owner's credentials and
records each one, so credentials and audit stay here.

Each MCP session remembers the tool set it last listed. When the set changes,
the next response announces ``notifications/tools/list_changed`` ahead of its
result, and Hermes lists the tools again and updates its own registry.
"""

import hashlib
import json
import logging
import secrets
from datetime import timedelta

from asgiref.sync import async_to_sync
from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone

from mcp.models import GatewayFetch, UserMCPConnection
from mcp.services.mcp_tool_executor import MCPToolExecutorError, mcp_tool_executor
from mcp.services.scholarly import (
    ScholarlyError,
    doi_in,
    open_access_url,
    search_papers,
    work_by_doi,
)
from mcp.services.web_fetch import PageUnreadable, fetch_page, web_search
from research.constants import scout_limits
from research.services.thread_service import ThreadTokenInvalid, resolve_run_token
from research.services.thread_tools import TOOL_DEFS as THREAD_TOOL_DEFS
from research.services.thread_tools import TOOL_NAMES as THREAD_TOOL_NAMES
from research.services.thread_tools import call_thread_tool

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2024-11-05"
_SEP = "__"  # namespace separator: <server_slug>__<tool_name>

# DARE-native gateway tools — available to every agent regardless of which MCP
# servers the user connected. No namespace prefix (they're the gateway's own).
# DARE-owned and audited, they run on DARE's API key (not the agent runtime's
# web tooling), so search + read never depend on the runtime's tools or billing.
_RUN_ARG = {
    "type": "string",
    "description": (
        "Your run id exactly as given in your instructions (a Scout's r…, or the "
        "chat's thread id t…), so the call counts against the right budget."
    ),
}

_BUILTIN_TOOL_DEFS = [
    {
        "name": "web_search",
        "description": (
            "Search the web and return a list of result links (title + URL). "
            "Prefer this over any runtime/native web_search tool. Then read the "
            "best results with fetch_page."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query"},
                "run": _RUN_ARG,
            },
            "required": ["query", "run"],
        },
    },
    {
        "name": "search_papers",
        "description": (
            "Search published papers and preprints (OpenAlex: journals, arXiv, "
            "repositories). Returns title, authors, year, venue, citation count, "
            "DOI, a free copy when one exists, and the abstract."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What the papers are about"},
                "run": _RUN_ARG,
            },
            "required": ["query", "run"],
        },
    },
    {
        "name": "fetch_page",
        "description": (
            "Read ONE article/abstract/paper page and return its readable text. "
            "Pass a single 'url' (an article URL, e.g. from web_search results). "
            "Do NOT pass a search-engine results URL (Google/Scholar/DuckDuckGo) "
            "— use web_search to find pages, then fetch_page to read them. A "
            "paywalled DOI is read from its free copy when one exists."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The http(s) URL to read"},
                "offset": {
                    "type": "integer",
                    "description": "Character to start from, to read further into a long page.",
                },
                "run": _RUN_ARG,
            },
            "required": ["url", "run"],
        },
    },
]


def _fetch_with_free_copy(url):
    """Read ``url``; if it's a paywalled paper, read its open-access copy instead."""
    try:
        return fetch_page(url)
    except PageUnreadable:
        doi = doi_in(url)
        try:
            work = work_by_doi(doi) if doi else None
        except ScholarlyError:
            work = None
        free = open_access_url(work) if work else ""
        if not free or free == url:
            raise
        text = fetch_page(free)
        return (
            f"(The original is paywalled; this is the free copy at {free}.)\n\n{text}"
        )


# How much of a page one fetch_page call returns. The full page is still stored
# (and quote-checked); a long page is read in pieces so earlier pages don't
# crowd every later turn of a Scout.
PAGE_PIECE_CHARS = 12_000


def _read_page(user, arguments):
    """One piece of a page; the whole page is read once and kept for later pieces."""
    url = str(arguments.get("url") or "")
    offset = arguments.get("offset") or 0
    offset = offset if isinstance(offset, int) and offset > 0 else 0
    cached = (
        GatewayFetch.all_objects.filter(user=user, tool="fetch_page", url=url, error="")
        .exclude(content="")
        .order_by("-id")
        .first()
        if offset
        else None
    )
    text = cached.content if cached else _fetch_with_free_copy(url)
    piece = text[offset : offset + PAGE_PIECE_CHARS]
    end = offset + len(piece)
    if end < len(text):
        piece += f"\n\n(Page continues: {len(text) - end} more characters. Call fetch_page with offset={end} to read on.)"
    return text, piece


_BUILTIN_HANDLERS = {
    "web_search": lambda user, arguments: web_search(str(arguments.get("query") or "")),
    "search_papers": lambda user, arguments: search_papers(
        str(arguments.get("query") or "")
    ),
}


def _scout_run(arguments):
    """The live Scout run named in ``arguments``, or None."""
    token = arguments.get("run")
    if not token:
        return None
    try:
        return resolve_run_token(token)
    except ThreadTokenInvalid:
        return None


_SEARCH_TOOLS = ("web_search", "search_papers")
# Slack over the brief's limits: the brief asks, this enforces. A model once
# requested 280 parallel searches in a single turn.
_SEARCH_SLACK, _READ_SLACK = 3, 5
# Backstop for calls that name no run (chat's own look-ups, or a Scout that
# forgot its run id): searches per user per minute.
USER_SEARCHES_PER_MINUTE = 30


def _is_search(name):
    """DARE's search tools and every connected tool count against search budgets."""
    return name in _SEARCH_TOOLS or _SEP in name


def _searches(rows):
    return rows.filter(Q(tool__in=_SEARCH_TOOLS) | Q(tool__contains=_SEP))


def _over_budget(user, name, run, run_key):
    """Why this call should not run, or "" when it may."""
    if _is_search(name):
        recent = _searches(
            GatewayFetch.all_objects.filter(
                user=user, created_at__gte=timezone.now() - timedelta(minutes=1)
            )
        ).count()
        if recent >= USER_SEARCHES_PER_MINUTE:
            return "Too many searches in the last minute. Work with the results you already have."
    if run is None:
        return ""
    max_searches, max_reads = scout_limits((run.selected_context or {}).get("depth"))
    rows = GatewayFetch.all_objects.filter(run_key=run_key)
    if _is_search(name) and _searches(rows).count() >= max_searches + _SEARCH_SLACK:
        return (
            f"Search budget for this Scout is used up ({max_searches} searches). "
            "Read and stage the best results you already have, then finish."
        )
    if (
        name == "fetch_page"
        and rows.filter(tool="fetch_page").count() >= max_reads + _READ_SLACK
    ):
        return (
            f"Reading budget for this Scout is used up ({max_reads} pages). "
            "Stage what you have read, then finish."
        )
    return ""


def _connections(user, project):
    """The owner's live connections that ``project`` enabled, by server slug."""
    if project is None:
        return {}
    wanted = {slug.lower() for slug in project.enabled_tools or []}
    connections = UserMCPConnection.all_objects.filter(
        user=user, is_active=True, is_deleted=False
    ).select_related("server")
    return {c.server.slug: c for c in connections if c.server.slug.lower() in wanted}


def _connected_tool_defs(user, project):
    defs = []
    for slug, connection in sorted(_connections(user, project).items()):
        for tool in connection.cached_tools or []:
            if not tool.get("name"):
                continue
            schema = dict(
                tool.get("inputSchema")
                or tool.get("input_schema")
                or {"type": "object", "properties": {}}
            )
            schema["properties"] = {**(schema.get("properties") or {}), "run": _RUN_ARG}
            defs.append(
                {
                    "name": f"{slug}{_SEP}{tool['name']}",
                    "description": (
                        f"[{connection.server.name}] {tool.get('description') or ''}"
                    ).strip(),
                    "inputSchema": schema,
                }
            )
    return defs


def list_user_tools(user, project=None):
    """Every tool the caller's profile is offered."""
    return [
        *_BUILTIN_TOOL_DEFS,
        *THREAD_TOOL_DEFS,
        *_connected_tool_defs(user, project),
    ]


def _tools_fingerprint(user, project):
    names = sorted(tool["name"] for tool in list_user_tools(user, project))
    key = json.dumps([project.id if project else None, names])
    return hashlib.sha256(key.encode()).hexdigest()


_SESSION_TTL = 60 * 60 * 24 * 30
# A new session is announced once: on reconnect Hermes lists the tools but keeps
# its old registry, and only a list_changed notice makes it refresh that.
_PENDING, _ANNOUNCED = "pending", "announced"


def _session_key(session_id):
    return f"mcp-gateway-session:{session_id}"


def open_session():
    session_id = secrets.token_hex(16)
    cache.set(_session_key(session_id), _PENDING, _SESSION_TTL)
    return session_id


def session_known(session_id):
    return cache.get(_session_key(session_id)) is not None


def mark_listed(session_id, user, project):
    """A tools/list answered: after an announcement, the client is up to date."""
    if cache.get(_session_key(session_id)) != _PENDING:
        cache.set(
            _session_key(session_id), _tools_fingerprint(user, project), _SESSION_TTL
        )


def announce_change(session_id, user, project):
    """Whether this reply should carry list_changed; records that it did."""
    state = cache.get(_session_key(session_id))
    if state in (_ANNOUNCED, _tools_fingerprint(user, project)):
        return False
    cache.set(_session_key(session_id), _ANNOUNCED, _SESSION_TTL)
    return True


TOOLS_CHANGED = {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}


def _doi_from_url(url):
    """The DOI in a (dx.)doi.org URL, or empty."""
    for host in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/"):
        if url.startswith(host):
            return url[len(host) :].strip("/")
    return ""


def _capture_fetch(user, tool, content, arguments, run_key="", call_id=""):
    """
    Persist the complete response of one gateway-served call (the agent's
    reading corpus). One row per call, so every call keeps its own run and
    tool_use id: a re-read of the same page (or the next piece of it) used to
    overwrite the earlier row and leave that call unmatched in the run audit.
    Capture must never break the call it records.
    """
    if not content:
        return
    url = str(arguments.get("url") or "")[:1000]
    try:
        GatewayFetch.all_objects.create(
            user=user,
            tool=tool,
            url=url,
            doi=_doi_from_url(url),
            arguments=arguments,
            content=content,
            run_key=run_key,
            call_id=call_id,
        )
    except Exception:  # noqa: BLE001 - capture is best-effort by design
        logger.exception("Gateway fetch capture failed for %s", tool)


def _capture_error(user, tool, error, arguments, run_key="", call_id=""):
    """Persist a gateway call's failure reason so the run audit can show WHY a
    gateway tool failed (paywall / auth / rate-limit), not merely that it did.
    Records the URL too, so the audit names the page that failed. Capture must
    never break the call it records."""
    if not error:
        return
    url = str(arguments.get("url") or "")[:1000]
    try:
        GatewayFetch.all_objects.create(
            user=user,
            tool=tool,
            url=url,
            doi=_doi_from_url(url),
            arguments=arguments,
            content="",
            error=str(error)[:1000],
            run_key=run_key,
            call_id=call_id,
        )
    except Exception:  # noqa: BLE001 - capture is best-effort by design
        logger.exception("Gateway error capture failed for %s", tool)


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


# How much of each paper's abstract to keep in the compacted view. The
# abstract is the source's actual claim — enough to triage, and to ground a
# finding when the full text is paywalled — so it's worth carrying (~1k tokens
# for ten papers) rather than dropping it and forcing a fetch to recover it.
_ABSTRACT_CHARS = 700


def _compact_scholarly_hits(text):
    """
    Compact a scholarly search result (Scite-style JSON with `hits`) into a few
    lines per paper — title, authors, year, venue, DOI, citation tallies, and a
    trimmed abstract. The raw JSON buries ~1 paper in bloat per 4k chars;
    compacted, ten papers (with their DOIs and abstracts) fit in a fraction of
    that. Returns None when the text isn't that shape.
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
        line = (
            f"- {hit.get('title', '?')} ({names}, {hit.get('year', '?')}) "
            f"| {hit.get('journal', '?')}"
            + (f" | DOI: {doi} -> https://doi.org/{doi}" if doi else "")
            + cites
        )
        abstract = (hit.get("abstract") or "").strip()
        if abstract:
            line += f"\n  abstract: {abstract[:_ABSTRACT_CHARS]}"
        lines.append(line)
    return "\n".join(lines) if lines else None


def _result(rpc_id, result):
    return {"jsonrpc": "2.0", "id": rpc_id, "result": result}


def _error(rpc_id, code, message):
    return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}


def _handle_tool_call(user, rpc_id, params, run_key="", project=None):
    """Route one tools/call to a gateway builtin or the user's tool executor."""
    name = params.get("name", "")
    arguments = params.get("arguments") or {}
    # Per-call run attribution: the MCP standard `_meta` field is the only thing
    # that varies per tool call on a shared connection (a header can't), so the
    # runtime forwards the run/session id there. Prefer it; fall back to the
    # header-derived run_key, then to the time-window match downstream.
    meta = params.get("_meta") or {}
    scout = _scout_run(arguments)
    run_key = (
        (scout.runtime_session_id if scout else "")
        or str(meta.get("dareRun") or "")
        or run_key
    )
    call_id = str(meta.get("dareCall") or "")

    if name in _BUILTIN_HANDLERS or name == "fetch_page":
        refusal = _over_budget(user, name, scout, run_key)
        if refusal:
            # Not isError: three errors would trip the runtime's breaker for
            # every DARE tool.
            return _result(rpc_id, {"content": [{"type": "text", "text": refusal}]})
        try:
            if name == "fetch_page":
                text, reply = _read_page(user, arguments)
            else:
                text = reply = _BUILTIN_HANDLERS[name](user, arguments)
        except (PageUnreadable, ScholarlyError) as exc:
            # A specific page couldn't be read (paywall / 404 / login wall). That
            # is a normal fetch OUTCOME, not a tool failure — return it WITHOUT
            # isError so the agent moves to another source and the runtime's
            # per-server circuit breaker doesn't count healthy per-page misses as
            # an outage. The reason is still captured for the run audit.
            logger.info("MCP gateway builtin %s: %s", name, exc)
            _capture_error(user, name, str(exc), arguments, run_key, call_id)
            return _result(rpc_id, {"content": [{"type": "text", "text": str(exc)}]})
        except Exception as exc:  # noqa: BLE001 - genuine tool/system failure
            # A real malfunction (no API key, upstream API error, bad input) —
            # surface as isError so the audit is honest and the breaker can react.
            logger.info("MCP gateway builtin %s failed: %s", name, exc)
            _capture_error(user, name, str(exc), arguments, run_key, call_id)
            return _result(
                rpc_id,
                {"content": [{"type": "text", "text": str(exc)}], "isError": True},
            )
        _capture_fetch(user, name, text, arguments, run_key, call_id)
        return _result(rpc_id, {"content": [{"type": "text", "text": reply}]})

    if name in THREAD_TOOL_NAMES:
        text, is_error = call_thread_tool(name, arguments)
        _capture_fetch(user, name, text, arguments, run_key, call_id)
        result = {"content": [{"type": "text", "text": text}]}
        if is_error:
            result["isError"] = True
        return _result(rpc_id, result)

    if _SEP not in name:
        return _error(rpc_id, -32602, f"Unknown tool: {name!r}")
    text = _call_connected_tool(user, project, name, arguments, run_key, call_id)
    return _result(rpc_id, {"content": [{"type": "text", "text": text}]})


def _call_connected_tool(user, project, name, arguments, run_key, call_id):
    """Run a connected server's tool with the owner's credentials.

    Failures come back as text, not MCP errors: three errors would trip the
    runtime's breaker for every DARE tool.
    """
    slug, tool_name = name.split(_SEP, 1)
    if slug not in _connections(user, project):
        error = f"{name} is not connected for this project."
        _capture_error(user, name, error, arguments, run_key, call_id)
        return f"Not done: {error}"
    if refusal := _over_budget(user, name, _scout_run(arguments), run_key):
        return refusal
    forwarded = {k: v for k, v in arguments.items() if k != "run"}
    try:
        result = async_to_sync(mcp_tool_executor.execute_tool_call)(
            user, slug, tool_name, forwarded
        )
    except MCPToolExecutorError as exc:
        logger.info("Connected tool %s failed: %s", name, exc)
        _capture_error(user, name, str(exc), arguments, run_key, call_id)
        return f"Not done: {name} failed ({exc})."
    text = _result_text(result).strip()
    if isinstance(result, dict) and result.get("isError"):
        _capture_error(user, name, text or "Tool error", arguments, run_key, call_id)
        return f"Not done: {name} reported an error: {text[:500]}"
    _capture_fetch(user, name, text, arguments, run_key, call_id)
    return _compact_scholarly_hits(text) or text


def handle_jsonrpc(user, payload, run_key="", project=None):
    """
    Handle one MCP JSON-RPC message. Returns the response dict, or None for
    notifications (which take no response). `run_key` is the caller's run/session
    identifier (when the agent runtime forwards it), stamped on captured rows so
    the run audit can attribute each call to its exact run.
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
    if method == "ping":
        return _result(rpc_id, {})
    if method == "tools/list":
        return _result(rpc_id, {"tools": list_user_tools(user, project)})
    if method == "tools/call":
        return _handle_tool_call(
            user, rpc_id, payload.get("params") or {}, run_key, project
        )
    return _error(rpc_id, -32601, f"Method not found: {method}")
