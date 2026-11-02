"""
Audit rows for the tool calls an agent run makes.

Gateway calls are recorded twice: as a streamed tool event (names the tool,
omits the result) and as a GatewayFetch row the gateway writes (the result or
failure reason). Joining the two is what lets the Runs view show the URL, the
body and the real reason a page failed.
"""

import logging

import tiktoken

from mcp.models import GatewayFetch
from research.constants import AgentToolCallStatus
from research.models import ResearchAgentToolCall

logger = logging.getLogger(__name__)

# Hermes names MCP tools `mcp__<server>__<tool>`.
_GATEWAY_PREFIX = "mcp__dare__"
_encoder = None


def _gateway_tool_base(tool):
    """The bare gateway tool name (`web_search`), or None if not a gateway call."""
    return (
        tool.removeprefix(_GATEWAY_PREFIX) if tool.startswith(_GATEWAY_PREFIX) else None
    )


def token_count(text):
    """Token size of ``text`` (cl100k_base): what a call added to the context."""
    global _encoder
    if not text:
        return 0
    if _encoder is None:
        _encoder = tiktoken.get_encoding("cl100k_base")
    return len(_encoder.encode(text))


def _next_gateway_fetch(run, tool, seen_ids, call_id=""):
    """The GatewayFetch row this streamed gateway call wrote, claimed once.

    Matched by the tool-call id both sides carry when the runtime sends it;
    otherwise by the run's session key, then the earliest unclaimed row for the
    tool since the run started. ``seen_ids`` keeps N calls on N rows. Returns
    None for tools that never touch the gateway.
    """
    base = _gateway_tool_base(tool)
    if base is None:
        return None
    row = None
    if call_id:
        row = (
            GatewayFetch.all_objects.filter(tool=base, call_id=call_id)
            .exclude(id__in=seen_ids)
            .order_by("created_at")
            .first()
        )
        if row is None:
            # Ordering is a guess from here; say so in the logs.
            logger.warning(
                "research.audit run %s: %s carried toolCallId=%r with no "
                "matching GatewayFetch row — falling back to positional order",
                run.id,
                tool,
                call_id,
            )
    if row is None:
        window = GatewayFetch.all_objects.filter(
            tool=base, created_at__gte=run.started_at
        ).exclude(id__in=seen_ids)
        row = (
            window.filter(run_key=run.runtime_session_id).order_by("created_at").first()
            or window.filter(run_key="").order_by("created_at").first()
        )
    if row is not None:
        seen_ids.add(row.id)
    return row


def record_tool_call(run, event, arguments, seen_ids):
    """Persist one streamed tool call for the Runs audit and return it.

    For gateway calls the gateway's own row is the source of truth for the real
    arguments, the result and the failure reason: the stream names the tool but
    omits the result, and a page miss reaches the agent as a normal reply.
    """
    duration = event.get("duration")
    tool = event.get("tool", "")
    raw_error = event.get("error")
    error_text = raw_error if isinstance(raw_error, str) else ""
    result_summary = ""

    row = _next_gateway_fetch(run, tool, seen_ids, event.get("toolCallId") or "")
    if row is None and _gateway_tool_base(tool) is not None:
        logger.warning(
            "research.audit run %s: no GatewayFetch row for %s "
            "(toolCallId=%r, run_key=%r) — url and result will be blank",
            run.id,
            tool,
            event.get("toolCallId") or "",
            run.runtime_session_id,
        )
    if row is not None:
        # The gateway saw the real arguments; the stream only has a preview.
        real = {
            k: v for k, v in (row.arguments or {}).items() if k not in ("run", "thread")
        }
        arguments = {**arguments, **real}
        if row.url:
            arguments = {**arguments, "url": row.url}
        if row.error:
            error_text = row.error
        else:
            result_summary = row.content

    # Error if the captured row (or, for non-gateway tools, the stream) says so.
    is_error = bool(error_text) or (bool(raw_error) and row is None)
    return ResearchAgentToolCall.objects.create(
        run=run,
        tool=tool,
        arguments=arguments,
        status=AgentToolCallStatus.ERROR if is_error else AgentToolCallStatus.SUCCESS,
        result_summary=result_summary,
        duration_ms=int(duration * 1000) if duration else None,
        result_tokens=token_count(result_summary),
        error=error_text,
    )
