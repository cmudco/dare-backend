from rest_framework import serializers
from conversations.models import LLM, Message, Conversation, Snippet
from files.api.serializers import FileSerializer

class LLMSerializer(serializers.ModelSerializer):
    class Meta:
        model = LLM
        fields = ['id', 'name', 'identifier', 'description']

class ConversationSerializer(serializers.ModelSerializer):
    user = serializers.ReadOnlyField(source='user.email')

    class Meta:
        model = Conversation
        fields = ['conversation_id', 'title', 'created_at', 'user']
        read_only_fields = ['conversation_id', 'created_at', 'user']

class SnippetSerializer(serializers.ModelSerializer):
    file = FileSerializer(read_only=True)

    class Meta:
        model = Snippet
        fields = ['id', 'file', 'text', 'similarity_score', 'chunk_index']
        read_only_fields = ['id', 'file', 'text', 'similarity_score', 'chunk_index']

class MessageSerializer(serializers.ModelSerializer):
    sender_name = serializers.ReadOnlyField(read_only=True)
    files = FileSerializer(many=True, read_only=True)
    file_ids = serializers.ListField(
        child=serializers.IntegerField(),
        write_only=True,
        required=False
    )
    snippets = SnippetSerializer(many=True, read_only=True)

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
            'created_at'
        ]
        read_only_fields = ['id', 'created_at', 'sender_name', 'files', 'snippets']