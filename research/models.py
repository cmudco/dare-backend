import uuid

from django.conf import settings
from django.db import models

from common.managers import ActiveObjectsManager
from common.models import BaseModel


class ResearchProjectStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    ARCHIVED = "archived", "Archived"


class ResearchSourceKind(models.TextChoices):
    FILE = "file", "File"
    URL = "url", "URL"
    DOI = "doi", "DOI"
    MANUAL = "manual", "Manual"


class ResearchReviewStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    LATER = "later", "Later"


class ResearchEvidenceLabel(models.TextChoices):
    SUPPORTING = "supporting", "Supporting"
    DISPUTING = "disputing", "Disputing"
    PARTIAL = "partial", "Partial"
    TANGENTIAL = "tangential", "Tangential"
    WEAK = "weak", "Weak"
    UNVERIFIABLE = "unverifiable", "Unverifiable"


class ResearchStagingItemType(models.TextChoices):
    SOURCE_CANDIDATE = "source_candidate", "Source Candidate"
    CLAIM = "claim", "Claim"
    NOTE = "note", "Note"


class ResearchAgentRole(models.TextChoices):
    MAIN_ASSISTANT = "main_assistant", "Main Assistant"
    SCOUT = "scout", "Scout"
    LIBRARIAN = "librarian", "Librarian"
    PAPER_ASSISTANT = "paper_assistant", "Paper-Specific Assistant"
    CRITIC = "critic", "Critic"
    PRESENTATION_ASSISTANT = "presentation_assistant", "Presentation Assistant"


class ResearchAgentRunStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"


class ResearchAgentOutputDestination(models.TextChoices):
    RUN_LOG = "run_log", "Run Log"
    STAGING = "staging", "Staging"
    REVIEW_METADATA = "review_metadata", "Review Metadata"
    MEMORY_PROPOSALS = "memory_proposals", "Memory Proposals"
    ARTIFACT_PROPOSALS = "artifact_proposals", "Artifact Proposals"


class ResearchAgentToolCallStatus(models.TextChoices):
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"
    SKIPPED = "skipped", "Skipped"


class ResearchSoulFileTemplate(models.TextChoices):
    RESEARCH_ETHICS = "research-ethics", "Research Ethics"
    EMPIRICAL_RIGOR = "empirical-rigor", "Empirical Rigor"
    CUSTOM = "custom", "Custom"


def default_run_selected_context() -> dict:
    return {
        "knowledge_item_ids": [],
        "staging_item_ids": [],
        "source_ids": [],
        "conversation_ids": [],
        "notes": "",
    }


def default_run_capability_policy() -> dict:
    return {
        "can_create_staging": False,
        "can_update_review_metadata": False,
        "can_propose_memory": False,
        "can_propose_artifacts": False,
        "can_approve_knowledge": False,
    }


class ResearchSoulFile(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="research_soul_files",
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    template_key = models.CharField(
        max_length=64,
        choices=ResearchSoulFileTemplate.choices,
        default=ResearchSoulFileTemplate.CUSTOM,
    )
    is_default = models.BooleanField(default=False)

    objects = models.Manager()
    active_objects = ActiveObjectsManager()

    class Meta:
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["user", "is_default"]),
            models.Index(fields=["user", "is_deleted"]),
        ]

    @property
    def current_version(self):
        return self.versions.order_by("-version_number").first()

    def __str__(self) -> str:
        return self.title


