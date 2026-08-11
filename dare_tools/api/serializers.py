"""
DARE Tools API serializers.
"""

from rest_framework import serializers
from dare_tools.models import DareTool, DareToolExecution


class DareToolSerializer(serializers.ModelSerializer):
    """Serializer for DARE tool definitions."""
    
    class Meta:
        model = DareTool
        fields = [
            'id',
            'name',
            'slug',
            'description',
            'icon',
            'category',
            'function_name',
            'is_active',
            'created_at',
        ]
        read_only_fields = fields


class DareToolExecutionSerializer(serializers.ModelSerializer):
    """Serializer for DARE tool execution records."""
    
    tool_name = serializers.CharField(source='tool.name', read_only=True)
    tool_slug = serializers.CharField(source='tool.slug', read_only=True)
    
    class Meta:
        model = DareToolExecution
        fields = [
            'id',
            'tool',
            'tool_name',
            'tool_slug',
            'tool_call_id',
            'arguments',
            'status',
            'result',
            'error_message',
            'execution_time_ms',
            'created_at',
        ]
        read_only_fields = fields
