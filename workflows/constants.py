from django.db import models

APP_NAME = "workflows"


class Mode(models.TextChoices):
    """Workflow execution mode."""
    SEQUENTIAL = 'sequential', 'Sequential'
    PARALLEL = 'parallel', 'Parallel'


class WorkflowRunStepStatus(models.TextChoices):
    PENDING = 'pending', 'Pending'
    RUNNING = 'running', 'Running'
    COMPLETED = 'completed', 'Completed'
    FAILED = 'failed', 'Failed'
    SKIPPED = 'skipped', 'Skipped'
    PENDING_HUMAN_INPUT = 'pending_human_input', 'Pending Human Input'


class BatchRunStatus(models.TextChoices):
    RUNNING = 'running', 'Running'
    COMPLETED = 'completed', 'Completed'
    FAILED = 'failed', 'Failed'


class RetrievalMode(models.TextChoices):
    """How file content is retrieved in a file node."""
    EMBEDDINGS = 'embeddings', 'Embeddings (Vector Search)'
    CONTENT = 'content', 'Full Content'
    BOTH = 'both', 'Both Embeddings and Content'


class QuerySource(models.TextChoices):
    """Source of query text for vector search in file nodes."""
    PREVIOUS_STEP = 'previous_step', 'Previous Step Output'
    TEXT_INPUT = 'text_input', 'Text Input'


# ============================================================================
# Workflow Sharing Constants
# ============================================================================

FORK_TITLE_PREFIX = "FORK OF"
DEFAULT_FORK_TITLE = "Untitled Workflow"


class SharingErrorCode:
    """Error codes for workflow sharing API responses."""
    PERMISSION_DENIED = "permission_denied"
    NOT_FOUND = "not_found"
    CANNOT_PUBLISH_FORKED = "cannot_publish_forked"
    FORK_FAILED = "fork_failed"


class SharingErrorMessage:
    """Error messages for workflow sharing API responses."""
    PERMISSION_DENIED = "Permission denied"
    WORKFLOW_NOT_FOUND = "Workflow not found"
    WORKFLOW_NOT_PUBLISHED = "Workflow not found or not published"
    CANNOT_PUBLISH_FORKED = "Cannot publish forked workflows. Only original workflows can be published."
