from rest_framework import serializers
from conversations.models import LLM, Message, Conversation, Snippet, Artifact, ArtifactCheckpoint
from files.api.serializers import FileSerializer, TagSerializer
from prompts.models import Prompt
from prompts.api.serializers import PromptSerializer
from users.constants import VectorDBChoice

class LLMSerializer(serializers.ModelSerializer):
    class Meta:
        model = LLM
        fields = ['id', 'name', 'identifier', 'provider', 'description', 'is_reasoning', 'is_image_generator', 'input_token_rate_per_million', 'output_token_rate_per_million']

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

    def get_user(self, obj):
        """Return user email or None for anonymous conversations."""
        return obj.user.email if obj.user else None

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
        ]
        read_only_fields = ['created_at', 'user', 'prompt']

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
    llm = serializers.PrimaryKeyRelatedField(read_only=True, allow_null=True)
    artifactId = serializers.SerializerMethodField()

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
            'artifactId',
        ]
        read_only_fields = ['id', 'created_at', 'sender_name', 'files', 'tags', 'snippets', 'input_tokens', 'output_tokens', 'cost', 'artifactId']

    def get_artifactId(self, obj):
        """Get the ID of the first active artifact linked to this message."""
        # Use the reverse relation from Artifact -> Message
        # Filter by is_active=True since the reverse relation uses the default manager
        # which doesn't filter by is_active automatically
        artifact = obj.artifacts.filter(is_active=True).first()
        return str(artifact.id) if artifact else None


class ArtifactCheckpointSerializer(serializers.ModelSerializer):
    """Serializer for artifact checkpoints."""

    class Meta:
        model = ArtifactCheckpoint
        fields = [
            'id',
            'content_snapshot',
            'current_section',
            'iteration_count',
            'state_data',
            'created_at',
        ]
        read_only_fields = fields


class ArtifactSerializer(serializers.ModelSerializer):
    """Serializer for artifacts with progress tracking and versioning."""

    # IDs returned as natural types (integers) from Django
    conversation_id = serializers.CharField(source='conversation.conversation_id', read_only=True)
    message_id = serializers.PrimaryKeyRelatedField(source='message', read_only=True)
    progress = serializers.FloatField(read_only=True)
    sections_remaining = serializers.IntegerField(read_only=True)
    word_count = serializers.IntegerField(read_only=True)
    latest_checkpoint = serializers.SerializerMethodField()
    
    # Versioning fields - use PrimaryKeyRelatedField for natural integer IDs
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
            'language',
            'outline',
            'content',
            'estimated_sections',
            'current_section',
            'status',
            'version',
            'metadata',
            'progress',
            'sections_remaining',
            'word_count',
            'latest_checkpoint',
            # Versioning fields
            'parent_artifact_id',
            'artifact_group_id',
            'version_history',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id',
            'conversation_id',
            'message_id',
            'version',
            'progress',
            'sections_remaining',
            'word_count',
            'latest_checkpoint',
            'parent_artifact_id',
            'artifact_group_id',
            'version_history',
            'created_at',
            'updated_at',
        ]

    def get_latest_checkpoint(self, obj):
        """Get the latest checkpoint for this artifact."""
        checkpoint = obj.checkpoints.order_by('-created_at').first()
        if checkpoint:
            return ArtifactCheckpointSerializer(checkpoint).data
        return None

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
    """Lightweight serializer for artifact lists (without full content)."""

    # IDs returned as natural types (integers) from Django
    conversation_id = serializers.CharField(source='conversation.conversation_id', read_only=True)
    progress = serializers.FloatField(read_only=True)
    word_count = serializers.IntegerField(read_only=True)
    # Content fields - empty strings to prevent frontend crashes, full content loaded on demand
    outline = serializers.SerializerMethodField()
    content = serializers.SerializerMethodField()
    # Versioning fields - use PrimaryKeyRelatedField for natural integer IDs
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
            'language',
            'outline',
            'content',
            'status',
            'estimated_sections',
            'current_section',
            'progress',
            'word_count',
            # Versioning fields
            'version',
            'parent_artifact_id',
            'artifact_group_id',
            'created_at',
        ]
        read_only_fields = fields
    
    def get_outline(self, obj):
        """Return empty string - full outline loaded on demand."""
        return ''
    
    def get_content(self, obj):
        """Return empty string - full content loaded on demand."""
        return ''



