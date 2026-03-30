from rest_framework import serializers
from conversations.models import LLM, Message, Conversation, Snippet, WebSearchSource, Artifact, ArtifactCheckpoint, Feedback, ModelCardData, PublicFeedbackSourceCluster, PublicFeedbackSource, MessageToolCall
from core.services.energy_service import compute_relatable_stats
from files.api.serializers import FileSerializer, TagSerializer
from prompts.models import Prompt
from prompts.api.serializers import PromptSerializer
from users.constants import VectorDBChoice
from mcp.models import MCPServer
from dare_tools.models import DareTool
from agents.models import Agent

class LLMSerializer(serializers.ModelSerializer):
    class Meta:
        model = LLM
        fields = ['id', 'name', 'identifier', 'provider', 'description', 'is_reasoning', 'is_image_generator', 'is_audio_transcriber', 'input_token_rate_per_million', 'output_token_rate_per_million', 'tier']

class ConversationSerializer(serializers.ModelSerializer):
    user = serializers.SerializerMethodField()
    prompt = PromptSerializer(read_only=True)
    prompt_id = serializers.PrimaryKeyRelatedField(
        queryset=Prompt.active_objects.all(),
        source='prompt',
        required=False,
        allow_null=True,
        write_only=True
    )
    selected_model = serializers.PrimaryKeyRelatedField(
        queryset=LLM.objects.all(),
        required=False,
        allow_null=True,
        read_only=False
    )
    conversation_id = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="Optional conversation ID. Auto-generated if not provided."
    )
    bot_id = serializers.IntegerField(
        required=False,
        allow_null=True,
        help_text="Associated Socratic Bot ID (only for SocraticBots source)."
    )
    anonymous_session_id = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
        help_text="Session ID for anonymous public bot conversations."
    )
    selected_mcp_server_ids = serializers.PrimaryKeyRelatedField(
        queryset=MCPServer.active_objects.all(),
        source='selected_mcp_servers',
        many=True,
        required=False,
        help_text="MCP servers enabled for this conversation."
    )
    selected_dare_tool_slugs = serializers.SlugRelatedField(
        queryset=DareTool.active_objects.filter(is_active=True),
        source='selected_dare_tools',
        slug_field='slug',
        many=True,
        required=False,
        help_text="DARE tools enabled for this conversation."
    )
    selected_agent = serializers.PrimaryKeyRelatedField(
        queryset=Agent.active_objects.all(),
        required=False,
        allow_null=True,
        help_text="Selected agent template for this conversation."
    )
    selected_agent_name = serializers.CharField(
        source='selected_agent.name',
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
        request = self.context.get('request')
        if not request or not hasattr(request, 'user') or not request.user.is_authenticated:
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
        local, domain = email.split('@', 1) if '@' in email else (email, '')
        if len(local) <= 2:
            masked_local = local[0] + '***'
        else:
            masked_local = local[0] + '***' + local[-1]
        return f"{masked_local}@{domain}" if domain else masked_local

    def get_owner_user_id(self, obj):
        """Return owner's user ID for shared conversations (to fetch their files)."""
        request = self.context.get('request')
        if not request or not hasattr(request, 'user') or not request.user.is_authenticated:
            return None
        # Only expose owner_user_id if the current user is NOT the owner
        # This is used by the frontend to load owner's files for shared conversations
        if obj.user_id != request.user.id:
            return obj.user_id
        return None

    class Meta:
        model = Conversation
        fields = [
            'conversation_id',
            'title',
            'source',
            'bot_id',
            'anonymous_session_id',
            'created_at',
            'user',
            'max_context_snippets',
            'document_similarity_threshold',
            'temperature',
            'max_tokens',
            'history_limit',
            'web_search_enabled',
            'image_generation_enabled',
            'audio_transcription_enabled',
            'artifacts_enabled',
            'selected_model',
            'selected_media_ids',
            'prompt',
            'prompt_id',
            'sort_order',
            'selected_embedding_ids',
            'selected_file_ids',
            'feedback_auto_prompt_count',
            'feedback_last_prompt_message_count',
            'feedback_last_prompt_timestamp',
            'selected_mcp_server_ids',
            'selected_dare_tool_slugs',
            'selected_agent',
            'selected_agent_name',
            'is_published',
            'published_at',
            'is_owner',
            'is_forked',
            'can_share',
            'owner_email',
            'owner_user_id',
            'file_owner_id',
        ]
        read_only_fields = ['created_at', 'user', 'prompt', 'selected_agent_name', 'is_owner', 'is_forked', 'can_share', 'owner_email', 'owner_user_id', 'file_owner_id']

class SnippetSerializer(serializers.ModelSerializer):
    file = FileSerializer(read_only=True)
    vector_db_source = serializers.SerializerMethodField()

    class Meta:
        model = Snippet
        fields = ['id', 'file', 'text', 'similarity_score', 'chunk_index', 'vector_db_source']
        read_only_fields = ['id', 'file', 'text', 'similarity_score', 'chunk_index', 'vector_db_source']

    def get_vector_db_source(self, obj):
        """Return the human-readable name of the vector database source."""
        if hasattr(obj.file, 'vector_db_source') and obj.file.vector_db_source is not None:
            return dict(VectorDBChoice.choices).get(obj.file.vector_db_source, "Unknown")
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
            'id',
            'url',
            'title',
            'cited_text',
            'page_age',
            'provider',
        ]
        read_only_fields = fields


