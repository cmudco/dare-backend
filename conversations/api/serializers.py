import json

from djangorestframework_camel_case.util import camelize
from rest_framework import serializers

from agents.models import Agent
from conversations.constants import ToolCallOrigin
from conversations.models import (
    LLM,
    Artifact,
    ArtifactCheckpoint,
    Conversation,
    ConversationSummary,
    Feedback,
    Message,
    MessageToolCall,
    ModelCardData,
    PublicFeedbackSource,
    PublicFeedbackSourceCluster,
    Snippet,
    WebSearchSource,
)
from core.services.energy_service import compute_relatable_stats
from dare_tools.models import DareTool
from files.api.serializers import FileSerializer, TagSerializer
from mcp.models import MCPServer
from prompts.api.serializers import PromptSerializer
from prompts.models import Prompt
from users.constants import VectorDBChoice


class LLMSerializer(serializers.ModelSerializer):
    class Meta:
        model = LLM
        fields = [
            "id",
            "name",
            "identifier",
            "provider",
            "description",
            "is_active",
            "is_reasoning",
            "supports_temperature",
            "supports_effort",
            "supports_adaptive_thinking",
            "default_effort",
            "default_adaptive_thinking_enabled",
            "is_image_generator",
            "is_audio_transcriber",
            "input_token_rate_per_million",
            "output_token_rate_per_million",
            "tier",
        ]


