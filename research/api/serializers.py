"""
Serializers for the Research app API.

Model field names are chosen so the global ``djangorestframework-camel-case``
renderer emits exactly the camelCase shape the frontend expects
(e.g. ``enabled_tools`` -> ``enabledTools``, ``standards_template`` ->
``standardsTemplate``).
"""

from django.db.models import Sum
from rest_framework import serializers

from research.constants import (
    LIVE_RUN_STATUSES,
    AgentRole,
    AgentRunStatus,
    ChatMessageRole,
    MemoryProposalStatus,
    ProjectMemorySource,
    SoulFileOrigin,
    SourceType,
    StagingItemStatus,
    soul_template_content,
)
from research.models import (
    ResearchAgentRun,
    ResearchAgentToolCall,
    ResearchArtifact,
    ResearchChatMessage,
    ResearchKnowledgeItem,
    ResearchMemoryProposal,
    ResearchProject,
    ResearchProjectMemory,
    ResearchSession,
    ResearchSource,
    ResearchStagingItem,
    SoulFile,
    SoulFileVersion,
)
from research.services.learning_service import proposal_payload
from research.services.thread_service import scout_summary


def soul_payload(project):
    """The project's current SOUL.md version, or None."""
    version = SoulFile.current_for(project)
    if version is None:
        return None
    return {
        "id": version.soul_file_id,
        "version": version.version,
        "content": version.content,
        "origin": version.origin,
        "updatedAt": version.created_at,
    }


class ResearchProjectSerializer(serializers.ModelSerializer):
    """Read/write serializer for a research project (list + create)."""

    pending_review_count = serializers.SerializerMethodField()
    approved_count = serializers.SerializerMethodField()
    source_count = serializers.SerializerMethodField()
    last_run_at = serializers.SerializerMethodField()
    # Write-only: file metadata from the create wizard, persisted as sources.
    sources = serializers.ListField(
        child=serializers.DictField(),
        required=False,
        write_only=True,
        help_text="Optional source files to attach on create.",
    )
    project_memory = serializers.ListField(
        child=serializers.CharField(max_length=600, trim_whitespace=True),
        required=False,
        write_only=True,
        max_length=40,
        help_text="The project's MEMORY.md entries written in the wizard.",
    )
    soul_content = serializers.CharField(
        required=False,
        write_only=True,
        allow_blank=True,
        max_length=20000,
        help_text="SOUL.md as built in the Standards step; replaces the template.",
    )

    class Meta:
        model = ResearchProject
        fields = [
            "id",
            "title",
            "question",
            "field",
            "kind",
            "status",
            "enabled_tools",
            "standards_template",
            "pending_review_count",
            "approved_count",
            "source_count",
            "sources",
            "project_memory",
            "soul_content",
            "created_at",
            "updated_at",
            "last_run_at",
        ]
        read_only_fields = [
            "id",
            "status",
            "pending_review_count",
            "approved_count",
            "source_count",
            "created_at",
            "updated_at",
            "last_run_at",
        ]

    def get_last_run_at(self, obj):
        """When the project's newest agent run started (annotated by the view)."""
        return getattr(obj, "last_run_at", None)

    def get_pending_review_count(self, obj):
        return ResearchStagingItem.active_objects.filter(
            project=obj, status=StagingItemStatus.STAGED
        ).count()

    def get_approved_count(self, obj):
        return ResearchKnowledgeItem.active_objects.filter(project=obj).count()

    def get_source_count(self, obj):
        return ResearchSource.active_objects.filter(project=obj).count()

    def create(self, validated_data):
        source_files = validated_data.pop("sources", [])
        soul_override = validated_data.pop("soul_content", None)
        scope = [line for line in validated_data.pop("project_memory", []) if line]
        project = super().create(validated_data)
        # Facts about the inquiry live in project memory, not in SOUL.md: the
        # soul file is who the agent is, and Hermes keeps the two apart.
        for line in scope:
            ResearchProjectMemory.objects.create(
                project=project,
                added_by=project.user,
                label=line.split(":", 1)[0][:255] if ":" in line[:60] else "Scope",
                detail=line,
                source=ProjectMemorySource.MANUAL,
            )
        for item in source_files:
            ResearchSource.objects.create(
                project=project,
                added_by=project.user,
                name=item.get("name", ""),
                kind=item.get("kind", ""),
                size_label=item.get("size_label", ""),
                source_type=SourceType.UPLOAD,
            )
        # Every project starts with a versioned soul file (v1) seeded from the
        # chosen standards template (or empty for a custom/blank start).
        content, origin = soul_template_content(project.standards_template)
        if soul_override is not None and soul_override.strip() != content.strip():
            content, origin = soul_override.strip(), SoulFileOrigin.EDIT
        soul = SoulFile.objects.create(project=project)
        SoulFileVersion.objects.create(
            soul_file=soul,
            version=1,
            content=content,
            origin=origin,
            created_by=project.user,
        )
        return project