class ResearchSoulFileVersion(BaseModel):
    soul_file = models.ForeignKey(
        ResearchSoulFile,
        on_delete=models.CASCADE,
        related_name="versions",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="research_soul_file_versions",
    )
    version_number = models.PositiveIntegerField()
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    template_key = models.CharField(
        max_length=64,
        choices=ResearchSoulFileTemplate.choices,
        default=ResearchSoulFileTemplate.CUSTOM,
    )
    body = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_research_soul_file_versions",
    )

    objects = models.Manager()
    active_objects = ActiveObjectsManager()

    class Meta:
        ordering = ["-version_number"]
        unique_together = ["soul_file", "version_number"]
        indexes = [
            models.Index(fields=["soul_file", "version_number"]),
            models.Index(fields=["user", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.title} v{self.version_number}"


class ResearchProject(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="research_projects",
    )
    title = models.CharField(max_length=255)
    question = models.TextField()
    field = models.CharField(max_length=255, blank=True)
    status = models.CharField(
        max_length=24,
        choices=ResearchProjectStatus.choices,
        default=ResearchProjectStatus.ACTIVE,
    )
    enabled_tools = models.JSONField(default=list, blank=True)
    standards_template = models.CharField(max_length=64, blank=True)
    active_soul_file = models.ForeignKey(
        ResearchSoulFile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="projects",
    )

    objects = models.Manager()
    active_objects = ActiveObjectsManager()

    class Meta:
        ordering = ["-updated_at"]
        indexes = [
            models.Index(
                fields=["user", "status"], name="research_re_user_id_4353f8_idx"
            ),
            models.Index(
                fields=["user", "is_deleted"], name="research_re_user_id_49db24_idx"
            ),
        ]

    def __str__(self) -> str:
        return self.title


class ResearchSource(BaseModel):
    project = models.ForeignKey(
        ResearchProject,
        on_delete=models.CASCADE,
        related_name="sources",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="research_sources",
    )
    kind = models.CharField(
        max_length=24,
        choices=ResearchSourceKind.choices,
        default=ResearchSourceKind.MANUAL,
    )
    title = models.CharField(max_length=500)
    citation = models.TextField(blank=True)
    url = models.URLField(blank=True)
    doi = models.CharField(max_length=255, blank=True)
    authors = models.TextField(blank=True)
    venue = models.CharField(max_length=255, blank=True)
    year = models.PositiveIntegerField(null=True, blank=True)
    abstract = models.TextField(blank=True)
    notes = models.TextField(blank=True)

    objects = models.Manager()
    active_objects = ActiveObjectsManager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["project", "is_deleted"], name="research_re_project_eea6ec_idx"
            ),
            models.Index(
                fields=["user", "is_deleted"], name="research_re_user_id_0c37e1_idx"
            ),
        ]

    def __str__(self) -> str:
        return self.title


class ResearchStagingItem(BaseModel):
    project = models.ForeignKey(
        ResearchProject,
        on_delete=models.CASCADE,
        related_name="staging_items",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="research_staging_items",
    )
    source = models.ForeignKey(
        ResearchSource,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="staging_items",
    )
    item_type = models.CharField(
        max_length=32,
        choices=ResearchStagingItemType.choices,
        default=ResearchStagingItemType.SOURCE_CANDIDATE,
    )
    title = models.CharField(max_length=500)
    authors = models.TextField(blank=True)
    venue = models.CharField(max_length=255, blank=True)
    year = models.PositiveIntegerField(null=True, blank=True)
    url = models.URLField(blank=True)
    doi = models.CharField(max_length=255, blank=True)
    content = models.TextField(blank=True)
    rationale = models.TextField(blank=True)
    confidence = models.PositiveSmallIntegerField(default=0)
    confidence_rationale = models.TextField(blank=True)
    evidence_label = models.CharField(
        max_length=32,
        choices=ResearchEvidenceLabel.choices,
        default=ResearchEvidenceLabel.UNVERIFIABLE,
    )
    citation_context = models.TextField(blank=True)
    provenance = models.JSONField(default=dict, blank=True)
    soul_file_version = models.ForeignKey(
        ResearchSoulFileVersion,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="staging_items",
    )
    status = models.CharField(
        max_length=24,
        choices=ResearchReviewStatus.choices,
        default=ResearchReviewStatus.PENDING,
    )
    rejection_reason = models.TextField(blank=True)
    later_reason = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_research_staging_items",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    objects = models.Manager()
    active_objects = ActiveObjectsManager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["project", "status"]),
            models.Index(fields=["user", "status"]),
            models.Index(fields=["is_deleted", "is_active"]),
        ]

    def __str__(self) -> str:
        return self.title