class ConversationSerializer(serializers.ModelSerializer):
    user = serializers.SerializerMethodField()
    prompt = PromptSerializer(read_only=True)
    prompt_id = serializers.PrimaryKeyRelatedField(
        queryset=Prompt.active_objects.all(),
        source="prompt",
        required=False,
        allow_null=True,
        write_only=True,
    )
    selected_model = serializers.PrimaryKeyRelatedField(
        queryset=LLM.objects.filter(is_active=True),
        required=False,
        allow_null=True,
        read_only=False,
    )
    conversation_id = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="Optional conversation ID. Auto-generated if not provided.",
    )
    bot_id = serializers.IntegerField(
        required=False,
        allow_null=True,
        help_text="Associated Socratic Bot ID (only for SocraticBots source).",
    )
    anonymous_session_id = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
        help_text="Session ID for anonymous public bot conversations.",
    )
    access_code = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
        help_text=(
            "Access code redeemed by the user when starting a bot conversation. "
            "Persisted on the Conversation so the billing finalizer can resolve "
            "the matching AccessCodeGroup and its GroupWallet for institutional "
            "(GROUP_WALLET) bots without round-tripping back to SocraticBooks."
        ),
    )
    selected_mcp_server_ids = serializers.PrimaryKeyRelatedField(
        queryset=MCPServer.active_objects.all(),
        source="selected_mcp_servers",
        many=True,
        required=False,
        help_text="MCP servers enabled for this conversation.",
    )
    selected_dare_tool_slugs = serializers.SlugRelatedField(
        queryset=DareTool.active_objects.filter(is_active=True),
        source="selected_dare_tools",
        slug_field="slug",
        many=True,
        required=False,
        help_text="DARE tools enabled for this conversation.",
    )
    selected_agent = serializers.PrimaryKeyRelatedField(
        queryset=Agent.active_objects.all(),
        required=False,
        allow_null=True,
        help_text="Selected agent template for this conversation.",
    )
    selected_agent_name = serializers.CharField(
        source="selected_agent.name",
        read_only=True,
        allow_null=True,
    )
    is_owner = serializers.SerializerMethodField()
    is_forked = serializers.SerializerMethodField()
    can_share = serializers.SerializerMethodField()
    owner_email = serializers.SerializerMethodField()
    owner_user_id = serializers.SerializerMethodField()

    def get_user(self, obj):
        """Return user email or None for anonymous conversations."""
        return obj.user.email if obj.user else None

    def get_is_owner(self, obj):
        """Return True if the requesting user owns this conversation."""
        request = self.context.get("request")
        if (
            not request
            or not hasattr(request, "user")
            or not request.user.is_authenticated
        ):
            return False
        return obj.user_id == request.user.id

    def get_is_forked(self, obj):
        """Return True if this conversation was forked from another user's conversation."""
        return obj.file_owner_id is not None

    def get_can_share(self, obj):
        """Return True when the conversation is not a cross-user fork."""
        return obj.file_owner_id is None

    def get_owner_email(self, obj):
        """Return masked owner email for shared conversations."""
        if not obj.user or not obj.user.email:
            return None
        email = obj.user.email
        local, domain = email.split("@", 1) if "@" in email else (email, "")
        if len(local) <= 2:
            masked_local = local[0] + "***"
        else:
            masked_local = local[0] + "***" + local[-1]
        return f"{masked_local}@{domain}" if domain else masked_local

    def get_owner_user_id(self, obj):
        """Return owner's user ID for shared conversations (to fetch their files)."""
        request = self.context.get("request")
        if (
            not request
            or not hasattr(request, "user")
            or not request.user.is_authenticated
        ):
            return None
        # Only expose owner_user_id if the current user is NOT the owner
        # This is used by the frontend to load owner's files for shared conversations
        if obj.user_id != request.user.id:
            return obj.user_id
        return None

    class Meta:
        model = Conversation
        fields = [
            "conversation_id",
            "title",
            "source",
            "bot_id",
            "anonymous_session_id",
            "access_code",
            "created_at",
            "user",
            "max_context_snippets",
            "document_similarity_threshold",
            "rag_mode",
            "temperature",
            "effort",
            "max_tokens",
            "history_limit",
            "web_search_enabled",
            "web_fetch_enabled",
            "image_generation_enabled",
            "audio_transcription_enabled",
            "artifacts_enabled",
            "selected_model",
            "selected_media_ids",
            "prompt",
            "prompt_id",
            "sort_order",
            "selected_embedding_ids",
            "selected_file_ids",
            "selected_library_ids",
            "feedback_auto_prompt_count",
            "feedback_last_prompt_message_count",
            "feedback_last_prompt_timestamp",
            "selected_mcp_server_ids",
            "selected_dare_tool_slugs",
            "selected_agent",
            "selected_agent_name",
            "is_favorite",
            "is_published",
            "published_at",
            "is_owner",
            "is_forked",
            "can_share",
            "owner_email",
            "owner_user_id",
            "file_owner_id",
        ]
        read_only_fields = [
            "created_at",
            "user",
            "prompt",
            "selected_agent_name",
            "is_favorite",
            "is_owner",
            "is_forked",
            "can_share",
            "owner_email",
            "owner_user_id",
            "file_owner_id",
        ]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # `_fallback_llm_id` is annotated by ConversationViewSet and holds the
        # most recent `Message.llm_id`. When a user picks a different model
        # mid-conversation, we only persist it on the message (not on
        # `conversation.selected_model`), so the stored value goes stale.
        # Prefer the latest-message llm whenever one exists; fall back to the
        # stored `selected_model` only for conversations with no messages yet.
        fallback_llm_id = getattr(instance, "_fallback_llm_id", None)
        if fallback_llm_id:
            data["selected_model"] = fallback_llm_id
        return data