class ProjectMemoryDocumentSerializer(serializers.Serializer):
    """A project's MEMORY.md as the scholar edits it — one entry per item."""

    entries = serializers.ListField(
        child=serializers.CharField(
            max_length=600, trim_whitespace=True, allow_blank=True
        ),
        max_length=40,
    )


class ResearchSourceSerializer(serializers.ModelSerializer):
    """A source in the project's library."""

    class Meta:
        model = ResearchSource
        fields = [
            "id",
            "name",
            "kind",
            "size_label",
            "page_count",
            "source_type",
            "title",
            "authors",
            "year",
            "venue",
            "doi",
            "url",
            "created_at",
        ]
        read_only_fields = fields


class ResearchAgentToolCallSerializer(serializers.ModelSerializer):
    """A single tool invocation within a run (audit)."""

    query = serializers.SerializerMethodField()
    url = serializers.SerializerMethodField()

    class Meta:
        model = ResearchAgentToolCall
        fields = [
            "id",
            "tool",
            "query",
            "url",
            "status",
            "duration_ms",
            "result_tokens",
            "result_summary",
            "error",
        ]
        read_only_fields = fields

    def get_query(self, obj):
        return (obj.arguments or {}).get("query", "")

    def get_url(self, obj):
        return (obj.arguments or {}).get("url", "")


class ResearchAgentRunSerializer(serializers.ModelSerializer):
    """A run in the project's run list."""

    tools = serializers.JSONField(source="allowed_tools", read_only=True)
    cost = serializers.FloatField(read_only=True)
    ran_at = serializers.SerializerMethodField()
    staged_count = serializers.SerializerMethodField()
    tool_call_count = serializers.SerializerMethodField()
    cancellation = serializers.SerializerMethodField()
    focus = serializers.SerializerMethodField()
    coverage = serializers.SerializerMethodField()

    class Meta:
        model = ResearchAgentRun
        fields = [
            "id",
            "role",
            "mode",
            "task",
            "status",
            "status_detail",
            "soul_file_version",
            "tools",
            "staged_count",
            "cost",
            "usage",
            "started_at",
            "completed_at",
            "ran_at",
            "hermes_run_id",
            "thread",
            "learning",
            "focus",
            "tool_call_count",
            "coverage",
            "cancellation",
        ]
        read_only_fields = fields

    def get_coverage(self, obj):
        """How wide a Scout looked: searches run and pages read."""
        if obj.role != AgentRole.SCOUT:
            return None
        searches = reads = 0
        for tool in ResearchAgentToolCall.active_objects.filter(run=obj).values_list(
            "tool", flat=True
        ):
            name = tool.removeprefix("mcp__dare__")
            if name == "fetch_page":
                reads += 1
            elif name in ("web_search", "search_papers") or "__" in name:
                searches += 1
        return {"searches": searches, "reads": reads}

    def get_focus(self, obj):
        return (obj.selected_context or {}).get("focus") or ""

    def get_ran_at(self, obj):
        return obj.started_at or obj.created_at

    def get_staged_count(self, obj):
        return ResearchStagingItem.active_objects.filter(run=obj).count()

    def get_tool_call_count(self, obj):
        return ResearchAgentToolCall.active_objects.filter(run=obj).count()

    def get_cancellation(self, obj):
        if obj.cancellation_requested_at is None:
            return None
        if obj.cancellation_confirmed_at is not None:
            state = "confirmed"
        elif obj.cancellation_acknowledged_at is not None:
            state = "acknowledged"
        else:
            state = "unconfirmed"
        return {
            "state": state,
            "requested_at": obj.cancellation_requested_at,
            "last_attempt_at": obj.cancellation_last_attempt_at,
            "attempt_count": obj.cancellation_attempt_count,
            "acknowledged_at": obj.cancellation_acknowledged_at,
            "confirmed_at": obj.cancellation_confirmed_at,
            "stop_http_status": obj.cancellation_stop_http_status,
            "error_code": obj.cancellation_error_code,
            "error_detail": obj.cancellation_error_detail,
        }


