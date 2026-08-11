"""Injecting memory context into LLM messages.

This is the read-path seam: build_standard_messages calls this once per turn,
gated on ``request.context.use_memory`` and an authenticated user. The return
shape (``{content, memory_type, categories}`` per item) feeds
``Message.memory_context_data`` and the frontend's per-message memory panel,
so it must not change.

Wired to the layered store's read path (USER.md + facts + rules) in the
read-path phase; until then it injects nothing, which is exactly how the
system behaves for a user with an empty memory.
"""

import logging
from typing import Any, Dict, List

from config.env import USE_POSTGRES

logger = logging.getLogger(__name__)


async def add_memory_context_to_messages(
    messages: List[Dict[str, str]],
    query: str,
    user_id: int,
) -> List[Dict[str, Any]]:
    """
    Read the user's memory layers and append the framed context blocks.

    Args:
        messages: LLM message list to append to (modified in place)
        query: The user's message text used as the retrieval query
        user_id: Authenticated user's integer ID

    Returns:
        List of memory item dicts used as context (for display on frontend).
        Each dict has: content, memory_type, categories.
    """
    if not query or not query.strip():
        return []

    if not USE_POSTGRES:
        logger.debug("Memory context injection skipped: USE_POSTGRES is False")
        return []

    # Read path lands with memory/services/context.py — nothing to inject yet.
    return []
