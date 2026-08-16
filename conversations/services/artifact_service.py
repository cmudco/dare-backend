"""Public persistence helpers for DARE artifacts."""

from typing import Any, Dict, Optional

from asgiref.sync import sync_to_async
from django.db import transaction

from conversations.constants import ArtifactStatus
from conversations.models import Artifact, ArtifactGroup, Conversation, Message


@sync_to_async
def create_artifact(
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
