"""
Workflow Sharing Service

Handles publish, unpublish, and fork operations for workflows.
Centralizes sharing business logic outside of views.
"""
import logging

from django.db import transaction
from django.utils import timezone

from workflows.constants import (
    SharingErrorCode,
    SharingErrorMessage,
)
from workflows.models import Workflow

logger = logging.getLogger(__name__)


class SharingValidationError(Exception):
    """Raised when a sharing operation fails validation."""
    def __init__(self, message: str, error_code: str):
        super().__init__(message)
        self.error_code = error_code


class WorkflowSharingService:
    """Service for workflow publish/unpublish/fork operations."""

    @staticmethod
    def toggle_publish(workflow: Workflow, user) -> Workflow:
        """
        Toggle the published status of a workflow.

        Args:
            workflow: The workflow to publish/unpublish.
            user: The requesting user (must be the owner).

        Returns:
            The updated workflow.

        Raises:
            SharingValidationError: If the user is not the owner or the workflow is forked.
        """
        if workflow.user != user:
            raise SharingValidationError(
                SharingErrorMessage.PERMISSION_DENIED,
                SharingErrorCode.PERMISSION_DENIED,
            )

        if workflow.file_owner_id is not None:
            raise SharingValidationError(
                SharingErrorMessage.CANNOT_PUBLISH_FORKED,
                SharingErrorCode.CANNOT_PUBLISH_FORKED,
            )

        workflow.is_published = not workflow.is_published
        workflow.published_at = timezone.now() if workflow.is_published else None
        workflow.save(update_fields=['is_published', 'published_at', 'updated_at'])

        return workflow

    @staticmethod
    def fork(workflow_id: int, user, cloning_service) -> Workflow:
        """
        Fork a published workflow for the given user.

        Args:
            workflow_id: The ID of the workflow to fork.
            user: The user who will own the forked copy.
            cloning_service: WorkflowCloningService instance for cloning.

        Returns:
            The newly created forked workflow.

        Raises:
            SharingValidationError: If the workflow is not found or not published.
        """
        workflow = Workflow.active_objects.filter(
            pk=workflow_id,
            is_published=True,
        ).first()

        if not workflow:
            raise SharingValidationError(
                SharingErrorMessage.WORKFLOW_NOT_PUBLISHED,
                SharingErrorCode.NOT_FOUND,
            )

        with transaction.atomic():
            forked = cloning_service.clone_workflow(
                original=workflow,
                target_user=user,
                file_owner_id=workflow.user.id,
            )

        return forked
