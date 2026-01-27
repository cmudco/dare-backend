"""
Serializers for Voice API.
"""

from rest_framework import serializers
from voice.models import VoiceAgent, VoiceConversation


class VoiceAgentSerializer(serializers.ModelSerializer):
    """Full serializer for VoiceAgent with all fields."""
    user_email = serializers.CharField(source='user.email', read_only=True)
    conversation_count = serializers.SerializerMethodField()

    class Meta:
        model = VoiceAgent
        fields = [
            'id', 'user', 'user_email', 'name', 'description',
            'elevenlabs_agent_id', 'voice_id', 'system_prompt',
            'first_message', 'temperature', 'max_duration_seconds',
            'exam_title', 'exam_instructions', 'status',
            'conversation_config', 'conversation_count', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'user', 'elevenlabs_agent_id', 'created_at', 'updated_at']

    def get_conversation_count(self, obj):
        return obj.conversations.count()


class VoiceAgentListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for agent list view."""
    user_email = serializers.CharField(source='user.email', read_only=True)

    class Meta:
        model = VoiceAgent
        fields = [
            'id', 'user', 'user_email', 'name', 'description',
            'status', 'exam_title', 'created_at'
        ]


class VoiceAgentCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating a new VoiceAgent."""

    class Meta:
        model = VoiceAgent
        fields = [
            'name', 'description', 'voice_id', 'system_prompt',
            'first_message', 'temperature', 'max_duration_seconds',
            'exam_title', 'exam_instructions', 'status', 'conversation_config'
        ]


class VoiceAgentUpdateSerializer(serializers.ModelSerializer):
    """Serializer for updating a VoiceAgent."""

    class Meta:
        model = VoiceAgent
        fields = [
            'name', 'description', 'voice_id', 'system_prompt',
            'first_message', 'temperature', 'max_duration_seconds',
            'exam_title', 'exam_instructions', 'status', 'conversation_config'
        ]


class VoiceConversationSerializer(serializers.ModelSerializer):
    """Serializer for voice conversations."""
    agent_name = serializers.CharField(source='agent.name', read_only=True)
    user_email = serializers.CharField(source='user.email', read_only=True)

    class Meta:
        model = VoiceConversation
        fields = [
            'id', 'agent', 'agent_name', 'user', 'user_email',
            'elevenlabs_conversation_id', 'started_at', 'ended_at',
            'duration_seconds', 'status', 'transcript', 'created_at'
        ]
        read_only_fields = [
            'id', 'elevenlabs_conversation_id', 'started_at',
            'ended_at', 'duration_seconds', 'transcript', 'created_at'
        ]


class VoiceConversationListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for conversation list."""
    agent_name = serializers.CharField(source='agent.name', read_only=True)
    user_email = serializers.CharField(source='user.email', read_only=True)

    class Meta:
        model = VoiceConversation
        fields = [
            'id', 'agent', 'agent_name', 'user_email',
            'started_at', 'ended_at', 'duration_seconds', 'status'
        ]


class SignedUrlResponseSerializer(serializers.Serializer):
    """Response serializer for signed URL endpoint."""
    signed_url = serializers.CharField()
    agent_id = serializers.CharField()


class StartConversationSerializer(serializers.Serializer):
    """Request serializer for starting a conversation."""
    conversation_id = serializers.CharField(required=True)


class EndConversationSerializer(serializers.Serializer):
    """Request serializer for ending a conversation."""
    conversation_id = serializers.CharField(required=True)


class VoiceSerializer(serializers.Serializer):
    """Serializer for ElevenLabs voice list."""
    voice_id = serializers.CharField()
    name = serializers.CharField()
    preview_url = serializers.CharField(required=False, allow_null=True)
    category = serializers.CharField(required=False, allow_null=True)
