from rest_framework import serializers
from conversations.models import LLM, Message, Conversation, Snippet
from files.api.serializers import FileSerializer
from prompts.models import Prompt
from prompts.api.serializers import PromptSerializer
from users.constants import VectorDBChoice

class LLMSerializer(serializers.ModelSerializer):
    class Meta:
        model = LLM
        fields = ['id', 'name', 'identifier', 'provider', 'description','is_reasoning', 'input_token_rate_per_million', 'output_token_rate_per_million',]

class ConversationSerializer(serializers.ModelSerializer):
    user = serializers.ReadOnlyField(source='user.email')
    prompt = PromptSerializer(read_only=True)
    prompt_id = serializers.PrimaryKeyRelatedField(
        queryset=Prompt.active_objects.all(),
        source='prompt',
        required=False,
        allow_null=True,
        write_only=True
    )

    class Meta:
        model = Conversation
        fields = [
            'conversation_id',
            'title',
            'created_at',
            'user',
            'max_context_snippets',
            'document_similarity_threshold',
            'temperature',
            'max_tokens',
            'prompt',
            'prompt_id',
        ]
        read_only_fields = ['conversation_id', 'created_at', 'user', 'prompt']

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
    file_ids = serializers.ListField(
        child=serializers.IntegerField(),
        write_only=True,
        required=False
    )
    snippets = SnippetSerializer(many=True, read_only=True)
    llm = serializers.PrimaryKeyRelatedField(read_only=True, allow_null=True)

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
            'snippets',
            'created_at',
            'is_liked',
            'is_disliked',
            'is_edited',
            'is_regenerated',
            'original_message',
            'llm',
        ]
        read_only_fields = ['id', 'created_at', 'sender_name', 'files', 'snippets']