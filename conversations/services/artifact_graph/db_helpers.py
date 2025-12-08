"""
Database Helper Functions for Artifact Generation

Provides async database operations for the artifact generation workflow.
All functions are async-compatible using Django's sync_to_async.
"""

import logging
from typing import Dict, Any, Optional

from asgiref.sync import sync_to_async
from django.db import connection

from conversations.models import Artifact, ArtifactCheckpoint, Conversation, Message, LLM
from conversations.constants import ArtifactStatus


logger = logging.getLogger(__name__)


# ========== Model Fetchers ==========


@sync_to_async
def get_llm(llm_id: int) -> LLM:
    """Get LLM from database."""
    return LLM.objects.get(id=llm_id)


@sync_to_async
def get_conversation(conversation_id: str) -> Conversation:
    """Get conversation from database."""
    return Conversation.active_objects.get(conversation_id=conversation_id)


@sync_to_async
def get_artifact(artifact_id: int) -> Artifact:
    """Get artifact from database."""
    return Artifact.active_objects.get(id=artifact_id)


@sync_to_async
def get_conversation_history(conversation: Conversation, limit: int = 10) -> list:
    """
    Get recent conversation history for context.

    Returns list of messages in format [{"role": "user"|"assistant", "content": str}]
    """
    from conversations.constants import SenderType

    messages = conversation.messages.filter(is_active=True).order_by('-created_at')[:limit]
    history = []

    for msg in reversed(list(messages)):
        role = "user" if msg.sender_type == SenderType.PLAYER else "assistant"
        content = msg.message or ""

        # If message has artifacts, include artifact info
        # Use the reverse relation 'artifacts' from Message model
        msg_artifacts = msg.artifacts.filter(is_active=True)
        if msg_artifacts.exists():
            artifact = msg_artifacts.first()
            content = f"[Generated artifact: '{artifact.title}' - {artifact.artifact_type}]\n{content}"

        if content.strip():
            history.append({"role": role, "content": content})

    return history


# ========== Artifact CRUD ==========


@sync_to_async
def create_artifact_db(
    conversation: Conversation,
    message: Optional[Message],
    artifact_type: str,
    title: str,
    outline: str,
    estimated_sections: int,
    language: Optional[str] = None,
) -> Artifact:
    """Create artifact in database."""
    artifact = Artifact(
        conversation=conversation,
        message=message,
        artifact_type=artifact_type,
        title=title,
        outline=outline,
        estimated_sections=estimated_sections,
        current_section=0,
        status=ArtifactStatus.PLANNING,
        language=language,
    )
    artifact.save()
    return artifact


@sync_to_async
def update_artifact_db(
    artifact_id: int,
    **kwargs
) -> Artifact:
    """Update artifact in database."""
    artifact = Artifact.active_objects.get(id=artifact_id)
    for key, value in kwargs.items():
        setattr(artifact, key, value)
    artifact.save()
    return artifact


@sync_to_async
def create_checkpoint_db(
    artifact: Artifact,
    content_snapshot: str,
    current_section: int,
    iteration_count: int,
    state_data: Dict[str, Any],
) -> ArtifactCheckpoint:
    """Create checkpoint in database."""
    checkpoint = ArtifactCheckpoint(
        artifact=artifact,
        content_snapshot=content_snapshot,
        current_section=current_section,
        iteration_count=iteration_count,
        state_data=state_data,
    )
    checkpoint.save()
    return checkpoint


@sync_to_async
def check_artifact_paused(artifact_id: int) -> bool:
    """Check if artifact has been paused by user."""
    try:
        # Use select_for_update to ensure we read the latest committed data
        # and avoid reading stale cached data
        connection.ensure_connection()

        artifact = Artifact.active_objects.get(id=artifact_id)
        # Force refresh from database to get latest status
        artifact.refresh_from_db(fields=['status'])
        is_paused = artifact.status == ArtifactStatus.PAUSED
        logger.info(f"Check artifact paused: artifact_id={artifact_id}, status={artifact.status}, is_paused={is_paused}")
        return is_paused
    except Artifact.DoesNotExist:
        logger.warning(f"Check artifact paused: artifact_id={artifact_id} not found")
        return False
