"""
Citation Serialization Helpers

Provides serialization utilities for workflow step citations (snippets and web search sources).
Separated from serializers.py to avoid circular imports with NodeExecutionStateBuilder.
"""

from typing import List, Dict, Any

from rest_framework import serializers
from files.api.serializers import FileSerializer
from users.constants import VectorDBChoice
from workflows.models import WorkflowStepSnippet, WorkflowStepWebSearchSource


class WorkflowStepSnippetSerializer(serializers.ModelSerializer):
    """Serializer for workflow step RAG snippets."""
    file = FileSerializer(read_only=True)
    vector_db_source = serializers.SerializerMethodField()

    class Meta:
        model = WorkflowStepSnippet
        fields = ['id', 'file', 'text', 'similarity_score', 'chunk_index', 'vector_db_source']
        read_only_fields = fields

    def get_vector_db_source(self, obj):
        """Return the human-readable name of the vector database source."""
        if obj.vector_db_source:
            return dict(VectorDBChoice.choices).get(obj.vector_db_source, obj.vector_db_source)
        return "Unknown"


class WorkflowStepWebSearchSourceSerializer(serializers.ModelSerializer):
    """
    Serializer for workflow step web search sources/citations.

    Returns the essential fields for displaying source links in the UI,
    similar to how WorkflowStepSnippetSerializer returns document context.
    """

    class Meta:
        model = WorkflowStepWebSearchSource
        fields = ['id', 'url', 'title', 'cited_text', 'page_age', 'provider']
        read_only_fields = fields


def serialize_step_citations(step) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Serialize snippets and web search sources for a workflow run step.

    Args:
        step: WorkflowRunStep instance

    Returns:
        Tuple of (snippets_data, web_search_sources_data)
    """
    snippets_data = WorkflowStepSnippetSerializer(
        step.snippets.all(), many=True
    ).data
    web_search_sources_data = WorkflowStepWebSearchSourceSerializer(
        step.web_search_sources.all(), many=True
    ).data
    return snippets_data, web_search_sources_data