class ResearchAgentRunDetailSerializer(ResearchAgentRunSerializer):
    """One run's full audit: the list fields plus what it was sent, every tool
    call and its exact final reply."""

    tool_calls = serializers.SerializerMethodField()
    prompt = serializers.SerializerMethodField()

    class Meta(ResearchAgentRunSerializer.Meta):
        fields = ResearchAgentRunSerializer.Meta.fields + [
            "tool_calls",
            "prompt",
            "raw_output",
        ]
        read_only_fields = fields

    def get_tool_calls(self, obj):
        calls = ResearchAgentToolCall.active_objects.filter(run=obj).order_by(
            "created_at"
        )
        return ResearchAgentToolCallSerializer(calls, many=True).data

    def get_prompt(self, obj):
        # Runs from before prompts were recorded have none.
        return obj.prompt or None


class ResearchProjectMemorySerializer(serializers.ModelSerializer):
    """A durable project-memory snapshot."""

    captured_at = serializers.DateTimeField(source="created_at", read_only=True)

    class Meta:
        model = ResearchProjectMemory
        fields = ["id", "label", "detail", "source", "captured_at"]
        read_only_fields = fields


class ResearchMemoryProposalSerializer(serializers.ModelSerializer):
    """An agent-proposed memory awaiting the scholar's decision."""

    role = serializers.CharField(source="proposed_by_role", read_only=True)
    proposed_at = serializers.DateTimeField(source="created_at", read_only=True)

    class Meta:
        model = ResearchMemoryProposal
        fields = [
            "id",
            "role",
            "content",
            "memory_type",
            "layer",
            "origin",
            "why",
            "evidence",
            "thread",
            "status",
            "proposed_at",
        ]
        read_only_fields = fields


class ResearchChatMessageSerializer(serializers.ModelSerializer):
    """One thread row: a scholar or agent message, or a DARE event."""

    class Meta:
        model = ResearchChatMessage
        fields = ["id", "role", "content", "event_type", "payload", "run", "created_at"]
        read_only_fields = fields


class ResearchThreadSerializer(serializers.ModelSerializer):
    """A chat thread in the thread list."""

    preview = serializers.SerializerMethodField()
    message_count = serializers.SerializerMethodField()

    class Meta:
        model = ResearchSession
        fields = [
            "id",
            "title",
            "preview",
            "message_count",
            "last_run_at",
            "created_at",
        ]
        read_only_fields = fields

    def get_preview(self, obj):
        last = (
            ResearchChatMessage.active_objects.filter(
                session=obj, role__in=[ChatMessageRole.USER, ChatMessageRole.ASSISTANT]
            )
            .order_by("-created_at")
            .values_list("content", flat=True)
            .first()
        )
        return (last or "")[:140]

    def get_message_count(self, obj):
        return ResearchChatMessage.active_objects.filter(
            session=obj, role__in=[ChatMessageRole.USER, ChatMessageRole.ASSISTANT]
        ).count()


def thread_detail(thread):
    """The thread view payload: transcript, Scouts, proposals, live state."""
    messages = ResearchChatMessage.active_objects.filter(session=thread).order_by(
        "created_at"
    )
    scouts = ResearchAgentRun.active_objects.filter(
        thread=thread, role=AgentRole.SCOUT
    ).order_by("created_at")
    last_turn = (
        ResearchAgentRun.active_objects.filter(thread=thread, role=AgentRole.CHAT)
        .order_by("-created_at")
        .first()
    )

    def turn(states):
        if last_turn is None or last_turn.status not in states:
            return None
        return {
            "runId": last_turn.id,
            "followup": bool((last_turn.selected_context or {}).get("followup")),
            "status": last_turn.status,
        }

    proposals = ResearchMemoryProposal.active_objects.filter(thread=thread).order_by(
        "created_at"
    )
    return {
        "thread": ResearchThreadSerializer(thread).data,
        "messages": ResearchChatMessageSerializer(messages, many=True).data,
        "scouts": [scout_summary(run) for run in scouts],
        "proposals": {p.id: proposal_payload(p) for p in proposals},
        "cost": float(
            ResearchAgentRun.active_objects.filter(thread=thread).aggregate(
                total=Sum("cost")
            )["total"]
            or 0
        ),
        "activeTurn": turn(LIVE_RUN_STATUSES),
        "lastTurnFailed": turn({AgentRunStatus.FAILED, AgentRunStatus.OUTCOME_UNKNOWN}),
    }


