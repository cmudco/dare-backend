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
    user, query: str, limit: int = DEFAULT_LIMIT
) -> Dict[str, Any]:
    """Search one user's transcript; returns the executor-shaped result dict.

    Scope comes from the server-side user object, never from model arguments —
    a hallucinated argument must not be able to widen the search.
    """
    if user is None:
        return {
            "success": False,
            "error": "Memory is unavailable for this conversation.",
        }

    terms = tokenize(query or "")
    if not terms:
        return {"success": True, "query": query, "found": 0, "transcript": ""}

    try:
        hits = _search(user, terms, limit)
        blocks = [_render_hit(hit) for hit in hits]
    except Exception as exc:
        logger.exception("[memory] search_sessions failed for user %s", user.id)
        return {"success": False, "error": f"Transcript search failed: {exc}"}

    _log_search(user, query, len(hits))

    return {
        "success": True,
        "query": query,
        "found": len(hits),
        "transcript": "\n\n".join(blocks),
    }


def _base_queryset(user):
    return Message.active_objects.filter(
        conversation__user=user,
        sender_type__in=[SenderType.PLAYER, SenderType.AI_ASSISTANT],
    ).exclude(message="")


def _search(user, terms: List[str], limit: int) -> List[Dict[str, Any]]:
    if connection.vendor == "postgresql":
        matched = _search_postgres(user, terms, limit)
    else:
        matched = _search_fallback(user, terms, limit)

    return [
        {
            "message": message,
            "before": _neighbor(message, older=True),
            "after": _neighbor(message, older=False),
        }
        for message in matched
    ]


def _search_postgres(user, terms: List[str], limit: int) -> List[Message]:
    """FTS with prefix-or terms, ranked.

    The tsvector expression matches memory/migrations/0004's GIN index over
    ``conversations_message (message)`` byte-for-byte; terms come from
    tokenize(), which strips to [a-z0-9] and is therefore tsquery-safe.
    """
    tsquery = " | ".join(f"{term}:*" for term in terms)
    queryset = (
        _base_queryset(user)
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


def _search_fallback(user, terms: List[str], limit: int) -> List[Message]:
    """SQLite local dev: LIKE over the body, newest first."""
    condition = Q()
    for term in terms:
        condition |= Q(message__icontains=term)
    return list(_base_queryset(user).filter(condition).order_by("-created_at")[:limit])


def _neighbor(message: Message, older: bool) -> Optional[Message]:
    """The adjacent turn, by pk order within the conversation.

    A matched line alone is usually unreadable — "yeah let's do that" needs
    its question. DARE messages have no sequence column; ids are allocated in
    insert order, which is turn order within a conversation.
    """
    queryset = Message.active_objects.filter(conversation_id=message.conversation_id)
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
