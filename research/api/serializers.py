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
    StagingItemStatus,
    soul_template_content,
)
from research.models import (
    ResearchAgentRun,
    ResearchAgentToolCall,
    ResearchChatMessage,
    ResearchKnowledgeItem,
    ResearchMemoryProposal,
    ResearchProject,
    ResearchProjectMemory,
    ResearchSource,
    ResearchStagingItem,
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
        return ResearchStagingItem.active_objects.filter(
            project=obj, status=StagingItemStatus.STAGED
        ).count()

    def get_approved_count(self, obj):
        return ResearchKnowledgeItem.active_objects.filter(project=obj).count()

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
            "status_detail",
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
        return ResearchStagingItem.active_objects.filter(run=obj).count()

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


class ResearchChatMessageSerializer(serializers.ModelSerializer):
    """One message in a project's chat transcript."""

    class Meta:
        model = ResearchChatMessage
        fields = ["id", "role", "content", "created_at"]
        read_only_fields = fields


class ResearchStagingItemSerializer(serializers.ModelSerializer):
    """A staged review candidate — the canonical §11 shape (camelCased)."""

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
            "rationale",
            "confidence",
            "confidence_rationale",
            "evidence_label",
            "citation_context",
            "provenance",
            "status",
            "rejection_reason",
            "later_reason",
            "critic_metadata",
            "review_metadata",
            "created_at",
        ]
        read_only_fields = fields


class ResearchKnowledgeItemSerializer(serializers.ModelSerializer):
    """
    Approved durable knowledge. Bibliographic display fields are read from the
    source staging item (the contract references it rather than duplicating it).
    """

    title = serializers.SerializerMethodField()
    authors = serializers.SerializerMethodField()
    year = serializers.SerializerMethodField()
    venue = serializers.SerializerMethodField()
    url = serializers.SerializerMethodField()
    confidence = serializers.SerializerMethodField()
    evidence_label = serializers.SerializerMethodField()
    citation_context = serializers.SerializerMethodField()

    class Meta:
        model = ResearchKnowledgeItem
        fields = [
            "id",
            "title",
            "authors",
            "year",
            "venue",
            "url",
            "rationale",
            "confidence",
            "evidence_label",
            "citation_context",
            "provenance",
            "soul_file_version",
            "used_in",
            "approved_at",
            "created_at",
        ]
        read_only_fields = fields

    def _staged(self, obj):
        return obj.source_staging_item

    def get_title(self, obj):
        s = self._staged(obj)
        return s.title if s else ""

    def get_authors(self, obj):
        s = self._staged(obj)
        return s.authors if s else ""

    def get_year(self, obj):
        s = self._staged(obj)
        return s.year if s else None

    def get_venue(self, obj):
        s = self._staged(obj)
        return s.venue if s else ""

    def get_url(self, obj):
        s = self._staged(obj)
        return s.url if s else ""

    def get_confidence(self, obj):
        s = self._staged(obj)
        return s.confidence if s else None

    def get_evidence_label(self, obj):
        s = self._staged(obj)
        return s.evidence_label if s else ""

    def get_citation_context(self, obj):
        s = self._staged(obj)
        return s.citation_context if s else ""


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

    class Meta(ResearchProjectSerializer.Meta):
        fields = ResearchProjectSerializer.Meta.fields + [
            "runs",
            "soul_file",
            "project_memory",
            "memory_proposals",
            "review_items",
            "knowledge_items",
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
        items = ResearchStagingItem.active_objects.filter(project=obj).order_by(
            "-created_at"
        )
        return ResearchStagingItemSerializer(items, many=True).data

    def get_knowledge_items(self, obj):
        items = ResearchKnowledgeItem.active_objects.filter(project=obj).order_by(
            "-created_at"
        )
        return ResearchKnowledgeItemSerializer(items, many=True).data
