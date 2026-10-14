import uuid

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from billing.models import LiteLLMKey
from conversations.models import LLM
from core.services.dtos.llm_descriptor_dto import split_litellm_picker_id
from files.models import File, Folder
from libraries.models import SharedLibrary, UserLibraryAccess
from projects.constants import (
    PROJECT_DESCRIPTION_MAX_LENGTH,
    PROJECT_INSTRUCTIONS_MAX_LENGTH,
    PROJECT_NAME_MAX_LENGTH,
)
from projects.models import PersonalProject
from projects.services.project_service import (
    project_model_ref,
    set_project_instructions,
)
from prompts.models import Prompt
from workflows.constants import WorkflowKind
from workflows.models import Workflow


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
        cleared = {
            "default_model": None,
            "default_litellm_key": None,
            "default_litellm_model_name": "",
        }
        if not value:
            return cleared

        litellm = split_litellm_picker_id(value)
        if litellm is not None:
            key_id, model_name = litellm
            try:
                key = (
                    LiteLLMKey.visible_for_user(self.context["request"].user)
                    .filter(pk=uuid.UUID(key_id))
                    .first()
                )
            except ValueError:
                key = None
            if key is None:
                raise serializers.ValidationError(
                    _("That LiteLLM key is not available.")
                )
            return {
                **cleared,
                "default_litellm_key": key,
                "default_litellm_model_name": model_name,
            }

        model = (
            LLM.objects.filter(is_active=True, pk=int(value)).first()
            if value.isdigit()
            else None
        )
        if model is None:
            raise serializers.ValidationError(_("That model is not available."))
        return {**cleared, "default_model": model}

    def validate(self, attrs):
        attrs.update(attrs.pop("default_model_ref", {}))
        return attrs

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
