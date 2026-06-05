from rest_framework import serializers

# isort: off
from research.models import (
    ResearchAgentOutputDestination,
    ResearchAgentRun,
    ResearchAgentRunStatus,
    ResearchAgentToolCall,
    ResearchAgentToolCallStatus,
    ResearchKnowledgeItem,
    ResearchProject,
    ResearchProjectStatus,
    ResearchAgentRole,
    ResearchReviewStatus,
    ResearchSoulFile,
    ResearchSoulFileVersion,
    ResearchSource,
    ResearchStagingItem,
)

# isort: on


class ResearchSoulFileVersionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ResearchSoulFileVersion
        fields = [
            "id",
            "soul_file",
            "version_number",
            "title",
            "description",
            "template_key",
            "body",
            "created_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class ResearchSoulFileSerializer(serializers.ModelSerializer):
    body = serializers.CharField(
        required=False,
        allow_blank=True,
        write_only=True,
    )
    current_version = ResearchSoulFileVersionSerializer(read_only=True)
    version_count = serializers.SerializerMethodField()

    class Meta:
        model = ResearchSoulFile
        fields = [
            "id",
            "title",
            "description",
            "template_key",
            "is_default",
            "body",
            "current_version",
            "version_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "current_version",
            "version_count",
            "created_at",
            "updated_at",
        ]

    def get_version_count(self, obj: ResearchSoulFile) -> int:
        return obj.versions.filter(is_deleted=False, is_active=True).count()


class ResearchAgentToolCallSerializer(serializers.ModelSerializer):
    class Meta:
        model = ResearchAgentToolCall
        fields = [
            "id",
            "run",
            "tool_name",
            "status",
            "input_summary",
            "output_summary",
            "error_message",
            "cost_usd",
            "started_at",
            "completed_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class ResearchAgentRunSerializer(serializers.ModelSerializer):
    tool_calls = ResearchAgentToolCallSerializer(many=True, read_only=True)
    soul_file_title = serializers.SerializerMethodField()
    soul_file_version_number = serializers.SerializerMethodField()

    class Meta:
        model = ResearchAgentRun
        fields = [
            "id",
            "run_id",
            "project",
            "role",
            "status",
            "task",
            "selected_context",
            "allowed_tools",
            "capability_policy",
            "output_destination",
            "soul_file_version",
            "soul_file_title",
            "soul_file_version_number",
            "external_run_id",
            "status_message",
            "error_message",
            "cost_usd",
            "queued_at",
            "started_at",
            "completed_at",
            "tool_calls",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_soul_file_title(self, obj: ResearchAgentRun) -> str:
        if obj.soul_file_version_id is None:
            return ""
        return obj.soul_file_version.title

    def get_soul_file_version_number(self, obj: ResearchAgentRun) -> int | None:
        if obj.soul_file_version_id is None:
            return None
        return obj.soul_file_version.version_number


class ResearchAgentRunCreateSerializer(serializers.Serializer):
    project = serializers.PrimaryKeyRelatedField(
        queryset=ResearchProject.active_objects.none()
    )
    role = serializers.ChoiceField(choices=ResearchAgentRole.choices)
    task = serializers.CharField()
    selected_context = serializers.JSONField(required=False, default=dict)
    allowed_tools = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        default=list,
        allow_empty=True,
    )
    output_destination = serializers.ChoiceField(
        choices=ResearchAgentOutputDestination.choices,
        required=False,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        if request is not None and request.user.is_authenticated:
            self.fields["project"].queryset = ResearchProject.active_objects.filter(
                user=request.user
            )

    def validate_task(self, value: str) -> str:
        if not value.strip():
            raise serializers.ValidationError("Task is required.")
        return value


class ResearchHermesToolCallCallbackSerializer(serializers.Serializer):
    tool_name = serializers.CharField()
    status = serializers.ChoiceField(choices=ResearchAgentToolCallStatus.choices)
    input_summary = serializers.CharField(required=False, allow_blank=True, default="")
    output_summary = serializers.CharField(required=False, allow_blank=True, default="")
    error_message = serializers.CharField(required=False, allow_blank=True, default="")
    cost_usd = serializers.DecimalField(
        max_digits=12,
        decimal_places=6,
        required=False,
        default=0,
    )
    started_at = serializers.DateTimeField(required=False, allow_null=True)
    completed_at = serializers.DateTimeField(required=False, allow_null=True)


class ResearchHermesRunCallbackSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=ResearchAgentRunStatus.choices)
    status_message = serializers.CharField(required=False, allow_blank=True, default="")
    error_message = serializers.CharField(required=False, allow_blank=True, default="")
    external_run_id = serializers.CharField(
        required=False, allow_blank=True, default=""
    )
    cost_usd = serializers.DecimalField(
        max_digits=12,
        decimal_places=6,
        required=False,
        default=0,
    )
    tool_calls = ResearchHermesToolCallCallbackSerializer(
        many=True,
        required=False,
        default=list,
    )

    def validate(self, attrs):
        attrs = super().validate(attrs)
        forbidden_keys = {
            "knowledgeItems",
            "knowledge_items",
            "approvedKnowledgeItems",
            "approved_knowledge_items",
        }
        payload_keys = set(self.initial_data.keys())
        if payload_keys.intersection(forbidden_keys):
            raise serializers.ValidationError(
                "Hermes callbacks cannot write approved knowledge."
            )
        return attrs


class ResearchProjectSerializer(serializers.ModelSerializer):
    pending_review_count = serializers.SerializerMethodField()
    approved_count = serializers.SerializerMethodField()
    source_count = serializers.SerializerMethodField()
    active_soul_file_version = serializers.SerializerMethodField()
    active_soul_file_title = serializers.SerializerMethodField()
    active_soul_file_version_number = serializers.SerializerMethodField()

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
            "active_soul_file",
            "active_soul_file_version",
            "active_soul_file_title",
            "active_soul_file_version_number",
            "pending_review_count",
            "approved_count",
            "source_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "pending_review_count",
            "approved_count",
            "source_count",
            "active_soul_file_version",
            "active_soul_file_title",
            "active_soul_file_version_number",
            "created_at",
            "updated_at",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        if request is not None and request.user.is_authenticated:
            self.fields["active_soul_file"].queryset = (
                ResearchSoulFile.active_objects.filter(user=request.user)
            )

    def get_pending_review_count(self, obj: ResearchProject) -> int:
        return obj.staging_items.filter(
            is_deleted=False,
            is_active=True,
            status=ResearchReviewStatus.PENDING,
        ).count()

    def get_approved_count(self, obj: ResearchProject) -> int:
        return obj.knowledge_items.filter(is_deleted=False, is_active=True).count()

    def get_source_count(self, obj: ResearchProject) -> int:
        return obj.sources.filter(is_deleted=False, is_active=True).count()

    def get_active_soul_file_version(self, obj: ResearchProject) -> int | None:
        if obj.active_soul_file_id is None:
            return None
        current_version = obj.active_soul_file.current_version
        return current_version.id if current_version is not None else None

    def get_active_soul_file_title(self, obj: ResearchProject) -> str:
        if obj.active_soul_file_id is None:
            return ""
        return obj.active_soul_file.title

    def get_active_soul_file_version_number(self, obj: ResearchProject) -> int | None:
        if obj.active_soul_file_id is None:
            return None
        current_version = obj.active_soul_file.current_version
        return current_version.version_number if current_version is not None else None

    def validate_status(self, value: str) -> str:
        if value not in ResearchProjectStatus.values:
            raise serializers.ValidationError("Unsupported research project status.")
        return value


class ResearchSourceSerializer(serializers.ModelSerializer):
    class Meta:
        model = ResearchSource
        fields = [
            "id",
            "project",
            "kind",
            "title",
            "citation",
            "url",
            "doi",
            "authors",
            "venue",
            "year",
            "abstract",
            "notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        if request is not None and request.user.is_authenticated:
            self.fields["project"].queryset = ResearchProject.active_objects.filter(
                user=request.user
            )


class ResearchStagingItemSerializer(serializers.ModelSerializer):
    soul_file_title = serializers.SerializerMethodField()
    soul_file_version_number = serializers.SerializerMethodField()

    class Meta:
        model = ResearchStagingItem
        fields = [
            "id",
            "project",
            "source",
            "item_type",
            "title",
            "authors",
            "venue",
            "year",
            "url",
            "doi",
            "content",
            "rationale",
            "confidence",
            "confidence_rationale",
            "evidence_label",
            "citation_context",
            "provenance",
            "soul_file_version",
            "soul_file_title",
            "soul_file_version_number",
            "status",
            "rejection_reason",
            "later_reason",
            "reviewed_by",
            "reviewed_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "status",
            "rejection_reason",
            "later_reason",
            "reviewed_by",
            "reviewed_at",
            "soul_file_version",
            "soul_file_title",
            "soul_file_version_number",
            "created_at",
            "updated_at",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        if request is not None and request.user.is_authenticated:
            self.fields["project"].queryset = ResearchProject.active_objects.filter(
                user=request.user
            )
            self.fields["source"].queryset = ResearchSource.active_objects.filter(
                user=request.user,
                project__user=request.user,
            )

    def validate(self, attrs):
        attrs = super().validate(attrs)
        project = attrs.get("project") or getattr(self.instance, "project", None)
        source = attrs.get("source")
        if (
            source is not None
            and project is not None
            and source.project_id != project.id
        ):
            raise serializers.ValidationError(
                {"source": "Source must belong to the selected research project."}
            )
        return attrs

    def get_soul_file_title(self, obj: ResearchStagingItem) -> str:
        if obj.soul_file_version_id is None:
            return ""
        return obj.soul_file_version.title

    def get_soul_file_version_number(self, obj: ResearchStagingItem) -> int | None:
        if obj.soul_file_version_id is None:
            return None
        return obj.soul_file_version.version_number


class ResearchKnowledgeItemSerializer(serializers.ModelSerializer):
    soul_file_title = serializers.SerializerMethodField()
    soul_file_version_number = serializers.SerializerMethodField()

    class Meta:
        model = ResearchKnowledgeItem
        fields = [
            "id",
            "project",
            "staging_item",
            "source",
            "title",
            "authors",
            "venue",
            "year",
            "url",
            "doi",
            "content",
            "rationale",
            "confidence",
            "confidence_rationale",
            "evidence_label",
            "citation_context",
            "provenance",
            "soul_file_version",
            "soul_file_title",
            "soul_file_version_number",
            "approved_by",
            "approved_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_soul_file_title(self, obj: ResearchKnowledgeItem) -> str:
        if obj.soul_file_version_id is None:
            return ""
        return obj.soul_file_version.title

    def get_soul_file_version_number(self, obj: ResearchKnowledgeItem) -> int | None:
        if obj.soul_file_version_id is None:
            return None
        return obj.soul_file_version.version_number


class ReviewReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, default="")


class ProjectSoulFileSelectionSerializer(serializers.Serializer):
    soul_file = serializers.PrimaryKeyRelatedField(
        queryset=ResearchSoulFile.active_objects.none()
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        if request is not None and request.user.is_authenticated:
            self.fields["soul_file"].queryset = ResearchSoulFile.active_objects.filter(
                user=request.user
            )


__all__ = [
    "ResearchAgentRunCreateSerializer",
    "ResearchAgentRunSerializer",
    "ResearchAgentToolCallSerializer",
    "ResearchHermesRunCallbackSerializer",
    "ResearchHermesToolCallCallbackSerializer",
    "ResearchKnowledgeItemSerializer",
    "ResearchProjectSerializer",
    "ResearchSoulFileSerializer",
    "ResearchSoulFileVersionSerializer",
    "ResearchStagingItemSerializer",
    "ResearchSourceSerializer",
    "ProjectSoulFileSelectionSerializer",
    "ReviewReasonSerializer",
]