class MessageToolCallSerializer(serializers.ModelSerializer):
    """Serializer for MCP tool calls within messages."""

    class Meta:
        model = MessageToolCall
        fields = [
            'tool_call_id',
            'tool_name',
            'server_slug',
            'status',
            'result',
            'error',
        ]
        read_only_fields = fields


class MessageSerializer(serializers.ModelSerializer):
    sender_name = serializers.ReadOnlyField(read_only=True)
    files = FileSerializer(many=True, read_only=True)
    tags = TagSerializer(many=True, read_only=True)
    file_ids = serializers.ListField(
        child=serializers.IntegerField(),
        write_only=True,
        required=False
    )
    snippets = SnippetSerializer(many=True, read_only=True)
    web_search_sources = WebSearchSourceSerializer(many=True, read_only=True)
    llm = serializers.PrimaryKeyRelatedField(read_only=True, allow_null=True)
    artifactId = serializers.SerializerMethodField()
    mcp_tool_calls = MessageToolCallSerializer(many=True, read_only=True)
    energy_stats = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = [
            'id',
            'conversation',
            'sender_type',
            'message',
            'sender_name',
            'files',
            'file_ids',
            'tags',
            'snippets',
            'web_search_sources',
            'created_at',
            'feedback_type',
            'feedback_text',
            'feedback_source',
            'is_edited',
            'is_regenerated',
            'original_message',
            'llm',
            'input_tokens',
            'output_tokens',
            'cost',
            'energy_wh',
            'carbon_g',
            'water_ml',
            'energy_stats',
            'artifactId',
            'mcp_tool_calls',
            'content_type',
            'content_metadata',
        ]
        read_only_fields = ['id', 'created_at', 'sender_name', 'files', 'tags', 'snippets', 'web_search_sources', 'input_tokens', 'output_tokens', 'cost', 'energy_wh', 'carbon_g', 'water_ml', 'energy_stats', 'artifactId', 'mcp_tool_calls', 'content_type', 'content_metadata']

    def get_artifactId(self, obj):
        """Get the ID of the first active artifact linked to this message."""
        artifact = obj.artifacts.filter(is_active=True).first()
        return str(artifact.id) if artifact else None

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
            'id',
            'content_snapshot',
            'created_at',
        ]
        read_only_fields = fields