class ResearchKnowledgeItem(BaseModel):
    project = models.ForeignKey(
        ResearchProject,
        on_delete=models.CASCADE,
        related_name="knowledge_items",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="research_knowledge_items",
    )
    staging_item = models.OneToOneField(
        ResearchStagingItem,
        on_delete=models.PROTECT,
        related_name="knowledge_item",
    )
    source = models.ForeignKey(
        ResearchSource,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="knowledge_items",
    )
    title = models.CharField(max_length=500)
    authors = models.TextField(blank=True)
    venue = models.CharField(max_length=255, blank=True)
    year = models.PositiveIntegerField(null=True, blank=True)
    url = models.URLField(blank=True)
    doi = models.CharField(max_length=255, blank=True)
    content = models.TextField(blank=True)
    rationale = models.TextField(blank=True)
    confidence = models.PositiveSmallIntegerField(default=0)
    confidence_rationale = models.TextField(blank=True)
    evidence_label = models.CharField(
        max_length=32,
        choices=ResearchEvidenceLabel.choices,
        default=ResearchEvidenceLabel.UNVERIFIABLE,
    )
    citation_context = models.TextField(blank=True)
    provenance = models.JSONField(default=dict, blank=True)
    soul_file_version = models.ForeignKey(
        ResearchSoulFileVersion,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="knowledge_items",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="approved_research_knowledge_items",
    )
    approved_at = models.DateTimeField()

    objects = models.Manager()
    active_objects = ActiveObjectsManager()

    class Meta:
        ordering = ["-approved_at"]
        indexes = [
            models.Index(fields=["project", "approved_at"]),
            models.Index(fields=["user", "approved_at"]),
        ]

    def __str__(self) -> str:
        return self.title


class ResearchAgentRun(BaseModel):
    run_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    project = models.ForeignKey(
        ResearchProject,
        on_delete=models.CASCADE,
        related_name="agent_runs",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="research_agent_runs",
    )
    role = models.CharField(max_length=48, choices=ResearchAgentRole.choices)
    status = models.CharField(
        max_length=24,
        choices=ResearchAgentRunStatus.choices,
        default=ResearchAgentRunStatus.QUEUED,
    )
    task = models.TextField()
    selected_context = models.JSONField(
        default=default_run_selected_context, blank=True
    )
    allowed_tools = models.JSONField(default=list, blank=True)
    capability_policy = models.JSONField(
        default=default_run_capability_policy,
        blank=True,
    )
    output_destination = models.CharField(
        max_length=48,
        choices=ResearchAgentOutputDestination.choices,
        default=ResearchAgentOutputDestination.RUN_LOG,
    )
    soul_file_version = models.ForeignKey(
        ResearchSoulFileVersion,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="agent_runs",
    )
    external_run_id = models.CharField(max_length=255, blank=True)
    status_message = models.TextField(blank=True)
    error_message = models.TextField(blank=True)
    cost_usd = models.DecimalField(max_digits=12, decimal_places=6, default=0)
    queued_at = models.DateTimeField()
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    objects = models.Manager()
    active_objects = ActiveObjectsManager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["project", "status"]),
            models.Index(fields=["user", "status"]),
            models.Index(fields=["role", "status"]),
            models.Index(fields=["run_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_role_display()} run {self.run_id}"


class ResearchAgentToolCall(BaseModel):
    run = models.ForeignKey(
        ResearchAgentRun,
        on_delete=models.CASCADE,
        related_name="tool_calls",
    )
    project = models.ForeignKey(
        ResearchProject,
        on_delete=models.CASCADE,
        related_name="agent_tool_calls",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="research_agent_tool_calls",
    )
    tool_name = models.CharField(max_length=255)
    status = models.CharField(
        max_length=24,
        choices=ResearchAgentToolCallStatus.choices,
        default=ResearchAgentToolCallStatus.SUCCEEDED,
    )
    input_summary = models.TextField(blank=True)
    output_summary = models.TextField(blank=True)
    error_message = models.TextField(blank=True)
    cost_usd = models.DecimalField(max_digits=12, decimal_places=6, default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    objects = models.Manager()
    active_objects = ActiveObjectsManager()

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["run", "created_at"]),
            models.Index(fields=["project", "tool_name"]),
            models.Index(fields=["user", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.tool_name} for {self.run.run_id}"


__all__ = [
    "ResearchAgentOutputDestination",
    "ResearchAgentRole",
    "ResearchAgentRun",
    "ResearchAgentRunStatus",
    "ResearchAgentToolCall",
    "ResearchAgentToolCallStatus",
    "ResearchEvidenceLabel",
    "ResearchKnowledgeItem",
    "ResearchProject",
    "ResearchProjectStatus",
    "ResearchReviewStatus",
    "ResearchSoulFile",
    "ResearchSoulFileTemplate",
    "ResearchSoulFileVersion",
    "ResearchSource",
    "ResearchSourceKind",
    "ResearchStagingItem",
    "ResearchStagingItemType",
]
