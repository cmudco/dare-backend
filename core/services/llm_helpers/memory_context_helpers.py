"""Helpers for injecting memory context into LLM messages via semantic search."""

import logging
from typing import List, Dict, Any

from config.env import USE_POSTGRES
from memory.services import get_memu_service

logger = logging.getLogger(__name__)


async def add_memory_context_to_messages(
    messages: List[Dict[str, str]],
    query: str,
    user_id: int,
) -> List[Dict[str, Any]]:
    """
    Search user's memory store and append matching items as context.

    Uses the user's message as a semantic search query against their
    personal memory store. Skips gracefully when USE_POSTGRES is False.

    Args:
        messages: LLM message list to append to (modified in place)
        query: The user's message text used as the search query
        user_id: Authenticated user's integer ID

    Returns:
        List of memory item dicts used as context (for display on frontend).
        Each dict has: content, memoryType, categories.
    """
    if not query or not query.strip():
        return []

    if not USE_POSTGRES:
        logger.debug("Memory context injection skipped: USE_POSTGRES is False")
        return []

    service = get_memu_service()
    try:
        result = await service.search(str(user_id), query.strip())
    except Exception as exc:
        logger.warning(
            "Failed to search memory for context injection for user %s: %s",
            user_id,
            exc,
        )
        return []

    items = result.get("items", []) if isinstance(result, dict) else []
    fetched_contents: List[str] = []
    used_items: List[Dict[str, Any]] = []

    for item in items:
        if hasattr(item, "model_dump"):
            item = item.model_dump()
        elif hasattr(item, "__dict__") and not isinstance(item, (str, dict)):
            item = vars(item)

        if isinstance(item, dict):
            content = (item.get("content") or item.get("summary") or "").strip()
        else:
            content = str(item).strip()
            item = {"content": content}

        if content:
            fetched_contents.append(content)
            used_items.append({
                "content": content,
                "memory_type": item.get("memory_type", ""),
                "categories": item.get("categories", []),
            })

    if not fetched_contents:
        return []

    formatted = "\n".join(f"- {entry}" for entry in fetched_contents)
    context_block = (
        "Relevant memories from the user's personal memory store:\n" + formatted
    )
    messages.append({"role": "user", "content": context_block})

    return used_items