class ArtifactSerializer(serializers.ModelSerializer):
    """
    Clean artifact serializer for DARE tool outputs (charts, diagrams).
    
    Removed legacy fields: outline, language, estimated_sections, current_section,
    progress, sections_remaining, word_count, latest_checkpoint.
    """

    conversation_id = serializers.CharField(source='conversation.conversation_id', read_only=True)
    message_id = serializers.PrimaryKeyRelatedField(source='message', read_only=True)
    
    # Versioning fields
    parent_artifact_id = serializers.PrimaryKeyRelatedField(
        source='parent_artifact',
        read_only=True,
    )
    artifact_group_id = serializers.PrimaryKeyRelatedField(
        source='artifact_group',
        read_only=True,
    )
    version_history = serializers.SerializerMethodField()

    class Meta:
        model = Artifact
        fields = [
            'id',
            'conversation_id',
            'message_id',
            'artifact_type',
            'title',
            'content',
            'filename',
            'content_type',
            'source_tool',
            'status',
            'metadata',
            # Versioning
            'version',
            'parent_artifact_id',
            'artifact_group_id',
            'version_history',
            # Timestamps
            'created_at',
            'updated_at',
        ]
        read_only_fields = fields

    def get_version_history(self, obj):
        """Get list of versions in this artifact's group."""
        if not obj.artifact_group:
            return []
        versions = obj.artifact_group.versions.filter(is_active=True).order_by('version')
        return [
            {'id': v.id, 'version': v.version, 'createdAt': v.created_at.isoformat()}
            for v in versions
        ]


class ArtifactListSerializer(serializers.ModelSerializer):
    """
    Lightweight artifact serializer for lists (content loaded on demand).
    
    Used in conversation history and artifact lists.
    """

    conversation_id = serializers.CharField(source='conversation.conversation_id', read_only=True)
    
    # Versioning fields
    parent_artifact_id = serializers.PrimaryKeyRelatedField(
        source='parent_artifact',
        read_only=True,
    )
    artifact_group_id = serializers.PrimaryKeyRelatedField(
        source='artifact_group',
        read_only=True,
    )

    class Meta:
        model = Artifact
        fields = [
            'id',
            'conversation_id',
            'artifact_type',
            'title',
            'filename',
            'content_type',
            'source_tool',
            'status',
            # Versioning
            'version',
            'parent_artifact_id',
            'artifact_group_id',
            'created_at',
        ]
        read_only_fields = fields


class PublicFeedbackSourceSerializer(serializers.ModelSerializer):
    class Meta:
        model = PublicFeedbackSource
        fields = [
            'id',
            'title',
            'url',
            'source_type',
            'page_date',
            'snippet',
        ]

class FeedbackSerializer(serializers.ModelSerializer):
    """Serializer for general user feedback from the FAB widget."""

    class Meta:
        model = Feedback
        fields = [
            'id',
            'emotion',
            'category',
            'message',
            'screenshot',
            'page',
            'browser_info',
            'created_at',
        ]
        read_only_fields = ['id', 'created_at']


class PublicFeedbackSourceClusterSerializer(serializers.ModelSerializer):
    sources = PublicFeedbackSourceSerializer(many=True, read_only=True)

    class Meta:
        model = PublicFeedbackSourceCluster
        fields = [
            'id',
            'cluster_index',
            'canonical_title',
            'canonical_url',
            'identifier',
            'sources',
        ]


class ModelCardDataSerializer(serializers.ModelSerializer):
    source_clusters = PublicFeedbackSourceClusterSerializer(many=True, read_only=True)

    class Meta:
        model = ModelCardData
        fields = [
            'id',
            'name',
            'slug',
            'provider_name',
            'name_variants',
            'public_feedback',
            'source_clusters',
            'llm',
            'created_at',
            'updated_at',
        ]

class ModelCardDataListSerializer(serializers.ModelSerializer):
    """Lighter serializer for list view (excludes full public_feedback blob)."""
    has_public_feedback = serializers.SerializerMethodField()

    class Meta:
        model = ModelCardData
        fields = [
            'id',
            'name',
            'slug',
            'provider_name',
            'llm',
            'has_public_feedback',
            'updated_at',
        ]

    def get_has_public_feedback(self, obj):
        return bool(obj.public_feedback)
