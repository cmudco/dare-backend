"""Public persistence helpers for DARE artifacts."""

import hashlib
from typing import Any, Dict, Optional, Tuple

from asgiref.sync import sync_to_async
from django.db import transaction

from conversations.constants import SNIPPET_ARTIFACT_SOURCE_TOOL, ArtifactStatus
from conversations.domain.snippet_artifacts import snippet_artifact_shape
from conversations.models import Artifact, ArtifactGroup, Conversation, Message


def create_artifact_sync(
    *,
    conversation: Optional[Conversation],
    message: Optional[Message],
    title: str,
    content: str,
    artifact_type: str,
    filename: str,
    content_type: str,
    source_tool: str,
    metadata: Optional[Dict[str, Any]] = None,
    workflow_run_step=None,
) -> Artifact:
    """Create a completed artifact and its version group atomically.

    Exactly one host anchors the artifact: a chat ``conversation`` (with an
    optional ``message``) or a ``workflow_run_step``.
    """
    with transaction.atomic():
        group = ArtifactGroup.active_objects.create(
            conversation=conversation,
            workflow_run_step=workflow_run_step,
            base_title=title,
        )
        artifact = Artifact.active_objects.create(
            conversation=conversation,
            workflow_run_step=workflow_run_step,
            message=message,
            artifact_group=group,
            title=title,
            content=content,
            artifact_type=artifact_type,
            filename=filename,
            content_type=content_type,
            source_tool=source_tool,
            status=ArtifactStatus.COMPLETED,
            metadata=metadata or {},
            version=1,
        )
        group.latest_version = artifact
        group.save(update_fields=["latest_version"])
    return artifact


create_artifact = sync_to_async(create_artifact_sync)


def open_snippet_artifact(
    *, message: Message, language: str, content: str
) -> Tuple[Artifact, bool]:
    """Return the artifact for a message's code block, creating it on first open.

    A block is keyed by (message, hash of its original code), so reopening it
    returns the newest saved version instead of a fresh copy.
    """
    source_hash = hashlib.sha256(f"{language}\n{content}".encode()).hexdigest()
    existing = (
        Artifact.active_objects.filter(
            conversation_id=message.conversation_id,
            source_tool=SNIPPET_ARTIFACT_SOURCE_TOOL,
            metadata__source_message_id=message.id,
            metadata__source_hash=source_hash,
        )
        .select_related("artifact_group__latest_version")
        .first()
    )
    if existing:
        latest = existing.artifact_group and existing.artifact_group.latest_version
        return latest or existing, False

    shape = snippet_artifact_shape(language, content)
    artifact = create_artifact_sync(
        conversation=message.conversation,
        message=None,
        title=shape.title,
        content=content,
        artifact_type=shape.artifact_type,
        filename=shape.filename,
        content_type=shape.content_type,
        source_tool=SNIPPET_ARTIFACT_SOURCE_TOOL,
        metadata={
            "language": shape.language,
            "source_message_id": message.id,
            "source_hash": source_hash,
        },
    )
    return artifact, True


@sync_to_async
def create_artifact_version(
    *,
    existing: Artifact,
    message: Optional[Message],
    content: str,
    title: str,
    filename: str,
    content_type: str,
    metadata: Dict[str, Any],
) -> Artifact:
    """Create the next version from the newest artifact in a version group."""
    with transaction.atomic():
        latest = (
            Artifact.active_objects.select_for_update()
            .filter(artifact_group=existing.artifact_group)
            .order_by("-version")
            .first()
        )
        artifact = (latest or existing).create_new_version()
        artifact.message = message
        artifact.content = content
        artifact.title = title
        artifact.filename = filename
        artifact.content_type = content_type
        artifact.source_tool = existing.source_tool
        artifact.metadata = metadata
        artifact.status = ArtifactStatus.COMPLETED
        artifact.save(
            update_fields=[
                "message",
                "content",
                "title",
                "filename",
                "content_type",
                "source_tool",
                "metadata",
                "status",
            ]
        )
    return artifact