class ConversationSummarySerializer(serializers.ModelSerializer):
    conversation_id = serializers.CharField(
        source="conversation.conversation_id",
        read_only=True,
    )
    conversation_title = serializers.CharField(
        source="conversation.title",
        read_only=True,
        allow_null=True,
    )
    llm_name = serializers.CharField(source="llm.name", read_only=True)

    class Meta:
        model = ConversationSummary
        fields = [
            "id",
            "conversation_id",
            "conversation_title",
            "summary",
            "llm",
            "llm_name",
            "input_tokens",
            "output_tokens",
            "summarized_message_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class SnippetSerializer(serializers.ModelSerializer):
    file = FileSerializer(read_only=True)
    vector_db_source = serializers.SerializerMethodField()
    library = serializers.SerializerMethodField()

    class Meta:
        model = Snippet
        fields = [
            "id",
            "file",
            "library",
            "source_ref",
            "text",
            "similarity_score",
            "chunk_index",
            "vector_db_source",
        ]
        read_only_fields = [
            "id",
            "file",
            "library",
            "source_ref",
            "text",
            "similarity_score",
            "chunk_index",
            "vector_db_source",
        ]

    def get_library(self, obj):
        """Lightweight library reference for shared-library snippets."""
        if obj.library_id is None:
            return None
        return {
            "id": obj.library.id,
            "name": obj.library.name,
            "slug": obj.library.slug,
        }

    def get_vector_db_source(self, obj):
        """Return the human-readable name of the vector database source."""
        if (
            obj.file is not None
            and getattr(obj.file, "vector_db_source", None) is not None
        ):
            return dict(VectorDBChoice.choices).get(
                obj.file.vector_db_source, "Unknown"
            )
        if obj.library_id is not None:
            return obj.library.get_backend_display()
        return "Unknown"


class WebSearchSourceSerializer(serializers.ModelSerializer):
    """
    Serializer for web search sources/citations.

    Returns the essential fields for displaying source links in the UI,
    similar to how SnippetSerializer returns document context.
    """

    class Meta:
        model = WebSearchSource
        fields = [
            "id",
            "url",
            "title",
            "cited_text",
            "page_age",
            "provider",
        ]
        read_only_fields = fields


class MessageToolCallSerializer(serializers.ModelSerializer):
    """Serializer for persisted tool calls shown when messages are reloaded."""

    id = serializers.CharField(source="tool_call_id", read_only=True)
    round = serializers.IntegerField(source="round_index", read_only=True)
    mcp_result = serializers.SerializerMethodField()
    dare_result = serializers.SerializerMethodField()
    provider_result = serializers.SerializerMethodField()

    class Meta:
        model = MessageToolCall
        fields = [
            "id",
            "tool_name",
            "server_slug",
            "origin",
            "status",
            "round",
            "arguments",
            "error",
            "mcp_result",
            "dare_result",
            "provider_result",
        ]
        read_only_fields = fields

    def get_mcp_result(self, obj):
        if obj.origin != ToolCallOrigin.MCP:
            return None
        return self._parse_result(obj.result)

    def get_dare_result(self, obj):
        if obj.origin != ToolCallOrigin.DARE:
            return None
        return self._parse_result(obj.result)

    def get_provider_result(self, obj):
        if obj.origin != ToolCallOrigin.PROVIDER:
            return None
        return self._parse_result(obj.result)

    def _parse_result(self, result: str):
        if not result:
            return None
        try:
            return camelize(json.loads(result))
        except (json.JSONDecodeError, TypeError):
            return result


class MessageSerializer(serializers.ModelSerializer):
    sender_name = serializers.ReadOnlyField(read_only=True)
    files = FileSerializer(many=True, read_only=True)
    tags = TagSerializer(many=True, read_only=True)
    file_ids = serializers.ListField(
        child=serializers.IntegerField(), write_only=True, required=False
    )
    snippets = SnippetSerializer(many=True, read_only=True)
    web_search_sources = WebSearchSourceSerializer(many=True, read_only=True)
    llm = serializers.PrimaryKeyRelatedField(read_only=True, allow_null=True)
    artifactId = serializers.SerializerMethodField()
    artifactIds = serializers.SerializerMethodField()
    # Unified tool-call list (DARE + MCP + provider). The model's
    # related_name stays mcp_tool_calls for schema stability.
    tool_calls = MessageToolCallSerializer(
        many=True, read_only=True, source="mcp_tool_calls"
    )
    energy_stats = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = [
            "id",
            "conversation",
            "sender_type",
            "message",
            "sender_name",
            "files",
            "file_ids",
            "tags",
            "snippets",
            "web_search_sources",
            "created_at",
            "feedback_type",
            "feedback_text",
            "feedback_source",
            "is_edited",
            "is_regenerated",
            "original_message",
            "llm",
            "litellm_model_name",
            "input_tokens",
            "output_tokens",
            "cost",
            "energy_wh",
            "carbon_g",
            "water_ml",
            "energy_stats",
            "artifactId",
            "artifactIds",
            "tool_calls",
            "content_type",
            "content_metadata",
            "memory_context_data",
            "retrieval_trace",
        ]
        read_only_fields = [
            "id",
            "created_at",
            "sender_name",
            "files",
            "tags",
            "snippets",
            "web_search_sources",
            "input_tokens",
            "output_tokens",
            "cost",
            "energy_wh",
            "carbon_g",
            "water_ml",
            "energy_stats",
            "artifactId",
            "artifactIds",
            "tool_calls",
            "content_type",
            "content_metadata",
            "memory_context_data",
            "retrieval_trace",
        ]

    def get_artifactId(self, obj):
        """Get the ID of the first active artifact linked to this message.

        Scoped to the message's own conversation: the artifact detail endpoint
        (artifact_detail) only serves artifacts whose conversation matches the
        request URL, so advertising an artifact that lives in a different
        conversation would hand the UI an id that 404s. Stay consistent.
        """
        artifact_ids = self._get_artifact_ids(obj)
        return str(artifact_ids[0]) if artifact_ids else None

    def get_artifactIds(self, obj):
        """Return every active artifact created by this assistant message."""
        return self._get_artifact_ids(obj)

    @staticmethod
    def _get_artifact_ids(obj):
        """Resolve artifact IDs once when both compatibility fields serialize."""
        cache_attr = "_serialized_active_artifact_ids"
        artifact_ids = getattr(obj, cache_attr, None)
        if artifact_ids is None:
            prefetched = getattr(obj, "_prefetched_objects_cache", {}).get(
                "artifacts"
            )
            if prefetched is not None:
                artifacts = sorted(
                    (
                        artifact
                        for artifact in prefetched
                        if artifact.is_active
                        and artifact.conversation_id == obj.conversation_id
                    ),
                    key=lambda artifact: (artifact.created_at, artifact.id),
                )
                artifact_ids = [artifact.id for artifact in artifacts]
            else:
                artifact_ids = list(
                    obj.artifacts.filter(
                        is_active=True, conversation_id=obj.conversation_id
                    )
                    .order_by("created_at", "id")
                    .values_list("id", flat=True)
                )
            setattr(obj, cache_attr, artifact_ids)
        return artifact_ids

    def get_energy_stats(self, obj):
        """Compute relatable energy stats at read time from stored energy_wh."""
        if not obj.energy_wh or obj.energy_wh <= 0:
            return None
        stats = compute_relatable_stats(float(obj.energy_wh))
        return {
            "phoneBatteryPct": round(stats.phone_battery_pct, 4),
            "googleSearchesEquiv": round(stats.google_searches_equiv, 2),
            "ledBulbSeconds": round(stats.led_bulb_seconds, 2),
            "netflixSeconds": round(stats.netflix_seconds, 2),
            "evMeters": round(stats.ev_meters, 2),
            "fridgeSeconds": round(stats.fridge_seconds, 2),
            "humanThinkingSeconds": round(stats.human_thinking_seconds, 2),
        }


class ArtifactCheckpointSerializer(serializers.ModelSerializer):
    """Serializer for artifact checkpoints."""

    class Meta:
        model = ArtifactCheckpoint
        fields = [
            "id",
            "content_snapshot",
            "created_at",
        ]
        read_only_fields = fields


class ArtifactSerializer(serializers.ModelSerializer):
    """
    Clean artifact serializer for DARE tool outputs (charts, diagrams).

    Removed legacy fields: outline, language, estimated_sections, current_section,
    progress, sections_remaining, word_count, latest_checkpoint.
    """

    conversation_id = serializers.CharField(
        source="conversation.conversation_id", read_only=True
    )
    message_id = serializers.PrimaryKeyRelatedField(source="message", read_only=True)

    # Versioning fields
    parent_artifact_id = serializers.PrimaryKeyRelatedField(
        source="parent_artifact",
        read_only=True,
    )
    artifact_group_id = serializers.PrimaryKeyRelatedField(
        source="artifact_group",
        read_only=True,
    )
    version_history = serializers.SerializerMethodField()

    class Meta:
        model = Artifact
        fields = [
            "id",
            "conversation_id",
            "message_id",
            "artifact_type",
            "title",
            "content",
            "filename",
            "content_type",
            "source_tool",
            "status",
            "metadata",
            # Versioning
            "version",
            "parent_artifact_id",
            "artifact_group_id",
            "version_history",
            # Timestamps
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_version_history(self, obj):
        """Get list of versions in this artifact's group."""
        if not obj.artifact_group:
            return []
        versions = obj.artifact_group.versions.filter(is_active=True).order_by(
            "version"
        )
        return [
            {"id": v.id, "version": v.version, "createdAt": v.created_at.isoformat()}
            for v in versions
        ]


class ArtifactListSerializer(serializers.ModelSerializer):
    """
    Lightweight artifact serializer for lists (content loaded on demand).

    Used in conversation history and artifact lists.
    """

    conversation_id = serializers.CharField(
        source="conversation.conversation_id", read_only=True
    )

    # Versioning fields
    parent_artifact_id = serializers.PrimaryKeyRelatedField(
        source="parent_artifact",
        read_only=True,
    )
    artifact_group_id = serializers.PrimaryKeyRelatedField(
        source="artifact_group",
        read_only=True,
    )

    class Meta:
        model = Artifact
        fields = [
            "id",
            "conversation_id",
            "artifact_type",
            "title",
            "filename",
            "content_type",
            "source_tool",
            "status",
            # Versioning
            "version",
            "parent_artifact_id",
            "artifact_group_id",
            "created_at",
        ]
        read_only_fields = fields


class PublicFeedbackSourceSerializer(serializers.ModelSerializer):
    class Meta:
        model = PublicFeedbackSource
        fields = [
            "id",
            "title",
            "url",
            "source_type",
            "page_date",
            "snippet",
        ]


class FeedbackSerializer(serializers.ModelSerializer):
    """Serializer for general user feedback from the FAB widget."""

    class Meta:
        model = Feedback
        fields = [
            "id",
            "emotion",
            "category",
            "message",
            "screenshot",
            "page",
            "browser_info",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]


class PublicFeedbackSourceClusterSerializer(serializers.ModelSerializer):
    sources = PublicFeedbackSourceSerializer(many=True, read_only=True)

    class Meta:
        model = PublicFeedbackSourceCluster
        fields = [
            "id",
            "cluster_index",
            "canonical_title",
            "canonical_url",
            "identifier",
            "sources",
        ]


class ModelCardDataSerializer(serializers.ModelSerializer):
    source_clusters = PublicFeedbackSourceClusterSerializer(many=True, read_only=True)

    class Meta:
        model = ModelCardData
        fields = [
            "id",
            "name",
            "slug",
            "provider_name",
            "name_variants",
            "public_feedback",
            "source_clusters",
            "llm",
            "created_at",
            "updated_at",
        ]


class ModelCardDataListSerializer(serializers.ModelSerializer):
    """Lighter serializer for list view (excludes full public_feedback blob)."""

    has_public_feedback = serializers.SerializerMethodField()

    class Meta:
        model = ModelCardData
        fields = [
            "id",
            "name",
            "slug",
            "provider_name",
            "llm",
            "has_public_feedback",
            "updated_at",
        ]

    def get_has_public_feedback(self, obj):
        return bool(obj.public_feedback)
