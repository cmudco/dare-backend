"""The transcript layer: word-for-word search over past conversations.

There is no memory table behind this — the episodic record IS the existing
``conversations.Message`` rows, kept verbatim by the chat pipeline. Keyword
search, deliberately not semantic: this is the layer where you want the exact
phrasing back ("what did we decide about the schema in March?"), not
something close to it.

Exposed to the model as the ``search_sessions`` tool, not prefetched. The
rule the whole read path follows: prefetch what the model CANNOT know it
needs (an allergy behind "book me somewhere nice"); give it a tool for what
it can ("what did we decide" is obviously a lookup).

Every execution appends a ledger row — reads share the audit timeline with
writes.
"""

import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from django.db import connection
from django.db.models import Q

from conversations.constants import SenderType
from conversations.models import Message
from memory.constants import WriterAction
from memory.models import MemoryLedgerEntry
from memory.services.store import tokenize

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 6
# A single monster message must not eat the whole tool result.
SNIPPET_CHARS = 700


def search_sessions_for_user(
    user,
    query: str = "",
    limit: int = DEFAULT_LIMIT,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> Dict[str, Any]:
    """Search one user's transcript; returns the executor-shaped result dict.

    Scope comes from the server-side user object, never from model arguments —
    a hallucinated argument must not be able to widen the search. The date
    bounds only ever NARROW, so the same holds for them.

    Before these existed, "what did we discuss last week" could only be asked
    by searching for the words "last week", which matched whenever someone had
    typed that phrase and never the week itself.
    """
    if user is None:
        return {
            "success": False,
            "error": "Memory is unavailable for this conversation.",
        }

    try:
        start, end = parse_bounds(since, until)
    except BadBounds as exc:
        return {"success": False, "error": str(exc)}
    terms = tokenize(query or "")
    if not terms and start is None and end is None:
        # An empty call is a mistake worth correcting, not an empty result:
        # answered with found=0, the model concluded "we never talked" and
        # said so. Told what the tool needs, it retries properly.
        return {
            "success": False,
            "error": (
                "Nothing to search: give keywords, a date range "
                "(since/until as YYYY-MM-DD), or both."
            ),
        }

    try:
        hits = _search(user, terms, limit, start, end)
        blocks = [_render_hit(hit) for hit in hits]
    except Exception as exc:
        logger.exception("[memory] search_sessions failed for user %s", user.id)
        return {"success": False, "error": f"Transcript search failed: {exc}"}

    _log_search(user, query, len(hits))

    return {
        "success": True,
        "query": query,
        "since": start.isoformat() if start else None,
        "until": end.isoformat() if end else None,
        "found": len(hits),
        "transcript": "\n\n".join(blocks),
    }


def _base_queryset(user, since: Optional[date] = None, until: Optional[date] = None):
    queryset = (
        Message.active_objects.filter(
            conversation__user=user,
            # Deleting a conversation has to mean the transcript stops answering
            # for it. Message rows are not touched when a conversation is
            # deleted, so filtering only on the message flags leaves every word
            # of a deleted conversation searchable — which reads to the person as
            # the delete not having worked.
            conversation__is_deleted=False,
            conversation__is_active=True,
            sender_type__in=[SenderType.PLAYER, SenderType.AI_ASSISTANT],
        )
        .select_related("conversation")
        .exclude(message="")
    )
    if since is not None:
        queryset = queryset.filter(created_at__date__gte=since)
    if until is not None:
        queryset = queryset.filter(created_at__date__lte=until)
    return queryset


def parse_day(value: Optional[str]) -> Optional[date]:
    """YYYY-MM-DD, or nothing — or a readable refusal.

    The first version dropped a malformed bound and searched on. Red-teamed,
    that read as a lie: ?since=not-a-date returned HTTP 200 with the whole
    history, presenting an unbounded search as the bounded one that was
    asked for. In a privacy-adjacent search, a typo must fail loudly."""
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        raise BadBounds(
            f"Unrecognized date {str(value)[:24]!r} — dates are YYYY-MM-DD."
        )


def parse_bounds(
    since: Optional[str], until: Optional[str]
) -> "tuple[Optional[date], Optional[date]]":
    start, end = parse_day(since), parse_day(until)
    if start is not None and end is not None and start > end:
        raise BadBounds(
            f"The range is reversed: since={start.isoformat()} is after "
            f"until={end.isoformat()}."
        )
    return start, end


class BadBounds(ValueError):
    """A date bound that must stop the search, in words a person can read."""


def _search(
    user,
    terms: List[str],
    limit: int,
    since: Optional[date] = None,
    until: Optional[date] = None,
) -> List[Dict[str, Any]]:
    if not terms:
        # A date range with no words is a real search: "what did we talk about
        # last Tuesday" has nothing to match on but is perfectly answerable.
        matched = list(
            _base_queryset(user, since, until).order_by("-created_at")[:limit]
        )
    elif connection.vendor == "postgresql":
        matched = _search_postgres(user, terms, limit, since, until)
    else:
        matched = _search_fallback(user, terms, limit, since, until)

    # A hit is shown with the turn either side of it, so two matches one apart
    # — which is the common case, since the person says a thing and the reply
    # quotes it back — render two windows over almost the same three messages.
    # Read back, the same exchange appears twice and looks like it happened
    # twice. A message already inside an earlier window is dropped: it is not
    # a second answer, it is the same one.
    shown: set = set()
    hits: List[Dict[str, Any]] = []
    for message in matched:
        if message.id in shown:
            continue
        before = _neighbor(message, True, since, until)
        after = _neighbor(message, False, since, until)
        hits.append({"message": message, "before": before, "after": after})
        shown.update(item.id for item in (before, message, after) if item is not None)
    return hits


def _search_postgres(
    user,
    terms: List[str],
    limit: int,
    since: Optional[date] = None,
    until: Optional[date] = None,
) -> List[Message]:
    """FTS with prefix-or terms, ranked.

    The tsvector expression matches memory/migrations/0004's GIN index over
    ``conversations_message (message)`` byte-for-byte; terms come from
    tokenize(), which strips to [a-z0-9] and is therefore tsquery-safe.
    """
    tsquery = " | ".join(f"{term}:*" for term in terms)
    queryset = (
        _base_queryset(user, since, until)
        .extra(
            select={
                "fts_rank": (
                    "ts_rank(to_tsvector('english', message), "
                    "to_tsquery('english', %s))"
                )
            },
            select_params=[tsquery],
            where=["to_tsvector('english', message) @@ to_tsquery('english', %s)"],
            params=[tsquery],
        )
        .order_by("-fts_rank")[:limit]
    )
    return list(queryset)


def _search_fallback(
    user,
    terms: List[str],
    limit: int,
    since: Optional[date] = None,
    until: Optional[date] = None,
) -> List[Message]:
    """SQLite local dev: LIKE over the body, newest first."""
    condition = Q()
    for term in terms:
        condition |= Q(message__icontains=term)
    return list(
        _base_queryset(user, since, until)
        .filter(condition)
        .order_by("-created_at")[:limit]
    )


def search_sessions_hits(
    user,
    query: str = "",
    limit: int = DEFAULT_LIMIT,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> Dict[str, Any]:
    """The same search, shaped for a page instead of a prompt.

    The tool flattens hits into one transcript block because a model reads
    text; the Memory page needs each hit as a thing that can be clicked —
    which conversation, what was said around it, and where to go. Same
    matching, same windows, same scoping; only the shape differs.
    """
    if user is None:
        return {"success": False, "error": "Memory is unavailable."}

    try:
        start, end = parse_bounds(since, until)
    except BadBounds as exc:
        return {"success": False, "error": str(exc), "bad_request": True}
    terms = tokenize(query or "")
    if not terms and start is None and end is None:
        return {"success": True, "query": query, "found": 0, "hits": []}

    try:
        raw = _search(user, terms, limit, start, end)
    except Exception as exc:
        logger.exception("[memory] session hits failed for user %s", user.id)
        return {"success": False, "error": f"Transcript search failed: {exc}"}

    _log_search(user, query or f"{since or ''}..{until or ''}", len(raw))

    hits = []
    for hit in raw:
        message = hit["message"]
        conversation = message.conversation
        hits.append(
            {
                "conversation_id": conversation.conversation_id,
                "conversation_title": conversation.title or "Untitled conversation",
                "message_id": message.id,
                "date": (
                    message.created_at.date().isoformat()
                    if message.created_at
                    else None
                ),
                "exchange": [
                    {
                        "role": _role(neighbor),
                        "text": neighbor.message[:SNIPPET_CHARS],
                        "matched": neighbor.id == message.id,
                    }
                    for neighbor in (hit["before"], message, hit["after"])
                    if neighbor is not None and neighbor.message
                ],
            }
        )

    return {
        "success": True,
        "query": query,
        "since": start.isoformat() if start else None,
        "until": end.isoformat() if end else None,
        "found": len(hits),
        "hits": hits,
    }


def _neighbor(
    message: Message,
    older: bool,
    since: Optional[date] = None,
    until: Optional[date] = None,
) -> Optional[Message]:
    """The adjacent turn, by pk order within the conversation.

    A matched line alone is usually unreadable — "yeah let's do that" needs
    its question. DARE messages have no sequence column; ids are allocated in
    insert order, which is turn order within a conversation.

    Neighbours obey the date bounds too. The block is rendered under ONE date
    header, so a neighbour from outside the window is presented as though it
    happened inside it — asking about last week and being shown something from
    six weeks ago, dated last week.
    """
    queryset = Message.active_objects.filter(conversation_id=message.conversation_id)
    if since is not None:
        queryset = queryset.filter(created_at__date__gte=since)
    if until is not None:
        queryset = queryset.filter(created_at__date__lte=until)
    if older:
        return queryset.filter(id__lt=message.id).order_by("-id").first()
    return queryset.filter(id__gt=message.id).order_by("id").first()


def _role(message: Message) -> str:
    return "user" if message.sender_type == SenderType.PLAYER else "assistant"


def _render_hit(hit: Dict[str, Any]) -> str:
    message = hit["message"]
    lines = [
        f"{_role(neighbor)}: {neighbor.message[:SNIPPET_CHARS]}"
        for neighbor in (hit["before"], message, hit["after"])
        if neighbor is not None and neighbor.message
    ]
    stamp = message.created_at.date().isoformat() if message.created_at else "unknown"
    return f"[{stamp}]\n" + "\n".join(lines)


def _log_search(user, query: str, found: int) -> None:
    try:
        MemoryLedgerEntry.objects.create(
            user=user,
            action=WriterAction.SEARCH_SESSIONS,
            proposed_action=WriterAction.SEARCH_SESSIONS,
            reason=f'The model searched the transcript for "{query[:200]}".',
            note=None if found else "Nothing in the transcript matched those words.",
            applied=found > 0,
            detail=f"{query[:200]} — {found} hit{'s' if found != 1 else ''}",
            source_text=query[:400],
        )
    except Exception:
        # A failed audit line must not fail the search itself.
        logger.exception("[memory] failed to log search_sessions for user %s", user.id)
