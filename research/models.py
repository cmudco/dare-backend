"""
Research Mode models (the "epistemic ecosystem").

DARE owns the durable truth: projects, soul files, sources, staging, approved
knowledge, and audit. Agents (later, via Hermes) write to staging only; the
scholar promotes staged items to durable knowledge.

Models land incrementally. This module currently defines:
- ResearchProject: a single line of scholarly inquiry, owned by a researcher.
"""

from django.conf import settings
from django.db import models

from common.managers import ActiveObjectsManager
from common.models import BaseModel
from research.constants import (
    AgentRunStatus,
    AgentToolCallStatus,
    ChatMessageRole,
    MemoryProposalStatus,
    MemoryType,
    ProjectMemorySource,
    ResearchProjectStatus,
    ResearchSessionMode,
    ResearchSessionStatus,
    SourceType,
    StagingItemStatus,
    StagingSourceType,
)


class ResearchProject(BaseModel):
    """
    A single line of scholarly inquiry owned by a researcher.

    Acts as the container for the project's soul files, sources, staging items,
    agent runs, and approved knowledge (added in later increments).
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="research_projects",
        help_text="The researcher (scholar) who owns this project.",
    )
    title = models.CharField(
        max_length=255,
        help_text="Human-readable project title.",
    )
    question = models.TextField(
        blank=True,
        help_text="The central research question driving this project.",
    )
    field = models.CharField(
        max_length=255,
        blank=True,
        help_text="Field or domain of inquiry (e.g. 'Bioethics').",
    )
    status = models.CharField(
        max_length=32,
        choices=ResearchProjectStatus.choices(),
        default=ResearchProjectStatus.ACTIVE,
        help_text="Project workflow status (DARE-owned).",
    )
    enabled_tools = models.JSONField(
        default=list,
        blank=True,
        help_text="Research tool keys enabled for this project (free-form list of strings).",
    )
    standards_template = models.CharField(
        max_length=64,
        blank=True,
        help_text="Soul-file starter template chosen at creation (e.g. 'research-ethics').",
    )

    objects = models.Manager()
    active_objects = ActiveObjectsManager()

    class Meta:
        verbose_name = "Research Project"
        verbose_name_plural = "Research Projects"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.title} ({self.user_id})"


class ResearchSession(BaseModel):
    """
    A delegation context that maps 1:1 to a Hermes session.

    Each project has (at most) one persistent session per mode — a 'scout'
    session for delegated discovery and a 'chat' session for hands-on work — so
    Hermes keeps separate cross-run memory per mode. Agent runs hang off a
    session.
    """

    project = models.ForeignKey(
        ResearchProject,
        on_delete=models.CASCADE,
        related_name="sessions",
        help_text="Project this session belongs to.",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="research_sessions",
        help_text="Scholar who owns this session.",
    )
    mode = models.CharField(
        max_length=16,
        choices=ResearchSessionMode.choices(),
        help_text="Delegation mode (DARE-owned): 'scout' or 'chat'.",
    )
    hermes_session_id = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Correlating Hermes session id (set when Hermes is wired).",
    )
    title = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Optional human label (e.g. a chat session name).",
    )
    status = models.CharField(
        max_length=16,
        choices=ResearchSessionStatus.choices(),
        default=ResearchSessionStatus.ACTIVE,
        help_text="Session lifecycle (DARE-owned).",
    )
    last_run_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp of the most recent run in this session.",
    )

    objects = models.Manager()
    active_objects = ActiveObjectsManager()

    class Meta:
        verbose_name = "Research Session"
        verbose_name_plural = "Research Sessions"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.project_id} · {self.mode}"

    @classmethod
    def _get_or_create(cls, project, user, mode):
        session = cls.active_objects.filter(project=project, mode=mode).first()
        if session:
            return session
        return cls.objects.create(
            project=project,
            user=user,
            mode=mode,
            hermes_session_id=f"dare-proj{project.id}-{mode}",
        )

    @classmethod
    def get_or_create_chat_session(cls, project, user):
        """The project's one persistent chat session (stable hermes_session_id)."""
        return cls._get_or_create(project, user, ResearchSessionMode.CHAT)

    @classmethod
    def get_or_create_scout_session(cls, project, user):
        """The project's one persistent scout session (stable hermes_session_id)."""
        return cls._get_or_create(project, user, ResearchSessionMode.SCOUT)

    @classmethod
    def get_or_create_artifact_session(cls, project, user):
        """The project's one persistent artifact session (stable hermes_session_id)."""
        return cls._get_or_create(project, user, ResearchSessionMode.ARTIFACT)


