"""
Research Mode models.

DARE owns the durable record: projects, standards (SOUL.md), approved memory
and procedures, chat threads, staged and approved findings, and the audit of
every agent run. The agent runtime (Hermes) only reads a rendering of it.
"""

from django.conf import settings
from django.db import models
from django.db.models import Q

from common.managers import ActiveObjectsManager
from common.models import BaseModel
from research.constants import (
    AgentRole,
    AgentRunStatus,
    AgentToolCallStatus,
    ChatMessageRole,
    MemoryLayer,
    MemoryProposalOrigin,
    MemoryProposalStatus,
    MemoryType,
    ProjectKind,
    ProjectMemorySource,
    ResearchProjectStatus,
    ResearchSessionMode,
    ResearchSessionStatus,
    SourceType,
    StagingItemStatus,
    StagingSourceType,
    ThreadEventType,
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
    kind = models.CharField(
        max_length=24,
        choices=ProjectKind.choices(),
        default=ProjectKind.GENERAL,
        help_text="What sort of question this is; sets which sources count and how fresh.",
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
    hermes_model = models.CharField(
        max_length=128,
        blank=True,
        default="",
        help_text=(
            "Model pinned into this project's Hermes profile, e.g. "
            "'claude-haiku-4.5' for a cheap project or 'claude-sonnet-5' for a "
            "demanding one. Blank uses settings.HERMES_PROFILE_MODEL. Never "
            "leave the runtime to choose: an unpinned profile resolves to the "
            "provider's flagship."
        ),
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
    A Hermes session: a chat thread (mode ``chat``), or the project's single
    session that its Scout, Critic and artifact runs hang off.
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
    followup_pending = models.BooleanField(
        default=False,
        help_text=(
            "A chat thread has events (finished Scouts, review notes) the agent "
            "has not reacted to yet, and a turn was already running when they "
            "arrived. The next turn to finish enqueues the follow-up."
        ),
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
    def create_thread(cls, project, user, title=""):
        """A new chat thread: its own persistent Hermes session."""
        thread = cls.objects.create(
            project=project, user=user, mode=ResearchSessionMode.CHAT, title=title
        )
        thread.hermes_session_id = f"dare-proj{project.id}-thread{thread.id}"
        thread.save(update_fields=["hermes_session_id", "updated_at"])
        return thread

    @classmethod
    def get_or_create_scout_session(cls, project, user):
        """The project's one persistent scout session (stable hermes_session_id)."""
        return cls._get_or_create(project, user, ResearchSessionMode.SCOUT)

    @classmethod
    def get_or_create_artifact_session(cls, project, user):
        """The project's one persistent artifact session (stable hermes_session_id)."""
        return cls._get_or_create(project, user, ResearchSessionMode.ARTIFACT)


class ResearchAgentRun(BaseModel):
    """One agent run on Hermes, and the audit record of what it was sent and did."""

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
    raw_output = models.TextField(
        blank=True,
        default="",
        help_text="The agent's exact final response text as Hermes returned it, "
        "before DARE parsing. Observability/audit only; may be backfilled from "
        "Hermes on demand while its run record is still retained.",
    )
    prompt = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Exactly what the run was sent: {instructions, input, history, "
            "files: {SOUL.md, MEMORY.md, USER.md, skills}}."
        ),
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
    thread = models.ForeignKey(
        ResearchSession,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="thread_runs",
        help_text=(
            "The chat thread this run belongs to or was launched from — chat "
            "turns, and Scouts the thread started. Review decisions on their "
            "findings flow back into this thread."
        ),
    )
    learning = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "What the run did because of the scholar's approved memory: "
            "{applied: [{memory, effect}], setAside: [{title, url, reason}]}."
        ),
    )
    cancellation_requested_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When an authorized user first requested cancellation.",
    )
    cancellation_requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="requested_research_run_cancellations",
        help_text="The authorized user who first requested cancellation.",
    )
    cancellation_attempt_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of claimed upstream stop attempts.",
    )
    cancellation_last_attempt_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the latest upstream stop attempt was claimed.",
    )
    cancellation_acknowledged_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When Hermes acknowledged a stop request with 'stopping'.",
    )
    cancellation_confirmed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When terminal Hermes truth confirmed 'cancelled'.",
    )
    cancellation_stop_http_status = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Safe HTTP status from the latest Hermes stop response.",
    )
    cancellation_error_code = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Safe machine-readable result of the latest cancellation attempt.",
    )
    cancellation_error_detail = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Safe fixed-text detail for the latest cancellation attempt.",
    )

    objects = models.Manager()
    active_objects = ActiveObjectsManager()

    class Meta:
        verbose_name = "Research Agent Run"
        verbose_name_plural = "Research Agent Runs"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.role or 'run'} · {self.status} ({self.project_id})"

    @property
    def runtime_session_id(self):
        """The Hermes session this run executes in.

        A chat turn continues its thread's session; every other run gets a
        fresh one so it starts from the profile files, not a long transcript.
        """
        if self.role == AgentRole.CHAT and self.thread_id:
            return self.thread.hermes_session_id
        return f"{self.session.hermes_session_id}-r{self.id}"


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

    @classmethod
    def current_for(cls, project):
        """The project's current SoulFileVersion, or None."""
        soul = cls.active_objects.filter(project=project).first()
        return soul.current_version() if soul else None

    @classmethod
    def current_label(cls, project):
        """'v3' for the version governing ``project`` now, for run provenance."""
        version = cls.current_for(project)
        return f"v{version.version}" if version else ""


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

    @classmethod
    def entries_for(cls, project):
        """The project's approved memory, oldest first, one text per entry."""
        rows = cls.active_objects.filter(project=project).order_by("created_at")
        return [text for text in ((m.detail or m.label).strip() for m in rows) if text]


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
    layer = models.CharField(
        max_length=16,
        choices=MemoryLayer.choices(),
        default=MemoryLayer.PROJECT,
        help_text="Where it lands if approved; the scholar may change it.",
    )
    origin = models.CharField(
        max_length=24,
        choices=MemoryProposalOrigin.choices(),
        default=MemoryProposalOrigin.LEGACY,
        help_text="Whether the agent proposed it mid-conversation or on reflection.",
    )
    payload = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "The runtime's staged write, verbatim: a memory op "
            "({action, target, content, old_text}) or a skill op "
            "({action, name, content, old_string, new_string, …})."
        ),
    )
    pending_ref = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Id of the runtime's staged write (<HERMES_HOME>/pending/…).",
    )
    thread = models.ForeignKey(
        ResearchSession,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="memory_proposals",
        help_text="Thread whose conversation produced it, when known.",
    )
    why = models.TextField(
        blank=True,
        default="",
        help_text="Why the agent proposed it, in the scholar's terms.",
    )
    evidence = models.JSONField(
        default=list,
        blank=True,
        help_text="Review notes it drew on: [{itemId, title, decision, note}].",
    )
    decided_at = models.DateTimeField(null=True, blank=True)

    objects = models.Manager()
    active_objects = ActiveObjectsManager()

    class Meta:
        verbose_name = "Research Memory Proposal"
        verbose_name_plural = "Research Memory Proposals"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "pending_ref"],
                condition=~models.Q(pending_ref=""),
                name="research_proposal_unique_pending_ref",
            )
        ]

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
    event_type = models.CharField(
        max_length=32,
        blank=True,
        default="",
        choices=ThreadEventType.choices(),
        help_text="For role=event rows: what happened.",
    )
    payload = models.JSONField(
        default=dict,
        blank=True,
        help_text="For role=event rows: the structured facts behind the event.",
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
    finding = models.TextField(
        blank=True,
        default="",
        help_text="What the source establishes for the Scout's task, with its numbers.",
    )
    rationale = models.TextField(
        blank=True, default="", help_text="Why the finding matters and its limits."
    )
    confidence = models.FloatField(
        null=True,
        blank=True,
        help_text="Agent's relevance confidence, 0.0–1.0.",
    )
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


class ResearchThesisSource(BaseModel):
    """
    A direct, typed link between a thesis (ResearchProjectMemory) and a durable
    source (ResearchKnowledgeItem) that bears on it.

    The shared project FK only says "same project"; it cannot express that a
    given source *supports* or *disputes* a given thesis. This is that missing
    edge. `stance` says how the source bears on the thesis (supporting /
    disputing / partial / …, free-form like evidence_label); it defaults to the
    source's own evidence label when set via the API. Only the scholar creates
    these, and they drive the OKF bundle's "Supported by / Disputed by" links.
    """

    thesis = models.ForeignKey(
        ResearchProjectMemory,
        on_delete=models.CASCADE,
        related_name="source_links",
        help_text="The thesis / open question / decision.",
    )
    source = models.ForeignKey(
        ResearchKnowledgeItem,
        on_delete=models.CASCADE,
        related_name="thesis_links",
        help_text="The durable source that bears on the thesis.",
    )
    stance = models.CharField(
        max_length=32,
        blank=True,
        default="",
        help_text="How the source bears on the thesis: supporting/disputing/partial/… (free-form).",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="research_thesis_source_links",
        help_text="The scholar who established the link.",
    )

    objects = models.Manager()
    active_objects = ActiveObjectsManager()

    class Meta:
        verbose_name = "Research Thesis-Source Link"
        verbose_name_plural = "Research Thesis-Source Links"
        ordering = ["-created_at"]
        unique_together = ("thesis", "source")

    def __str__(self):
        return f"thesis {self.thesis_id} -[{self.stance or 'related'}]-> source {self.source_id}"


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


class ResearcherProfile(BaseModel):
    """
    What DARE knows about a scholar — the person, not any one project.

    The runtime keeps two memory files per profile, and they have different
    natural owners: MEMORY.md is about a project, USER.md is about the person.
    Because a Hermes profile holds both, scoping profiles per project (which
    MEMORY.md needs) left USER.md re-learned from scratch in every new project.

    So DARE owns the person. This record is rendered into the USER.md of every
    profile the scholar owns, and anything the agent learns about them in one
    project is absorbed back here and propagated to the rest. Learn once, known
    everywhere — without merging the projects' memories, which must stay apart.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="researcher_profile",
        help_text="The scholar this describes.",
    )
    content = models.TextField(
        blank=True,
        default="",
        help_text=(
            "The scholar's durable facts, one per entry, in the runtime's own "
            "'§'-separated form so it can be written to USER.md verbatim."
        ),
    )

    objects = models.Manager()
    active_objects = ActiveObjectsManager()

    class Meta:
        verbose_name = "Researcher Profile"
        verbose_name_plural = "Researcher Profiles"

    def __str__(self):
        return f"Researcher profile ({self.user_id})"

    def entries(self):
        return [e.strip() for e in (self.content or "").split("§") if e.strip()]

    @classmethod
    def entries_for(cls, user):
        profile = cls.active_objects.filter(user=user).first()
        return profile.entries() if profile else []


class ResearchProcedure(BaseModel):
    """
    A learned method ("verify a claim your way"), one row per version.

    The agent proposes a procedure as a runtime skill write; once the scholar
    approves it, DARE stores the full SKILL.md here and renders it into the
    profiles it applies to. ``project`` null means researcher-wide — every
    project the scholar owns gets it.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="research_procedures",
        help_text="The scholar this method belongs to.",
    )
    project = models.ForeignKey(
        ResearchProject,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="procedures",
        help_text="Set for a project-only procedure; null means researcher-wide.",
    )
    name = models.CharField(
        max_length=64,
        help_text="Skill name (directory name in the runtime's skills tree).",
    )
    description = models.CharField(max_length=512, blank=True, default="")
    content = models.TextField(help_text="The full SKILL.md at this version.")
    version = models.PositiveIntegerField(default=1)
    is_current = models.BooleanField(
        default=True, help_text="The version rendered into profiles."
    )
    proposal = models.ForeignKey(
        ResearchMemoryProposal,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="procedures",
        help_text="The approved proposal that produced this version.",
    )

    objects = models.Manager()
    active_objects = ActiveObjectsManager()

    class Meta:
        verbose_name = "Research Procedure"
        verbose_name_plural = "Research Procedures"
        ordering = ["name", "-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "project", "name", "version"],
                name="research_procedure_unique_version",
                nulls_distinct=False,
            )
        ]

    def __str__(self):
        return f"{self.name} v{self.version} ({self.user_id})"

    @classmethod
    def current_for(cls, project):
        """Current versions that reach ``project``: its own and researcher-wide."""
        return cls.active_objects.filter(
            Q(project=project) | Q(project__isnull=True),
            user=project.user,
            is_current=True,
        )
