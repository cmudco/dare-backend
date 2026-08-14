"""Persist and broadcast the post-reply writer result."""

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
    """Push the writer verdict into the conversation room."""
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
    """Format applied writes and refusals for the memory chip."""
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
