"""
Serializers for the Research app API.

Model field names are chosen so the global ``djangorestframework-camel-case``
renderer emits exactly the camelCase shape the frontend expects
(e.g. ``enabled_tools`` -> ``enabledTools``, ``standards_template`` ->
``standardsTemplate``).
"""

from rest_framework import serializers

from research.constants import (
    MemoryProposalStatus,
    SourceType,
    soul_template_content,
)
from research.models import (
    ResearchAgentRun,
    ResearchAgentToolCall,
    ResearchMemoryProposal,
    ResearchProject,
    ResearchProjectMemory,
    ResearchSource,
    SoulFile,
    SoulFileVersion,
)


class ResearchProjectSerializer(serializers.ModelSerializer):
    """Read/write serializer for a research project (list + create)."""

    pending_review_count = serializers.SerializerMethodField()
    approved_count = serializers.SerializerMethodField()
    source_count = serializers.SerializerMethodField()
    # Write-only: file metadata from the create wizard, persisted as sources.
    sources = serializers.ListField(
        child=serializers.DictField(),
        required=False,
        write_only=True,
        help_text="Optional source files to attach on create.",
    )

    class Meta:
        model = ResearchProject
        fields = [
            "id",
            "title",
            "question",
            "field",
            "status",
            "enabled_tools",
            "standards_template",
            "pending_review_count",
            "approved_count",
            "source_count",
            "sources",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "status",
            "pending_review_count",
            "approved_count",
            "source_count",
            "created_at",
            "updated_at",
        ]

    def get_pending_review_count(self, obj):
        # Wired to staging items in a later increment.
        return 0

    def get_approved_count(self, obj):
        # Wired to approved knowledge items in a later increment.
        return 0

    def get_source_count(self, obj):
        return ResearchSource.active_objects.filter(project=obj).count()

    def create(self, validated_data):
        source_files = validated_data.pop("sources", [])
        project = super().create(validated_data)
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
        soul = SoulFile.objects.create(project=project)
        SoulFileVersion.objects.create(
            soul_file=soul,
            version=1,
            content=content,
            origin=origin,
            created_by=project.user,
        )
        return project


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

    class Meta:
        model = ResearchAgentToolCall
        fields = ["id", "tool", "query", "status", "duration_ms", "result_summary"]
        read_only_fields = fields

    def get_query(self, obj):
        return (obj.arguments or {}).get("query", "")


class ResearchAgentRunSerializer(serializers.ModelSerializer):
    """A delegated run with its tool calls (the Runs/activity record)."""

    tools = serializers.JSONField(source="allowed_tools", read_only=True)
    cost = serializers.FloatField(read_only=True)
    ran_at = serializers.SerializerMethodField()
    staged_count = serializers.SerializerMethodField()
    tool_calls = serializers.SerializerMethodField()

    class Meta:
        model = ResearchAgentRun
        fields = [
            "id",
            "role",
            "mode",
            "task",
            "status",
            "soul_file_version",
            "tools",
            "staged_count",
            "cost",
            "started_at",
            "completed_at",
            "ran_at",
            "hermes_run_id",
            "tool_calls",
        ]
        read_only_fields = fields

    def get_ran_at(self, obj):
        return obj.started_at or obj.created_at

    def get_staged_count(self, obj):
        # Wired to staging items in a later increment.
        return 0

    def get_tool_calls(self, obj):
        calls = ResearchAgentToolCall.active_objects.filter(run=obj).order_by(
            "created_at"
        )
        return ResearchAgentToolCallSerializer(calls, many=True).data


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
        fields = ["id", "role", "content", "memory_type", "status", "proposed_at"]
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

    class Meta(ResearchProjectSerializer.Meta):
        fields = ResearchProjectSerializer.Meta.fields + [
            "runs",
            "soul_file",
            "project_memory",
            "memory_proposals",
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
        soul = SoulFile.active_objects.filter(project=obj).first()
        if not soul:
            return None
        version = soul.current_version()
        if not version:
            return None
        return {
            "id": soul.id,
            "version": version.version,
            "content": version.content,
            "origin": version.origin,
            "updatedAt": version.created_at,
        }

    def get_project_memory(self, obj):
        memory = ResearchProjectMemory.active_objects.filter(
            project=obj
        ).order_by("-created_at")
        return ResearchProjectMemorySerializer(memory, many=True).data

    def get_memory_proposals(self, obj):
        proposals = ResearchMemoryProposal.active_objects.filter(
            project=obj, status=MemoryProposalStatus.PROPOSED
        ).order_by("-created_at")
        return ResearchMemoryProposalSerializer(proposals, many=True).data
