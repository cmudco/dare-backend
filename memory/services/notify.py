"""Telling the conversation what the writer just did.

The writer runs after the reply is finished, on a different process, so what
it decides can never be a tool call inside the turn — by the time it knows
anything the turn is closed. That is a real constraint, not an oversight: the
alternative is making every reply wait on an extraction it does not need.

So it reports afterwards instead. Socket.IO's Redis manager already carries
messages between processes for horizontal scaling, and a write-only manager
joins that bus without running a server, which is exactly what a worker needs.

Failure here is deliberately silent to the caller: the memory was written and
committed before this runs, and a dropped notification must never turn a
successful ingest into a failed job.
"""

import logging
from typing import Any, Dict, List, Optional

import socketio

from conversations.socket_server import redis_url

logger = logging.getLogger(__name__)

_client: Optional[socketio.RedisManager] = None


def _emitter() -> socketio.RedisManager:
    """One write-only manager per worker process, made on first use."""
    global _client
    if _client is None:
        _client = socketio.RedisManager(redis_url, write_only=True)
    return _client


def announce_write(conversation_id, message_id, summary: Dict[str, Any]) -> None:
    """Push the writer's verdict into the conversation's room.

    Sent inside the ordinary ``message`` envelope with a ``type``, which is the
    convention every other server event here follows — the client dispatches on
    the type and needs no new listener.
    """
    try:
        _emitter().emit(
            "message",
            {
                "type": "memory_written",
                "conversationId": str(conversation_id),
                "messageId": message_id,
                **summary,
            },
            room=f"conversation_{conversation_id}",
            namespace="/chat",
        )
    except Exception:
        logger.warning(
            "[memory] could not announce the write for conversation %s",
            conversation_id,
            exc_info=True,
        )


def summarize_report(report, ledger_entries: List[Any]) -> Dict[str, Any]:
    """What the panel shows: what changed, and the reasons in the writer's words.

    Refusals travel too. A turn where the gate declined to store something is
    the most informative thing this feature can show, and hiding it would make
    the panel a highlight reel of a system whose whole claim is that it can be
    audited.
    """
    changes: List[Dict[str, Any]] = []
    for entry in ledger_entries:
        changes.append(
            {
                "action": entry.action,
                "proposedAction": entry.proposed_action,
                "applied": entry.applied,
                "reason": entry.reason,
                "note": entry.note,
                "detail": entry.detail,
                "recordId": str(entry.record_id) if entry.record_id else None,
            }
        )

    return {
        "created": len(report.created),
        "retired": report.retired,
        "reinforced": report.reinforced,
        "profileChanged": report.profile_changed,
        "considered": report.decisions,
        "changes": changes,
    }