class ResearchAgentRun(BaseModel):
    """
    One delegated run from DARE to Hermes; the durable audit record behind the
    Runs/activity view. Hangs off a ResearchSession and maps 1:1 to a Hermes run.

    Runs can be seeded manually for now; the Hermes adapter populates them later.
    """

    session = models.ForeignKey(
        ResearchSession,
        on_delete=models.CASCADE,
        related_name="runs",
        help_text="Session this run belongs to.",
    )
    project = models.ForeignKey(
        ResearchProject,
        on_delete=models.CASCADE,
        related_name="agent_runs",
        help_text="Project this run belongs to (denormalised for filtering).",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="research_agent_runs",
        help_text="Scholar who initiated the run.",
    )
    role = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Agent role slug (e.g. 'scout', 'critic'); free-form for now.",
    )
    mode = models.CharField(
        max_length=16,
        choices=ResearchSessionMode.choices(),
        help_text="Mode of this run, mirroring the session mode.",
    )
    task = models.TextField(
        blank=True,
        default="",
        help_text="The delegated task text.",
    )
    status = models.CharField(
        max_length=32,
        default=AgentRunStatus.STARTED,
        help_text="Run lifecycle (Hermes-produced; free-form).",
    )
    status_detail = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Human-readable live progress line (e.g. 'Searching the web…').",
    )
    soul_file_version = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Soul file version that governed this run (provenance).",
    )
    selected_context = models.JSONField(
        default=dict,
        blank=True,
        help_text="Explicitly chosen context: sourceIds, approvedKnowledgeIds, "
        "projectMemoryIds, conversationIds.",
    )
    allowed_tools = models.JSONField(
        default=list,
        blank=True,
        help_text="Tool slugs this run was allowed to use.",
    )
    started_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the run started.",
    )
    completed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the run finished (null while running).",
    )
    cost = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        default=0,
        help_text="Cumulative cost for the run.",
    )
    usage = models.JSONField(
        default=dict,
        blank=True,
        help_text="Token usage reported by the agent runtime "
        "(input_tokens / output_tokens / total_tokens).",
    )
    error = models.TextField(
        blank=True,
        default="",
        help_text="Error detail if the run failed.",
    )
    hermes_run_id = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Correlating Hermes run id (set when Hermes is wired).",
    )

    objects = models.Manager()
    active_objects = ActiveObjectsManager()

    class Meta:
        verbose_name = "Research Agent Run"
        verbose_name_plural = "Research Agent Runs"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.role or 'run'} · {self.status} ({self.project_id})"


class ResearchAgentToolCall(BaseModel):
    """Audit record for a single tool invocation within a run."""

    run = models.ForeignKey(
        ResearchAgentRun,
        on_delete=models.CASCADE,
        related_name="tool_calls",
        help_text="Run this tool call belongs to.",
    )
    tool = models.CharField(
        max_length=128,
        help_text="Tool/MCP slug invoked (e.g. 'consensus').",
    )
    arguments = models.JSONField(
        default=dict,
        blank=True,
        help_text="Arguments passed to the tool (includes the query).",
    )
    status = models.CharField(
        max_length=16,
        default=AgentToolCallStatus.SUCCESS,
        help_text="Outcome: 'success' or 'error' (free-form).",
    )
    result_summary = models.TextField(
        blank=True,
        default="",
        help_text="Brief summary of the result.",
    )
    duration_ms = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Duration of the call in milliseconds.",
    )
    result_tokens = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Token size of the result — how much this call added to the "
        "agent's context (the per-call input-token driver).",
    )
    error = models.TextField(
        blank=True,
        default="",
        help_text="Error detail if the call failed.",
    )

    objects = models.Manager()
    active_objects = ActiveObjectsManager()

    class Meta:
        verbose_name = "Research Agent Tool Call"
        verbose_name_plural = "Research Agent Tool Calls"
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.tool} · {self.status}"


