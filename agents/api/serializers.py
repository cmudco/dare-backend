from rest_framework import serializers
from agents.models import Agent, AgentNodeData, TemplateAgentNodeData


class AgentSerializer(serializers.ModelSerializer):
    """Serializer for Agent model with nested relationships."""
    user = serializers.ReadOnlyField(source='user.email')
    prompt_title = serializers.CharField(source='prompt.title', read_only=True)
    llm_name = serializers.CharField(source='llm.name', read_only=True)

    class Meta:
        model = Agent
        fields = [
            'id', 'user', 'name', 'description', 'prompt', 'prompt_title',
            'content_files', 'embedding_files', 'llm', 'llm_name',
            'max_tokens', 'temperature', 'max_context_snippets',
            'document_similarity_threshold', 'enable_web_search',
            'version', 'parent', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'user', 'version']


class AgentListSerializer(serializers.ModelSerializer):
    """Serializer for agent list with all necessary fields for table display."""
    user = serializers.ReadOnlyField(source='user.email')
    prompt_title = serializers.CharField(source='prompt.title', read_only=True)
    llm_name = serializers.CharField(source='llm.name', read_only=True, required=False)

    class Meta:
        model = Agent
        fields = [
            'id', 'name', 'description', 'user', 'prompt', 'prompt_title',
            'content_files', 'embedding_files', 'llm', 'llm_name',
            'max_tokens', 'temperature', 'max_context_snippets',
            'document_similarity_threshold', 'enable_web_search', 'version',
            'parent', 'created_at', 'updated_at'
        ]


class AgentNodeDataSerializer(serializers.ModelSerializer):
    """Serializer for AgentNodeData."""
    agent_name = serializers.CharField(source='agent.name', read_only=True)
    prompt_title = serializers.CharField(source='prompt.title', read_only=True, required=False)
    llm_name = serializers.CharField(source='llm.name', read_only=True, required=False)

    class Meta:
        model = AgentNodeData
        fields = [
            'id', 'agent', 'agent_name', 'name', 'description',
            'prompt', 'prompt_title', 'content_files', 'embedding_files',
            'llm', 'llm_name', 'agent_number', 'max_tokens', 'temperature',
            'max_context_snippets', 'document_similarity_threshold',
            'use_previous_agent_files', 'use_previous_agent_embeddings',
            'text_input', 'use_structured_output_node', 'enable_web_search',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class TemplateAgentNodeDataSerializer(serializers.ModelSerializer):
    """Serializer for TemplateAgentNodeData."""
    agent_name = serializers.CharField(source='agent.name', read_only=True)

    class Meta:
        model = TemplateAgentNodeData
        fields = [
            'id', 'agent', 'agent_name', 'name', 'description',
            'agent_number', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
