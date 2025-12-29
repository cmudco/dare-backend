"""
Workflow Citation Models

Models for storing web search citations and RAG snippets associated with
workflow step executions. Mirrors the conversation implementation for consistency.
"""

from django.db import models
from common.managers import ActiveObjectsManager
from common.models import BaseModel


class WorkflowStepSnippet(BaseModel):
    """
    Model to track retrieved snippets from vector search for workflow steps.
    Analogous to conversations.models.Snippet for Message.
    """
    workflow_run_step = models.ForeignKey(
        'workflows.WorkflowRunStep',
        on_delete=models.CASCADE,
        related_name="snippets",
        help_text="The workflow run step this snippet was retrieved for."
    )
    file = models.ForeignKey(
        'files.File',
        on_delete=models.CASCADE,
        related_name="workflow_step_snippets",
        help_text="The file this snippet belongs to."
    )
    text = models.TextField(
        help_text="The text content of the snippet (chunk)."
    )
    similarity_score = models.FloatField(
        help_text="The similarity score of the snippet to the query."
    )
    chunk_index = models.PositiveIntegerField(
        help_text="The index of the chunk in the original file."
    )
    vector_db_source = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        help_text="The vector database source (e.g., 'pinecone', 'weaviate')."
    )

    active_objects = ActiveObjectsManager()

    def __str__(self):
        return f"Snippet for WorkflowRunStep {self.workflow_run_step.id} from File {self.file.id}"


class WorkflowStepWebSearchSource(BaseModel):
    """
    Model to store web search sources/citations from LLM responses in workflow steps.
    Analogous to conversations.models.WebSearchSource for Message.

    Provider-specific fields:
    - OpenAI: url, title (from annotations)
    - Claude: url, title, cited_text, page_age (from web_search_tool_result)
    - Gemini: url, title (from grounding_metadata.grounding_chunks)
    """
    workflow_run_step = models.ForeignKey(
        'workflows.WorkflowRunStep',
        on_delete=models.CASCADE,
        related_name="web_search_sources",
        help_text="The workflow run step this source was cited in."
    )
    url = models.URLField(
        max_length=2048,
        help_text="The URL of the source."
    )
    title = models.CharField(
        max_length=500,
        blank=True,
        help_text="The title of the source page."
    )
    cited_text = models.TextField(
        blank=True,
        help_text="The text that was cited from this source (Claude only)."
    )
    page_age = models.CharField(
        max_length=100,
        blank=True,
        help_text="When the page was last updated (Claude only)."
    )
    provider = models.CharField(
        max_length=20,
        blank=True,
        help_text="The LLM provider that returned this source (openai, claude, gemini)."
    )

    active_objects = ActiveObjectsManager()

    class Meta:
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['workflow_run_step'], name='wf_websearch_step_idx'),
        ]

    def __str__(self):
        return f"WebSearchSource for WorkflowRunStep {self.workflow_run_step.id}: {self.title or self.url[:50]}"