class ResearchStagingItemSerializer(serializers.ModelSerializer):
    """A finding a Scout staged, with the task it was found for."""

    thread_id = serializers.SerializerMethodField()
    focus = serializers.SerializerMethodField()
    task = serializers.SerializerMethodField()

    class Meta:
        model = ResearchStagingItem
        fields = [
            "id",
            "title",
            "authors",
            "year",
            "venue",
            "doi",
            "url",
            "abstract",
            "finding",
            "rationale",
            "confidence",
            "evidence_label",
            "citation_context",
            "provenance",
            "status",
            "rejection_reason",
            "later_reason",
            "critic_metadata",
            "review_metadata",
            "thread_id",
            "focus",
            "task",
            "created_at",
        ]
        read_only_fields = fields

    def get_thread_id(self, obj):
        return obj.run.thread_id if obj.run else None

    def get_focus(self, obj):
        return (obj.run.selected_context or {}).get("focus", "") if obj.run else ""

    def get_task(self, obj):
        return obj.run.task if obj.run else ""


class ResearchKnowledgeItemSerializer(serializers.ModelSerializer):
    """Approved knowledge: the finding it was approved from, in full, plus when."""

    class Meta:
        model = ResearchKnowledgeItem
        fields = ["id", "used_in", "approved_at", "created_at"]
        read_only_fields = fields

    def to_representation(self, obj):
        staged = obj.source_staging_item
        evidence = ResearchStagingItemSerializer(staged).data if staged else {}
        return {
            **evidence,
            **super().to_representation(obj),
            "staging_item_id": staged.id if staged else None,
        }


class ResearchArtifactSerializer(serializers.ModelSerializer):
    """A renderable artifact — artifactType drives the FE renderer registry."""

    class Meta:
        model = ResearchArtifact
        fields = [
            "id",
            "artifact_type",
            "title",
            "content",
            "source",
            "provenance",
            "created_at",
        ]
        read_only_fields = fields


class ResearchProjectDetailSerializer(ResearchProjectSerializer):
    """
    Single-project payload for the workspace (GET /api/research/projects/{id}/).

    The aggregation point for the whole workspace: it nests the project's agent
    runs today and will grow to nest the active soul file, sources, staging/review
    items and memory as those models land — so the frontend can load everything
    the workspace needs from one call.
    """

    runs = serializers.SerializerMethodField()
    sources = serializers.SerializerMethodField()
    soul_file = serializers.SerializerMethodField()
    project_memory = serializers.SerializerMethodField()
    memory_proposals = serializers.SerializerMethodField()
    review_items = serializers.SerializerMethodField()
    knowledge_items = serializers.SerializerMethodField()
    artifacts = serializers.SerializerMethodField()

    class Meta(ResearchProjectSerializer.Meta):
        fields = ResearchProjectSerializer.Meta.fields + [
            "runs",
            "soul_file",
            "project_memory",
            "memory_proposals",
            "review_items",
            "knowledge_items",
            "artifacts",
        ]

    def get_runs(self, obj):
        runs = ResearchAgentRun.active_objects.filter(project=obj).order_by(
            "-created_at"
        )
        return ResearchAgentRunSerializer(runs, many=True).data

    def get_sources(self, obj):
        sources = ResearchSource.active_objects.filter(project=obj).order_by(
            "-created_at"
        )
        return ResearchSourceSerializer(sources, many=True).data

    def get_soul_file(self, obj):
        return soul_payload(obj)

    def get_project_memory(self, obj):
        memory = ResearchProjectMemory.active_objects.filter(project=obj).order_by(
            "-created_at"
        )
        return ResearchProjectMemorySerializer(memory, many=True).data

    def get_memory_proposals(self, obj):
        proposals = ResearchMemoryProposal.active_objects.filter(
            project=obj, status=MemoryProposalStatus.PROPOSED
        ).order_by("-created_at")
        return ResearchMemoryProposalSerializer(proposals, many=True).data

    def get_review_items(self, obj):
        items = (
            ResearchStagingItem.active_objects.filter(project=obj)
            .select_related("run")
            .order_by("-created_at")
        )
        return ResearchStagingItemSerializer(items, many=True).data

    def get_knowledge_items(self, obj):
        items = (
            ResearchKnowledgeItem.active_objects.filter(project=obj)
            .select_related("source_staging_item__run")
            .order_by("-created_at")
        )
        return ResearchKnowledgeItemSerializer(items, many=True).data

    def get_artifacts(self, obj):
        items = ResearchArtifact.active_objects.filter(project=obj).order_by(
            "-created_at"
        )
        return ResearchArtifactSerializer(items, many=True).data
