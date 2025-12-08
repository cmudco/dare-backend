"""
Database Helper Functions for Artifact Generation

Provides async database operations for the artifact generation workflow.
All functions are async-compatible using Django's sync_to_async.
"""

import logging
from typing import Dict, Any, Optional

from asgiref.sync import sync_to_async
from django.db import connection

from conversations.models import Artifact, ArtifactGroup, ArtifactCheckpoint, Conversation, Message, LLM
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
    """Create artifact in database with its ArtifactGroup."""
    # Create the artifact group first
    artifact_group = ArtifactGroup(
        conversation=conversation,
        base_title=title,
    )
    artifact_group.save()
    
    # Create the artifact (v1)
    artifact = Artifact(
        conversation=conversation,
        message=message,
        artifact_group=artifact_group,
        parent_artifact=None,  # First version has no parent
        artifact_type=artifact_type,
        title=title,
        outline=outline,
        estimated_sections=estimated_sections,
        current_section=0,
        status=ArtifactStatus.PLANNING,
        language=language,
        version=1,
    )
    artifact.save()
    
    # Set this artifact as the latest version
    artifact_group.latest_version = artifact
    artifact_group.save(update_fields=['latest_version'])
    
    return artifact


@sync_to_async
def create_artifact_version_db(
    parent_artifact: Artifact,
    new_outline: str,
    estimated_new_sections: int,
    message: Optional[Message] = None,
) -> Artifact:
    """
    Create a new version based on parent artifact.
    
    The new artifact:
    - Copies content from parent
    - Links to same artifact_group
    - Sets parent_artifact to the parent
    - Increments version number
    - Updates artifact_group.latest_version
    
    Args:
        parent_artifact: The artifact to create a new version from
        new_outline: The new/updated outline
        estimated_new_sections: Number of NEW sections to add
        message: Optional message to link to
        
    Returns:
        The newly created artifact version
    """
    # Calculate new totals
    new_estimated_total = parent_artifact.current_section + estimated_new_sections
    
    # Create new version using the model's helper method
    new_artifact = parent_artifact.create_new_version()
    
    # Update with new outline and sections
    new_artifact.message = message
    new_artifact.outline = new_outline
    new_artifact.estimated_sections = new_estimated_total
    new_artifact.status = ArtifactStatus.GENERATING
    new_artifact.save()
    
    logger.info(
        f"Created artifact version {new_artifact.version} "
        f"(id={new_artifact.id}) from parent {parent_artifact.id}"
    )
    
    return new_artifact



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
