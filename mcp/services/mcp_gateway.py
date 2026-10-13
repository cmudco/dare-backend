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
from datetime import timedelta

from asgiref.sync import async_to_sync
from django.utils import timezone

from mcp.models import GatewayFetch, UserMCPConnection
from mcp.services.mcp_tool_executor import mcp_tool_executor
from mcp.services.scholarly import (
    ScholarlyError,
    doi_in,
    open_access_url,
    search_papers,
    work_by_doi,
)
from mcp.services.web_fetch import PageUnreadable, fetch_page, web_search
from research.services.audit_service import run_session_key
from research.services.scout_service import scout_limits
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
    "description": "Scouts: your run id exactly as given in your instructions, so the call is logged on your run.",
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
            "required": ["query"],
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
            "required": ["query"],
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
            "required": ["url"],
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


def _over_budget(user, name, run, run_key):
    """Why this call should not run, or "" when it may."""
    if name in _SEARCH_TOOLS:
        recent = GatewayFetch.all_objects.filter(
            user=user,
            tool__in=_SEARCH_TOOLS,
            created_at__gte=timezone.now() - timedelta(minutes=1),
        ).count()
        if recent >= USER_SEARCHES_PER_MINUTE:
            return "Too many searches in the last minute. Work with the results you already have."
    if run is None:
        return ""
    max_searches, max_reads = scout_limits((run.selected_context or {}).get("depth"))
    rows = GatewayFetch.all_objects.filter(run_key=run_key)
    if (
        name in _SEARCH_TOOLS
        and rows.filter(tool__in=_SEARCH_TOOLS).count() >= max_searches + _SEARCH_SLACK
    ):
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


def list_user_tools(user):
    """Tool definitions exposed to a delegated agent over the live gateway.

    Tenant isolation (Path B): a live gateway call carries no project/run/owner
    identity — Hermes forwards none — so the gateway cannot safely decide whose
    credentials a `<server>__<tool>` call should use. It would fall back to the
    shared service-user's credentials, leaking one tenant's tools to another.

    So the live gateway exposes ONLY DARE-owned, credential-free builtins
    (fetch_page). User-credentialed research tools (Consensus, Scite, Scholar)
    are never exposed here; DARE runs them server-side under the project owner
    (`gather_tool_results(run.user, …)`) and injects their results into the run.

    Research-thread tools ride along: they carry their own authority (a signed
    thread token in the arguments), so they are safe on a shared connection.

    `user` is accepted for signature stability but intentionally unused — the
    live surface is identity-independent by design.
    """
    return list(_BUILTIN_TOOL_DEFS) + list(THREAD_TOOL_DEFS)


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
    mcp_dare_* tool failed (paywall / auth / rate-limit), not merely that it did.
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
    _capture_fetch(user, f"{slug}{_SEP}{tool_name}", text, {param: query})
    compact = _compact_scholarly_hits(text)
    return entry(text=(compact or text)[:per_tool_chars], raw=text)


def gather_tool_results(user, slugs, query, per_tool_chars=12000):
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


def gather_tool_context(user, slugs, query, per_tool_chars=12000):
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


def _handle_tool_call(user, rpc_id, params, run_key=""):
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
        (run_session_key(scout) if scout else "")
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

    # Tenant isolation (Path B): a namespaced, user-credentialed tool reached
    # the live gateway, which has no identity to resolve the owner. Refuse it
    # rather than fall back to the shared service-user's credentials (a
    # cross-tenant leak). DARE runs these server-side under the project owner
    # and provides the results in the run input. Returned as isError so the
    # agent reads the reason and continues, and the audit records an honest
    # refusal — never a false success.
    logger.info("MCP gateway refused credentialed tool %s (server-side only)", name)
    _capture_error(
        user,
        name,
        "credentialed tool not available on the live gateway",
        arguments,
        run_key,
        call_id,
    )
    return _result(
        rpc_id,
        {
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"{name} cannot be called here. The user's "
                        "research-tool results are already in your input — use "
                        "those, and read pages with fetch_page."
                    ),
                }
            ],
            "isError": True,
        },
    )


def handle_jsonrpc(user, payload, run_key=""):
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
    if method == "tools/list":
        return _result(rpc_id, {"tools": list_user_tools(user)})
    if method == "tools/call":
        return _handle_tool_call(user, rpc_id, payload.get("params") or {}, run_key)
    return _error(rpc_id, -32601, f"Method not found: {method}")
