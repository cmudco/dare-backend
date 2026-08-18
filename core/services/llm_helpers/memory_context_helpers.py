"""Attach the current memory layers to a model request."""

import logging
from typing import Any, Dict, List

from channels.db import database_sync_to_async
from django.contrib.auth import get_user_model

from config.env import USE_POSTGRES
from memory.services.context import ReadContext, read_context

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


def _read_context_for_user(user_id: int, query: str) -> ReadContext | None:
    user = get_user_model().objects.filter(pk=user_id).first()
    if user is None:
        return None
    return read_context(user, query)
