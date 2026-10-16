from rest_framework import serializers

from agents.models import Agent, AgentNodeData, TemplateAgentNodeData
from agents.services.agent_service import agent_model_ref
from core.services.model_ref import ModelRefUnavailable, resolve_model_ref
from dare_tools.models import DareTool
from files.models import File
from libraries.models import SharedLibrary, UserLibraryAccess
from mcp.models import MCPServer
from prompts.models import Prompt


class AgentSerializer(serializers.ModelSerializer):
    """An agent and its full chat setup.

    `model_ref` is the picker id chats use; it is resolved into either a DARE
    model or a LiteLLM key and gateway model.
    """

    user = serializers.ReadOnlyField(source="user.email")
    prompt_title = serializers.CharField(source="prompt.title", read_only=True)
    model_ref = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
        max_length=512,
        help_text="Picker id: an LLM PK, 'litellm:<key>:<model>', or null.",
    )
    model_name = serializers.SerializerMethodField()
    dare_tool_slugs = serializers.SlugRelatedField(
        source="dare_tools",
        slug_field="slug",
        many=True,
        required=False,
        queryset=DareTool.active_objects.filter(is_active=True),
    )

    class Meta:
        model = Agent
        fields = [
            "id",
            "user",
            "name",
            "description",
            "prompt",
            "prompt_title",
            "model_ref",
            "model_name",
            "llm",
            "max_tokens",
            "temperature",
            "effort",
            "history_limit",
            "rag_mode",
            "max_context_snippets",
            "document_similarity_threshold",
            "enable_web_search",
            "web_fetch_enabled",
            "artifacts_enabled",
            "memory_enabled",
            "content_files",
            "embedding_files",
            "libraries",
            "mcp_servers",
            "dare_tool_slugs",
            "version",
            "parent",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "user",
            "llm",
            "version",
            "parent",
            "created_at",
            "updated_at",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        user = self.context["request"].user
        self.fields["prompt"].queryset = Prompt.active_objects.filter(user=user)
        files = File.active_objects.filter(user=user)
        self.fields["content_files"].child_relation.queryset = files
        self.fields["embedding_files"].child_relation.queryset = files
        self.fields["libraries"].child_relation.queryset = (
            SharedLibrary.active_objects.filter(
                pk__in=UserLibraryAccess.active_objects.filter(user=user).values(
                    "library_id"
                )
            )
        )
        self.fields["mcp_servers"].child_relation.queryset = (
            MCPServer.active_objects.all()
        )

    def validate_model_ref(self, value):
        try:
            choice = resolve_model_ref(self.context["request"].user, value)
        except ModelRefUnavailable as exc:
            raise serializers.ValidationError(str(exc))
        return {
            "llm": choice.llm,
            "litellm_key": choice.litellm_key,
            "litellm_model_name": choice.litellm_model_name,
        }

    def validate(self, attrs):
        attrs.update(attrs.pop("model_ref", {}))
        return attrs

    def get_model_name(self, agent) -> str:
        if agent.litellm_model_name:
            return agent.litellm_model_name
        return agent.llm.name if agent.llm else ""

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["model_ref"] = agent_model_ref(instance)
        return data


class AgentNodeDataSerializer(serializers.ModelSerializer):
    """Serializer for AgentNodeData."""

    agent_name = serializers.CharField(source="agent.name", read_only=True)
    prompt_title = serializers.CharField(
        source="prompt.title", read_only=True, required=False
    )
    llm_name = serializers.CharField(source="llm.name", read_only=True, required=False)

    class Meta:
        model = AgentNodeData
        fields = [
            "id",
            "agent",
            "agent_name",
            "name",
            "description",
            "prompt",
            "prompt_title",
            "content_files",
            "embedding_files",
            "llm",
            "llm_name",
            "agent_number",
            "max_tokens",
            "temperature",
            "max_context_snippets",
            "document_similarity_threshold",
            "use_previous_agent_files",
            "use_previous_agent_embeddings",
            "text_input",
            "use_structured_output_node",
            "enable_web_search",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class TemplateAgentNodeDataSerializer(serializers.ModelSerializer):
    """Serializer for TemplateAgentNodeData."""

    agent_name = serializers.CharField(source="agent.name", read_only=True)

    class Meta:
        model = TemplateAgentNodeData
        fields = [
            "id",
            "agent",
            "agent_name",
            "name",
            "description",
            "agent_number",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