class ResearchSource(BaseModel):
    """
    A source in the project's library — an uploaded file or a discovered record.

    Uploaded files store display metadata (name/kind/size) for now; actual file
    storage and processing are deferred. Discovered records (from Scout, later)
    carry the bibliographic fields.
    """

    project = models.ForeignKey(
        ResearchProject,
        on_delete=models.CASCADE,
        related_name="sources",
        help_text="Project this source belongs to.",
    )
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="research_sources",
        help_text="Who added this source (null if added by an agent).",
    )
    name = models.CharField(
        max_length=512,
        help_text="Display name (file name or source title).",
    )
    kind = models.CharField(
        max_length=128,
        blank=True,
        default="",
        help_text="Display kind, e.g. 'PDF', 'Book chapter', 'Report'.",
    )
    size_label = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Human-readable file size, e.g. '2.3 MB' (for uploads).",
    )
    page_count = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Number of pages, when known.",
    )
    source_type = models.CharField(
        max_length=32,
        blank=True,
        default=SourceType.UPLOAD,
        help_text="Kind of source (free-form): upload/paper/book/article/other.",
    )
    title = models.CharField(max_length=512, blank=True, default="")
    authors = models.CharField(max_length=512, blank=True, default="")
    year = models.PositiveIntegerField(null=True, blank=True)
    venue = models.CharField(max_length=255, blank=True, default="")
    doi = models.CharField(max_length=255, blank=True, default="")
    url = models.CharField(max_length=1024, blank=True, default="")
    abstract = models.TextField(blank=True, default="")

    objects = models.Manager()
    active_objects = ActiveObjectsManager()

    class Meta:
        verbose_name = "Research Source"
        verbose_name_plural = "Research Sources"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.project_id})"


class SoulFile(BaseModel):
    """
    A project's durable, versioned standards document (the "soul file").

    One per project. The content lives in versioned SoulFileVersion rows so that
    older staging items can keep the version that governed them; editing the soul
    file writes a new version rather than mutating the old one.
    """

    project = models.OneToOneField(
        ResearchProject,
        on_delete=models.CASCADE,
        related_name="soul_file",
        help_text="Project this soul file governs.",
    )
    name = models.CharField(
        max_length=255,
        default="Research standards",
        help_text="Display name for the soul file.",
    )

    objects = models.Manager()
    active_objects = ActiveObjectsManager()

    class Meta:
        verbose_name = "Soul File"
        verbose_name_plural = "Soul Files"

    def __str__(self):
        return f"Soul file ({self.project_id})"

    def current_version(self):
        return (
            SoulFileVersion.active_objects.filter(soul_file=self)
            .order_by("-version")
            .first()
        )


class SoulFileVersion(BaseModel):
    """An immutable version of a soul file's content."""

    soul_file = models.ForeignKey(
        SoulFile,
        on_delete=models.CASCADE,
        related_name="versions",
        help_text="The soul file this version belongs to.",
    )
    version = models.PositiveIntegerField(
        help_text="Incrementing version number (1, 2, 3, ...).",
    )
    content = models.TextField(
        blank=True,
        default="",
        help_text="The full text/markdown of the soul file at this version.",
    )
    origin = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Where this version came from: template:<key>/upload/empty/edit.",
    )
    change_note = models.CharField(
        max_length=512,
        blank=True,
        default="",
        help_text="Optional note describing the edit.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="soul_file_versions",
        help_text="Who authored this version (null if seeded/agent).",
    )

    objects = models.Manager()
    active_objects = ActiveObjectsManager()

    class Meta:
        verbose_name = "Soul File Version"
        verbose_name_plural = "Soul File Versions"
        ordering = ["-version"]
        unique_together = ("soul_file", "version")

    def __str__(self):
        return f"v{self.version} ({self.soul_file_id})"


class ResearchProjectMemory(BaseModel):
    """
    A durable project-memory snapshot (working thesis, open question, a decision)
    that persists between sessions. DARE-owned: added by the scholar or promoted
    from an accepted agent proposal.
    """

    project = models.ForeignKey(
        ResearchProject,
        on_delete=models.CASCADE,
        related_name="memory_snapshots",
        help_text="Project this memory belongs to.",
    )
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="research_memory_snapshots",
        help_text="Who captured this memory (null if from an agent).",
    )
    label = models.CharField(
        max_length=255,
        help_text="Short label, e.g. 'Working thesis'.",
    )
    detail = models.TextField(
        blank=True,
        default="",
        help_text="The memory content.",
    )
    source = models.CharField(
        max_length=32,
        blank=True,
        default=ProjectMemorySource.MANUAL,
        help_text="Where it came from: manual/proposal/agent (free-form).",
    )

    objects = models.Manager()
    active_objects = ActiveObjectsManager()

    class Meta:
        verbose_name = "Research Project Memory"
        verbose_name_plural = "Research Project Memory"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.label} ({self.project_id})"


