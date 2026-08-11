"""Injecting memory context into LLM messages.

The read-path seam: build_standard_messages calls this once per turn, gated
on ``request.context.use_memory`` and an authenticated user. The three memory
layers (USER.md, retrieved facts, standing procedures) arrive as ONE appended
user-role message with per-layer framing; the return value (``{content,
memory_type, categories}`` per item) feeds ``Message.memory_context_data`` and
the frontend's per-message memory panel, and its shape must not change.

Latency: two DB round-trips plus one 512-dim embedding call (~tens of ms),
run off the event loop. Any failure degrades to "no memory context" — a
broken memory read must never take a conversation down.
"""

import logging
from typing import Any, Dict, List

from channels.db import database_sync_to_async

from config.env import USE_POSTGRES

logger = logging.getLogger(__name__)


async def add_memory_context_to_messages(
    messages: List[Dict[str, str]],
    query: str,
    user_id: int,
) -> List[Dict[str, Any]]:
    """
    Read the user's memory layers and append the framed context block.

    Args:
        messages: LLM message list to append to (modified in place)
        query: The user's message text used as the retrieval query
        user_id: Authenticated user's integer ID

    Returns:
        List of memory item dicts the prompt actually carried (for display on
        the frontend). Each dict has: content, memory_type, categories.
    """
    if not query or not query.strip():
        return []

    if not USE_POSTGRES:
        logger.debug("Memory context injection skipped: USE_POSTGRES is False")
        return []

    try:
        context = await database_sync_to_async(_read_context_for_user)(
            user_id, query.strip()
        )
    except Exception:
        logger.exception(
            "Memory context read failed for user %s; injecting nothing", user_id
        )
        return []

    if context is None or not context.block:
        return []

    messages.append({"role": "user", "content": context.block})
    return context.items


def _read_context_for_user(user_id: int, query: str):
    from django.contrib.auth import get_user_model

    from memory.services.context import read_context

    user = get_user_model().objects.filter(pk=user_id).first()
    if user is None:
        return None
    return read_context(user, query)
