"""
Conversation-history builders for LLM context.

Rebuilds prior turns in the internal (OpenAI) message format. Assistant
messages that used tools replay as structured tool turns — the
assistant-with-``tool_calls`` turn followed by its ``role:"tool"`` results —
reconstructed from persisted ``MessageToolCall`` rows, instead of the old
"--- Tool Results ---" prose. Only the most recent tool-using messages get
the full replay; older ones compress to a one-line usage note to protect
the token budget.
"""

import logging
from typing import Any, Dict, List

from channels.db import database_sync_to_async

from conversations.constants import SenderType
from conversations.models import Artifact, Conversation, Message

from .tool_turn_helpers import build_history_tool_turns, summarize_tool_usage

logger = logging.getLogger(__name__)

# Maximum artifact content to include in LLM context (chars, ~2k tokens)
MAX_ARTIFACT_CONTENT_IN_CONTEXT = 8000

# Assistant messages (counted from the newest) whose tool calls replay as
# full structured turns; older ones compress to a one-line usage note.
TOOL_HISTORY_MESSAGE_WINDOW = 6


@database_sync_to_async
def get_conversation_history(conversation: Conversation, limit: int = 10) -> list:
    """Retrieves recent chat history for AI context, including tool turns and artifacts.

    Assistant messages that made tool calls are replayed as structured tool
    turns (assistant ``tool_calls`` + ``role:"tool"`` results) when they fall
    inside ``TOOL_HISTORY_MESSAGE_WINDOW``; outside it their text carries a
    compact "[used tools: ...]" suffix instead.

    For assistant messages with artifacts, includes the LATEST VERSION of each
    artifact group to enable intelligent inline edits using string replacement.

    Args:
        conversation: Conversation instance
        limit: Maximum number of messages to retrieve

    Returns:
        Flat list of message dictionaries in internal (OpenAI) format
    """
    messages = (
        Message.active_objects.filter(conversation=conversation)
        .prefetch_related("mcp_tool_calls", "artifacts__artifact_group__latest_version")
        .order_by("-created_at")
    )

    if limit >= 50:
        messages = messages[2:]
    else:
        messages = messages[2 : limit + 2] if limit > 0 else messages[2:]

    ordered = list(reversed(messages))

    # Rank assistant messages from the newest backwards to apply the window.
    assistant_recency: Dict[int, int] = {}
    rank = 0
    for msg in reversed(ordered):
        if msg.sender_type == SenderType.AI_ASSISTANT:
            assistant_recency[msg.id] = rank
            rank += 1

    # Track artifact groups to only include latest version once
    included_artifact_groups = set()

    result: List[Dict[str, Any]] = []
    for msg in ordered:
        if msg.sender_type != SenderType.AI_ASSISTANT:
            result.append({"role": "user", "content": msg.message})
            continue

        content = msg.message
        tool_calls = list(msg.mcp_tool_calls.all())

        if tool_calls:
            if assistant_recency.get(msg.id, 0) < TOOL_HISTORY_MESSAGE_WINDOW:
                result.extend(build_history_tool_turns(msg.id, tool_calls))
            else:
                suffix = summarize_tool_usage(tool_calls)
                content = f"{content}\n\n{suffix}" if content else suffix

        content = _append_artifact_context(msg, content, included_artifact_groups)
        result.append({"role": "assistant", "content": content})

    logger.info(
        f"[get_conversation_history] Returning {len(result)} turns, "
        f"{len(included_artifact_groups)} artifact groups included"
    )
    return result


def _append_artifact_context(
    msg: Message, content: str, included_artifact_groups: set
) -> str:
    """Attach the latest version of each of the message's artifact groups."""
    artifacts = msg.artifacts.filter(is_deleted=False)
    for artifact in artifacts:
        group = artifact.artifact_group
        if not group:
            continue
        group_id = group.id

        # Skip if we've already included the latest version from this group
        if group_id in included_artifact_groups:
            logger.debug(
                f"[get_conversation_history] Skipping artifact #{artifact.id} "
                f"(group {group_id} already included)"
            )
            continue
        included_artifact_groups.add(group_id)

        # Use the LATEST version from the group, not the version attached to this message
        latest_artifact = group.latest_version
        if not latest_artifact:
            latest_artifact = artifact  # Fallback to current if no latest set

        artifact_context = _format_artifact_for_history(latest_artifact)
        content = f"{content}\n\n{artifact_context}"
        logger.info(
            f"[get_conversation_history] Including artifact #{latest_artifact.id} "
            f"(type={latest_artifact.artifact_type}, v{latest_artifact.version}, group={group_id}) "
            f"as LATEST version in message {msg.id}"
        )
    return content


def _format_artifact_for_history(artifact: "Artifact") -> str:
    """Format a single artifact for inclusion in conversation history.

    Provides structured context that helps the LLM understand:
    - Artifact identity (ID, title, group)
    - Type and version for context
    - Complete content for string-based modifications

    Args:
        artifact: Artifact model instance

    Returns:
        Formatted string with artifact metadata and content
    """
    # Truncate very large artifacts to prevent context explosion
    content_to_include = artifact.content
    if len(content_to_include) > MAX_ARTIFACT_CONTENT_IN_CONTEXT:
        content_to_include = (
            content_to_include[:MAX_ARTIFACT_CONTENT_IN_CONTEXT]
            + f"\n... [truncated, {len(artifact.content)} total chars]"
        )

    return f"""[Artifact #{artifact.id}] {artifact.title}
Type: {artifact.artifact_type} | Version: v{artifact.version} | Group: {artifact.artifact_group_id}
--- Content ---
{content_to_include}
--- End Content ---"""