class ResearchMemoryProposal(BaseModel):
    """
    A memory the agent proposes to keep — separates Hermes suggestions from
    accepted DARE project memory. Propose-only; the scholar accepts or rejects.
    """

    project = models.ForeignKey(
        ResearchProject,
        on_delete=models.CASCADE,
        related_name="memory_proposals",
        help_text="Project this proposal belongs to.",
    )
    run = models.ForeignKey(
        ResearchAgentRun,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="memory_proposals",
        help_text="Run that produced this proposal, if any.",
    )
    proposed_by_role = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Agent role slug that proposed it (e.g. 'scout').",
    )
    content = models.TextField(
        blank=True,
        default="",
        help_text="The proposed memory text.",
    )
    memory_type = models.CharField(
        max_length=32,
        blank=True,
        default=MemoryType.PROJECT_MEMORY,
        help_text="Scope of the proposed memory (free-form).",
    )
    status = models.CharField(
        max_length=16,
        choices=MemoryProposalStatus.choices(),
        default=MemoryProposalStatus.PROPOSED,
        help_text="Proposal lifecycle (DARE-owned).",
    )
    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="research_accepted_memory_proposals",
        help_text="Scholar who accepted it, if accepted.",
    )
    accepted_at = models.DateTimeField(null=True, blank=True)

    objects = models.Manager()
    active_objects = ActiveObjectsManager()

    class Meta:
        verbose_name = "Research Memory Proposal"
        verbose_name_plural = "Research Memory Proposals"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.proposed_by_role or 'agent'} proposal ({self.project_id})"


class ResearchChatMessage(BaseModel):
    """
    One message in a project's hands-on chat (the durable transcript). Hermes
    keeps the session memory; DARE keeps the visible record.
    """

    session = models.ForeignKey(
        ResearchSession,
        on_delete=models.CASCADE,
        related_name="chat_messages",
        help_text="Chat session this message belongs to.",
    )
    project = models.ForeignKey(
        ResearchProject,
        on_delete=models.CASCADE,
        related_name="chat_messages",
        help_text="Project this message belongs to (denormalised).",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="research_chat_messages",
        help_text="The scholar whose chat this is.",
    )
    run = models.ForeignKey(
        ResearchAgentRun,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="chat_messages",
        help_text="The run that produced this message (assistant turns).",
    )
    role = models.CharField(
        max_length=16,
        choices=ChatMessageRole.choices(),
        help_text="Author of the message: user or assistant.",
    )
    content = models.TextField(
        blank=True,
        default="",
        help_text="The message text.",
    )

    objects = models.Manager()
    active_objects = ActiveObjectsManager()

    class Meta:
        verbose_name = "Research Chat Message"
        verbose_name_plural = "Research Chat Messages"
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.role} ({self.session_id})"


