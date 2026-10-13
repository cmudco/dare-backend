"""
Audit rows for the tool calls an agent run makes.

Gateway calls are recorded twice: as a streamed tool event (names the tool,
omits the result) and as a GatewayFetch row the gateway writes (the result or
failure reason). Joining the two is what lets the Runs view show the URL, the
body and the real reason a page failed.
"""

import logging

from mcp.models import GatewayFetch
from research.constants import AgentToolCallStatus
from research.models import ResearchAgentToolCall

logger = logging.getLogger(__name__)

# Hermes namespaces MCP tools as `<prefix><server>_<tool>`. It used a single
# underscore until v0.19, which switched to the `mcp__<server>__<tool>` form
# shared with Claude Code/Codex (tools/mcp_tool.py MCP_TOOL_NAME_PREFIX). Both
# are accepted so the audit keeps working across runtime versions — matching the
# wrong one silently drops every GatewayFetch row (no URL, no content, no
# failure reason) rather than raising.
_GATEWAY_PREFIXES = ("mcp__dare__", "mcp_dare_")
_encoder = None


def _gateway_tool_base(tool):
    """The bare gateway tool name (`web_search`), or None if not a gateway call."""
    for prefix in _GATEWAY_PREFIXES:
        if tool.startswith(prefix):
            return tool[len(prefix) :]
    return None


def token_count(text):
    """Token size of `text` (cl100k_base) — the per-call context contribution.
    Best-effort: falls back to a chars/4 estimate if the tokenizer is absent."""
    global _encoder
    if not text:
        return 0
    if _encoder is None:
        try:
            import tiktoken

            _encoder = tiktoken.get_encoding("cl100k_base")
        except Exception:  # noqa: BLE001 - estimate rather than fail the run
            _encoder = False
    if _encoder:
        return len(_encoder.encode(text))
    return len(text) // 4


def run_session_key(run):
    """The Hermes session id DARE assigned this run (see `_start_run`) — the
    value the runtime forwards as X-DARE-Run-Session so gateway rows can be
    attributed to this exact run."""
    return f"{run.session.hermes_session_id}-r{run.id}"


def _next_gateway_fetch(run, tool, seen_ids, call_id=""):
    """The GatewayFetch row this streamed gateway call produced, consumed once.

    One row is written per gateway call (its content on success, the reason on a
    page miss). We link a streamed tool event to its row by, in order:
      1. exact tool_use-id match — the stream event and the gateway's `_meta`
         carry the SAME id, so this is an exact, order- and concurrency-proof
         join (no guessing);
      2. else exact run-key match — when only the run/session id is forwarded;
      3. else the earliest unconsumed *unattributed* row for this tool in the
         run window, tracked via `seen_ids` so N calls map to N rows in order.

    The old code always took the most-recent matching row, which mislabelled
    calls that shared a time window (and, when a call wrote no row at all,
    borrowed a different call's reason — the "same URL ×N" audit artifact).
    Returns None for tools that never touch the gateway."""
    base = _gateway_tool_base(tool)
    if base is None:
        return None
    row = None
    if call_id:
        # tool_use ids are globally unique, so match them WITHOUT the time
        # window — a re-fetched URL de-dups onto an existing (older) row, which
        # a windowed search would miss.
        row = (
            GatewayFetch.all_objects.filter(tool=base, call_id=call_id)
            .exclude(id__in=seen_ids)
            .order_by("created_at")
            .first()
        )
        if row is None:
            # The runtime named an id and we still could not match it. Ordering
            # takes over from here, which is a guess — and a silent guess is how
            # a whole run's audit ended up blank once already. Say it out loud.
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
            window.filter(run_key=run_session_key(run)).order_by("created_at").first()
            or window.filter(run_key="").order_by("created_at").first()
        )
    if row is not None:
        seen_ids.add(row.id)
    return row


def record_tool_call(run, event, arguments, seen_ids):
    """Persist one streamed tool call for the Runs audit view and return it.

    For gateway calls the result body and the real failure reason live in the
    GatewayFetch corpus (the event stream names the tool but omits its result),
    so the captured row is the source of truth — not the stream's error flag. A
    page miss (paywall/404) is now returned to the agent WITHOUT isError so it
    doesn't trip the circuit breaker, so the stream would call it a success;
    reading the row keeps the audit honest (the row carries the reason)."""
    duration = event.get("duration")
    tool = event.get("tool", "")
    raw_error = event.get("error")
    error_text = raw_error if isinstance(raw_error, str) else ""
    result_summary = ""

    row = _next_gateway_fetch(run, tool, seen_ids, event.get("toolCallId") or "")
    if row is None and _gateway_tool_base(tool) is not None:
        # A gateway tool that produced no row means the audit lost this call's
        # URL and body entirely. It has failed silently twice now (once on a
        # tool-name rename), so say so rather than persist a blank row.
        logger.warning(
            "research.audit run %s: no GatewayFetch row for %s "
            "(toolCallId=%r, run_key=%r) — url and result will be blank",
            run.id,
            tool,
            event.get("toolCallId") or "",
            run_session_key(run),
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
