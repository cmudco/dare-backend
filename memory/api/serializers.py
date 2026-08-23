"""Serializers for the memory API.

Wire casing is handled globally (djangorestframework-camel-case), so these
declare snake_case and the frontend sees ``memoryType`` / ``createdAt``.
"""

from rest_framework import serializers


class MemoryItemSerializer(serializers.Serializer):
    """One compat item — a USER.md line, a fact, or a rule, flattened."""

    id = serializers.CharField(read_only=True)
    memory_type = serializers.CharField(read_only=True)
    content = serializers.CharField(read_only=True)
    categories = serializers.ListField(child=serializers.CharField(), read_only=True)
    created_at = serializers.CharField(read_only=True, required=False, allow_null=True)
    updated_at = serializers.CharField(read_only=True, required=False, allow_null=True)
    score = serializers.FloatField(read_only=True, required=False)
    state = serializers.CharField(read_only=True, required=False, allow_null=True)
    valid_until = serializers.CharField(read_only=True, required=False, allow_null=True)
    replaced_by = serializers.CharField(read_only=True, required=False, allow_null=True)


class MemorySearchRequestSerializer(serializers.Serializer):
    query = serializers.CharField(
        required=True,
        min_length=1,
        max_length=1000,
        help_text="The search query to find relevant memories",
    )


class MemorySearchResponseSerializer(serializers.Serializer):
    query = serializers.CharField(read_only=True)
    items = MemoryItemSerializer(many=True, read_only=True)
    categories = serializers.ListField(
        child=serializers.DictField(),
        read_only=True,
        help_text="Cluster-level summaries shown as 'matched clusters' chips",
    )


class ClearResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField(read_only=True)
    message = serializers.CharField(read_only=True)
