from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from core.services.model_ref import ModelRefUnavailable, resolve_model_ref
from files.models import File, Folder
from libraries.models import SharedLibrary, UserLibraryAccess
from projects.constants import (
    FULL_FILES_TOKEN_LIMIT,
    PROJECT_DESCRIPTION_MAX_LENGTH,
    PROJECT_INSTRUCTIONS_MAX_LENGTH,
    PROJECT_NAME_MAX_LENGTH,
    ProjectSourceMode,
)
from projects.models import PersonalProject
from projects.services.project_service import (
    SourceSize,
    estimate_source_size,
    project_model_ref,
    selected_source_files,
    set_project_instructions,
)
from prompts.models import Prompt
from workflows.constants import WorkflowKind
from workflows.models import Workflow


def full_files_too_large_message(size: SourceSize) -> str:
    return _(
        "These files are about %(tokens)s tokens, over the %(limit)s-token "
        "limit for reading in full. Use search instead."
    ) % {
        "tokens": f"{size.estimated_tokens:,}",
        "limit": f"{FULL_FILES_TOKEN_LIMIT:,}",
    }


class PersonalProjectSerializer(serializers.ModelSerializer):
    name = serializers.CharField(max_length=PROJECT_NAME_MAX_LENGTH)
    description = serializers.CharField(
        max_length=PROJECT_DESCRIPTION_MAX_LENGTH, allow_blank=True, required=False
    )
    prompt = serializers.PrimaryKeyRelatedField(
        required=False, allow_null=True, queryset=Prompt.active_objects.none()
    )
    instructions = serializers.CharField(
        max_length=PROJECT_INSTRUCTIONS_MAX_LENGTH,
        allow_blank=True,
        required=False,
        trim_whitespace=False,
        write_only=True,
        help_text="Saved as the project's prompt; applied after `prompt`.",
    )
    default_model_ref = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
        max_length=512,
        write_only=True,
        help_text=(
            "Picker id of the model new chats start on: an LLM PK, "
            "'litellm:<key>:<model>', or null to choose in each chat."
        ),
    )
    file_ids = serializers.PrimaryKeyRelatedField(
        source="files", many=True, required=False, queryset=File.active_objects.none()
    )
    folder_ids = serializers.PrimaryKeyRelatedField(
        source="folders", many=True, required=False, queryset=Folder.objects.none()
    )
    library_ids = serializers.PrimaryKeyRelatedField(
        source="libraries",
        many=True,
        required=False,
        queryset=SharedLibrary.objects.none(),
    )
    workflow_ids = serializers.PrimaryKeyRelatedField(
        source="workflows",
        many=True,
        required=False,
        queryset=Workflow.active_objects.none(),
    )
    conversation_count = serializers.IntegerField(read_only=True)
    last_activity_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = PersonalProject
        fields = [
            "id",
            "name",
            "icon",
            "description",
            "prompt",
            "instructions",
            "default_model_ref",
            "web_search_enabled",
            "artifacts_enabled",
            "memory_enabled",
            "memory_scope",
            "source_mode",
            "file_ids",
            "folder_ids",
            "library_ids",
            "workflow_ids",
            "conversation_count",
            "last_activity_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        user = self.context["request"].user
        self.fields["prompt"].queryset = Prompt.active_objects.filter(user=user)
        self.fields["workflow_ids"].child_relation.queryset = (
            Workflow.active_objects.filter(user=user, kind=WorkflowKind.USER)
        )
        self.fields["file_ids"].child_relation.queryset = File.active_objects.filter(
            user=user, is_media=False
        )
        self.fields["folder_ids"].child_relation.queryset = Folder.objects.filter(
            user=user
        )
        self.fields["library_ids"].child_relation.queryset = (
            SharedLibrary.active_objects.filter(
                pk__in=UserLibraryAccess.active_objects.filter(user=user).values(
                    "library_id"
                )
            )
        )

    def validate_default_model_ref(self, value):
        """Resolve the picker id into the project's default-model columns."""
        try:
            choice = resolve_model_ref(self.context["request"].user, value)
        except ModelRefUnavailable as exc:
            raise serializers.ValidationError(str(exc))
        return {
            "default_model": choice.llm,
            "default_litellm_key": choice.litellm_key,
            "default_litellm_model_name": choice.litellm_model_name,
        }

    def validate(self, attrs):
        attrs.update(attrs.pop("default_model_ref", {}))
        self._check_full_files_fit(attrs)
        return attrs

    def _check_full_files_fit(self, attrs):
        """Refuse switching to full files for sources already too large.

        Sources added later are accepted; chats then fall back to search.
        """
        if attrs.get("source_mode") != ProjectSourceMode.FULL:
            return
        project = self.instance
        files = attrs.get("files", project.files.all() if project else [])
        folders = attrs.get("folders", project.folders.all() if project else [])
        size = estimate_source_size(
            selected_source_files(
                self.context["request"].user,
                [file.pk for file in files],
                [folder.pk for folder in folders],
            )
        )
        if not size.fits_full_files:
            raise serializers.ValidationError(
                {"source_mode": full_files_too_large_message(size)}
            )

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["instructions"] = instance.prompt.content if instance.prompt else ""
        data["default_model_ref"] = project_model_ref(instance)
        return data

    def create(self, validated_data):
        instructions = validated_data.pop("instructions", None)
        project = super().create(validated_data)
        if instructions is not None:
            set_project_instructions(project, instructions)
        return project

    def update(self, instance, validated_data):
        instructions = validated_data.pop("instructions", None)
        project = super().update(instance, validated_data)
        if instructions is not None:
            set_project_instructions(project, instructions)
        return project


class SourceSizeRequestSerializer(serializers.Serializer):
    file_ids = serializers.PrimaryKeyRelatedField(
        many=True, required=False, default=list, queryset=File.active_objects.none()
    )
    folder_ids = serializers.PrimaryKeyRelatedField(
        many=True, required=False, default=list, queryset=Folder.objects.none()
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        user = self.context["request"].user
        self.fields["file_ids"].child_relation.queryset = File.active_objects.filter(
            user=user, is_media=False
        )
        self.fields["folder_ids"].child_relation.queryset = Folder.objects.filter(
            user=user
        )


class SourceSizeSerializer(serializers.Serializer):
    file_count = serializers.IntegerField()
    estimated_tokens = serializers.IntegerField()
    token_limit = serializers.SerializerMethodField()
    fits_full_files = serializers.BooleanField()

    def get_token_limit(self, size) -> int:
        return FULL_FILES_TOKEN_LIMIT