class ResearchStagingItem(BaseModel):
    """
    A candidate the agent staged for review — the safe landing zone before the
    scholar promotes it to durable knowledge. Carries the full §11 provenance so
    every finding is auditable and tied to the soul-file version that governed it.
    """

    project = models.ForeignKey(
        ResearchProject,
        on_delete=models.CASCADE,
        related_name="staging_items",
        help_text="Project this candidate belongs to.",
    )
    run = models.ForeignKey(
        ResearchAgentRun,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="staging_items",
        help_text="The run that produced this item, if any.",
    )
    source_type = models.CharField(
        max_length=32,
        blank=True,
        default=StagingSourceType.SOURCE_CANDIDATE,
        help_text="Type of staged item (free-form; default 'sourceCandidate').",
    )
    # Bibliographic fields (from the §11 result contract).
    title = models.CharField(max_length=512, blank=True, default="")
    authors = models.CharField(max_length=512, blank=True, default="")
    year = models.PositiveIntegerField(null=True, blank=True)
    venue = models.CharField(max_length=255, blank=True, default="")
    doi = models.CharField(max_length=255, blank=True, default="")
    url = models.CharField(max_length=1024, blank=True, default="")
    abstract = models.TextField(blank=True, default="")
    content = models.TextField(blank=True, default="")
    # Why it matters + the agent's calibrated confidence.
    rationale = models.TextField(blank=True, default="")
    confidence = models.FloatField(
        null=True,
        blank=True,
        help_text="Agent's relevance confidence, 0.0–1.0.",
    )
    confidence_rationale = models.TextField(blank=True, default="")
    evidence_label = models.CharField(
        max_length=32,
        blank=True,
        default="",
        help_text="supporting/disputing/partial/tangential/unverifiable (free-form).",
    )
    citation_context = models.TextField(blank=True, default="")
    provenance = models.JSONField(
        default=dict,
        blank=True,
        help_text="{tool, query, retrievedAt, soulFileId, soulFileVersion, role, runId}.",
    )
    status = models.CharField(
        max_length=16,
        choices=StagingItemStatus.choices(),
        default=StagingItemStatus.STAGED,
        help_text="Review status (DARE-owned).",
    )
    rejection_reason = models.TextField(blank=True, default="")
    later_reason = models.TextField(blank=True, default="")
    critic_metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Critic pressure-test output, when 'Ask Critic' has run.",
    )
    review_metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Free-form review annotations.",
    )

    objects = models.Manager()
    active_objects = ActiveObjectsManager()

    class Meta:
        verbose_name = "Research Staging Item"
        verbose_name_plural = "Research Staging Items"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.title or 'staged item'} · {self.status} ({self.project_id})"


class ResearchKnowledgeItem(BaseModel):
    """
    Scholar-approved durable knowledge — the permanent record, promoted from a
    staging item. Only the scholar may create these (the durability gate).
    """

    project = models.ForeignKey(
        ResearchProject,
        on_delete=models.CASCADE,
        related_name="knowledge_items",
        help_text="Project this knowledge belongs to.",
    )
    source_staging_item = models.ForeignKey(
        ResearchStagingItem,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="knowledge_items",
        help_text="The staging item this was promoted from.",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="research_approved_knowledge",
        help_text="The scholar who approved it.",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    content = models.TextField(blank=True, default="")
    rationale = models.TextField(blank=True, default="")
    provenance = models.JSONField(
        default=dict,
        blank=True,
        help_text="Provenance carried over from the staging item.",
    )
    soul_file_version = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Soul-file version that governed the approved item.",
    )
    used_in = models.JSONField(
        default=list,
        blank=True,
        help_text="Sections of the project this source supports.",
    )

    objects = models.Manager()
    active_objects = ActiveObjectsManager()

    class Meta:
        verbose_name = "Research Knowledge Item"
        verbose_name_plural = "Research Knowledge Items"
        ordering = ["-created_at"]

    def __str__(self):
        return f"knowledge ({self.project_id})"


class ResearchArtifact(BaseModel):
    """
    A renderable artifact produced for a project (diagram, HTML, SVG, Excalidraw,
    …). Mirrors DARE's Artifact contract: `artifact_type` drives the frontend
    renderer; `content` is the raw payload (Mermaid/SVG/HTML text or scene JSON).
    Detected from agent replies today; DARE's own tools can write them too.
    """

    project = models.ForeignKey(
        ResearchProject,
        on_delete=models.CASCADE,
        related_name="artifacts",
        help_text="Project this artifact belongs to.",
    )
    run = models.ForeignKey(
        ResearchAgentRun,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="artifacts",
        help_text="Run that produced this artifact, if any.",
    )
    artifact_type = models.CharField(
        max_length=32,
        help_text="Renderer key: diagram | html | svg | excalidraw | … (free-form).",
    )
    title = models.CharField(max_length=255, blank=True, default="")
    content = models.TextField(
        blank=True,
        default="",
        help_text="Raw artifact payload (mermaid/svg/html text, or scene JSON).",
    )
    source = models.CharField(
        max_length=32,
        default="hermes",
        help_text="Who produced it: 'hermes' or 'dare'.",
    )
    provenance = models.JSONField(default=dict, blank=True)

    objects = models.Manager()
    active_objects = ActiveObjectsManager()

    class Meta:
        verbose_name = "Research Artifact"
        verbose_name_plural = "Research Artifacts"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.artifact_type} artifact ({self.project_id})"
