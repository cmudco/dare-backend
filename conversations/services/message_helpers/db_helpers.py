"""
Database Helper Functions for Message Coordinator

Standalone `@database_sync_to_async` functions for database operations.
These functions are extracted from MessageCoordinator to improve modularity.

All functions are stateless - they receive the required models (conversation,
message, etc.) as parameters instead of accessing class instance state.
"""

import logging
from typing import Dict, List, Optional

from channels.db import database_sync_to_async

from billing import litellm_models_service
from billing.models import LiteLLMKey
from conversations.constants import SenderType
from conversations.models import LLM, Conversation, Message
from core.services.dtos import LLMDescriptor

logger = logging.getLogger(__name__)


@database_sync_to_async
def get_ai_message_by_id(
    message_id: int, conversation_id: int
) -> Optional[Message]:
    """Fetch an AI message by ID with dispatch relations loaded.

    Eager-loads ``llm`` and ``litellm_key`` so the regeneration path can
    rebuild an ``LLMDescriptor`` from the persisted message without a second
    DB hit.

    Args:
        message_id: ID of the AI message to fetch
        conversation_id: Conversation the active socket coordinator owns

    Returns:
        Message instance or None if not found
    """
    return (
        Message.active_objects.select_related("llm", "litellm_key")
        .filter(
            id=message_id,
            conversation_id=conversation_id,
            sender_type=SenderType.AI_ASSISTANT,
        )
        .first()
    )


@database_sync_to_async
def get_message_media_file_ids(message: Message) -> List[int]:
    """Get audio/video file IDs attached to a message.

    Args:
        message: Message instance to get media files from

    Returns:
        List of file IDs for audio/video files
    """
    return list(
        message.files.filter(media_type__in=["audio", "video"]).values_list(
            "id", flat=True
        )
    )


def _resolve_litellm_ref(
    key_id: str, model_name: str, user=None
) -> Optional[LLMDescriptor]:
    """Look up a LiteLLM dispatch reference and build the descriptor.

    Uses the cached probe (`billing.litellm_models_service.list_models`) to
    discover the provider the proxy reports for ``model_name``; falls back
    to ``"custom"`` if the probe is unavailable so dispatch can still run.
    """
    key_queryset = (
        LiteLLMKey.visible_for_user(user)
        if user is not None
        else LiteLLMKey.objects.all()
    )
    key = key_queryset.filter(pk=key_id).first()
    if key is None:
        logger.info("LiteLLM ref references missing LiteLLMKey id=%s", key_id)
        return None
    if getattr(key, "is_expired", False):
        logger.info("LiteLLM ref references expired LiteLLMKey id=%s", key_id)
        return None

    cached = litellm_models_service.list_models(key)
    provider = next(
        (m.provider for m in cached.models if m.name == model_name and m.provider),
        "custom",
    )
    return LLMDescriptor.from_litellm(
        litellm_key=key, model_name=model_name, provider=provider
    )


LITELLM_ID_PREFIX = "litellm:"


def _visible_llms_for_user(user):
    """Mirror the model catalog entitlement rules at dispatch time."""
    return LLM.visible_for_user(user)


@database_sync_to_async
def parse_model_id(model_id, user=None) -> Optional[LLMDescriptor]:
    """Resolve an opaque ``model_id`` string to an ``LLMDescriptor``.

    The FE treats ``model_id`` as opaque — it just hands back whatever the
    picker endpoint gave it. Two encodings:

      ``"<int>"``                       → DB-backed LLM (PK)
      ``"litellm:<key_pk>:<model>"``    → LiteLLM-routed dispatch

    Returns ``None`` for an unknown id, deleted/expired LiteLLM key, or
    malformed string — caller falls back to the conversation default.
    """
    if not isinstance(model_id, str) or not model_id:
        return None
    if model_id.startswith(LITELLM_ID_PREFIX):
        try:
            _, key_id, model_name = model_id.split(":", 2)
        except ValueError:
            logger.warning("Malformed LiteLLM model_id: %r", model_id)
            return None
        if not key_id or not model_name:
            logger.warning("Malformed LiteLLM model_id: %r", model_id)
            return None
        return _resolve_litellm_ref(key_id, model_name, user=user)
    try:
        pk = int(model_id)
    except ValueError:
        logger.warning("Unparseable model_id: %r", model_id)
        return None
    llm = _visible_llms_for_user(user).filter(id=pk).first()
    return LLMDescriptor.from_llm(llm) if llm else None


@database_sync_to_async
def get_conversation_default_descriptor(
    conversation: Conversation, user=None
) -> Optional[LLMDescriptor]:
    """Get the descriptor for the conversation's default LLM (or first available).

    The conversation-level default is always a real DB-backed LLM — synthetic
    LiteLLM models are picked per-message and never persisted on
    ``Conversation.selected_model``.
    """
    visible = _visible_llms_for_user(user)
    selected_model_id = getattr(conversation, "selected_model_id", None)
    llm = (
        visible.filter(id=selected_model_id).first()
        if selected_model_id is not None
        else visible.first()
    )
    return LLMDescriptor.from_llm(llm) if llm else None


@database_sync_to_async
def fetch_preceding_user_message(conversation: Conversation) -> Optional[Message]:
    """Get the most recent user message in the conversation.

    Args:
        conversation: Conversation instance

    Returns:
        Most recent user message or None
    """
    return (
        conversation.messages.filter(sender_type=SenderType.PLAYER)
        .order_by("-created_at")
        .first()
    )


@database_sync_to_async
def should_generate_title(conversation: Conversation) -> bool:
    """Check if we should generate a conversation title (first message pair).

    Args:
        conversation: Conversation instance

    Returns:
        True if this is the first user+AI message pair
    """
    return conversation.messages.count() == 2  # User + AI = 2 messages


@database_sync_to_async
def update_message_learning_progress(
    message_obj: "Message",
    assessment,
    learning_goals: str,
    tracking_prompt: str,
    progress_llm,
    last_usage: Optional[Dict],
) -> "Message":
    """Update message with learning progress data.

    Args:
        message_obj: Message to update
        assessment: Saved progress assessment
        learning_goals: Learning goals text
        tracking_prompt: Tracking prompt text
        progress_llm: LLM used for assessment
        last_usage: Final usage data

    Returns:
        Updated message instance
    """
    message_obj.learning_progress_data = {
        "progress_assessment_id": str(getattr(assessment, "id", "")),
        "learning_goals": learning_goals,
        "tracking_prompt": tracking_prompt,
        "llm_id": getattr(progress_llm, "id", None),
        "input_tokens": (last_usage or {}).get("input_tokens"),
        "output_tokens": (last_usage or {}).get("output_tokens"),
        "status": "completed",
    }
    message_obj.save(update_fields=["learning_progress_data"])
    return message_obj
