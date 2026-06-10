"""
Memory API Serializers

Serializers for memory-related API endpoints.
"""
from rest_framework import serializers


class MemoryItemSerializer(serializers.Serializer):
    """Serializer for a single memory item."""
    
    id = serializers.CharField(read_only=True)
    memory_type = serializers.CharField(read_only=True)
    content = serializers.CharField(source="summary", read_only=True)
    categories = serializers.ListField(
        child=serializers.CharField(),
        read_only=True,
    )
    created_at = serializers.DateTimeField(read_only=True, required=False)
    updated_at = serializers.DateTimeField(read_only=True, required=False)
    score = serializers.FloatField(read_only=True, required=False)


class MemorySearchRequestSerializer(serializers.Serializer):
    """Serializer for search request input."""
    
    query = serializers.CharField(
        required=True,
        min_length=1,
        max_length=1000,
        help_text="The search query to find relevant memories",
    )


class MemorySearchResultSerializer(serializers.Serializer):
    """Serializer for individual search result."""
    
    id = serializers.CharField(read_only=True)
    memory_type = serializers.CharField(read_only=True)
    content = serializers.CharField(source="summary", read_only=True)
    categories = serializers.ListField(
        child=serializers.CharField(),
        read_only=True,
    )
    score = serializers.FloatField(read_only=True, help_text="Relevance score")


class MemorySearchResponseSerializer(serializers.Serializer):
    """Serializer for search response."""
    
    query = serializers.CharField(read_only=True)
    items = MemorySearchResultSerializer(many=True, read_only=True)
    categories = serializers.ListField(
        child=serializers.DictField(),
        read_only=True,
        help_text="Category-level summaries",
    )


class MemoryImportItemSerializer(serializers.Serializer):
    """Serializer for one imported memory item."""

    memory_type = serializers.CharField(
        required=False,
        allow_blank=True,
        default="profile",
    )
    content = serializers.CharField(
        required=True, allow_blank=False, trim_whitespace=True
    )
    categories = serializers.ListField(
        child=serializers.CharField(allow_blank=False, trim_whitespace=True),
        required=False,
        default=list,
    )


class MemoryImportRequestSerializer(serializers.Serializer):
    """Serializer for memory import input."""

    items = MemoryImportItemSerializer(many=True, allow_empty=False)


class MemoryImportResponseSerializer(serializers.Serializer):
    """Serializer for memory import response."""

    items_created = serializers.IntegerField(read_only=True)
    message = serializers.CharField(read_only=True)


class SeedResponseSerializer(serializers.Serializer):
    """Serializer for seeding response."""
    
    items_created = serializers.IntegerField(read_only=True)
    message = serializers.CharField(read_only=True)


class ClearResponseSerializer(serializers.Serializer):
    """Serializer for clear all response."""
    
    success = serializers.BooleanField(read_only=True)
    message = serializers.CharField(read_only=True)
