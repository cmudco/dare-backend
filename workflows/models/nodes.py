from dataclasses import dataclass, field
from typing import Iterable

from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from workflows.node_handler_constants import StepNodeDefaults, FileNodeDefaults
from workflows.constants import Mode, RetrievalMode, QuerySource


@dataclass(frozen=True)
class NodeFileReference:
    file_id: int
    file_name: str


@dataclass(frozen=True)
class PrefetchedNodeFileRelations:
    step_content_files: dict[int, tuple[NodeFileReference, ...]] = field(default_factory=dict)
    step_embedding_files: dict[int, tuple[NodeFileReference, ...]] = field(default_factory=dict)
    step_tags: dict[int, tuple[NodeFileReference, ...]] = field(default_factory=dict)
    file_node_files: dict[int, tuple[NodeFileReference, ...]] = field(default_factory=dict)

    def get_step_content_files(self, node_data_id: int) -> tuple[NodeFileReference, ...]:
        return self.step_content_files.get(node_data_id, ())

    def get_step_embedding_files(self, node_data_id: int) -> tuple[NodeFileReference, ...]:
        return self.step_embedding_files.get(node_data_id, ())

    def get_step_tags(self, node_data_id: int) -> tuple[NodeFileReference, ...]:
        return self.step_tags.get(node_data_id, ())

    def get_file_node_files(self, node_data_id: int) -> tuple[NodeFileReference, ...]:
        return self.file_node_files.get(node_data_id, ())


def _group_file_rows(rows: Iterable[tuple[int, int, str]]) -> dict[int, tuple[NodeFileReference, ...]]:
    grouped: dict[int, list[NodeFileReference]] = {}
    for node_data_id, file_id, file_name in rows:
        grouped.setdefault(node_data_id, []).append(
            NodeFileReference(file_id=file_id, file_name=file_name)
        )
    return {
        node_data_id: tuple(file_refs)
        for node_data_id, file_refs in grouped.items()
    }


def build_prefetched_node_file_relations(nodes) -> PrefetchedNodeFileRelations:
    """Precompute all node/file M2M data needed for node serialization."""
    step_node_data_ids = [node.data_object_id for node in nodes if node.node_type == "step"]
    file_node_data_ids = [node.data_object_id for node in nodes if node.node_type == "file"]

    step_content_files: dict[int, tuple[NodeFileReference, ...]] = {}
    step_embedding_files: dict[int, tuple[NodeFileReference, ...]] = {}
    step_tags: dict[int, tuple[NodeFileReference, ...]] = {}
    file_node_files: dict[int, tuple[NodeFileReference, ...]] = {}

    if step_node_data_ids:
        step_content_files = _group_file_rows(
            StepNodeData.content_files.through.objects.filter(
                stepnodedata_id__in=step_node_data_ids
            ).values_list("stepnodedata_id", "file_id", "file__name")
        )
        step_embedding_files = _group_file_rows(
            StepNodeData.embedding_files.through.objects.filter(
                stepnodedata_id__in=step_node_data_ids
            ).values_list("stepnodedata_id", "file_id", "file__name")
        )
        step_tags = _group_file_rows(
            StepNodeData.tags.through.objects.filter(
                stepnodedata_id__in=step_node_data_ids
            ).values_list("stepnodedata_id", "tag_id", "tag__label")
        )

    if file_node_data_ids:
        file_node_files = _group_file_rows(
            FileNodeData.files.through.objects.filter(
                filenodedata_id__in=file_node_data_ids
            ).values_list("filenodedata_id", "file_id", "file__name")
        )

    return PrefetchedNodeFileRelations(
        step_content_files=step_content_files,
        step_embedding_files=step_embedding_files,
        step_tags=step_tags,
        file_node_files=file_node_files,
    )


def _serialize_file_refs(file_refs: tuple[NodeFileReference, ...]) -> tuple[list[int], dict[int, str]]:
    return (
        [file_ref.file_id for file_ref in file_refs],
        {file_ref.file_id: file_ref.file_name for file_ref in file_refs},
    )


class BaseNodeData(models.Model):
    """Base class for type-safe node data storage."""
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

    def to_dict(self, relations: PrefetchedNodeFileRelations | None = None):
        """Convert to dict for API serialization. Override in subclasses."""
        raise NotImplementedError("Subclasses must implement to_dict()")


class StepNodeData(BaseNodeData):
    """Data model for 'step' type nodes - replaces Step model entirely."""
    label = models.CharField(
        max_length=255,
        blank=True,
        default='',
        help_text="Display label (e.g. 'Step 1', 'Research')"
    )
    agent = models.ForeignKey(
        'agents.Agent',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Agent template that was loaded for this step"
    )
    prompt = models.ForeignKey(
        'prompts.Prompt',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Prompt template for this step (required for execution)"
    )
    content_files = models.ManyToManyField(
        'files.File',
        related_name='step_node_content',
        blank=True,
        help_text="Files to be processed with full content"
    )
    embedding_files = models.ManyToManyField(
        'files.File',
        related_name='step_node_embeddings',
        blank=True,
        help_text="Files to be processed using embeddings/vector search"
    )
    tags = models.ManyToManyField(
        'files.Tag',
        related_name='step_nodes',
        blank=True,
        help_text="Tags to filter files for this step"
    )
    llm = models.ForeignKey(
        'conversations.LLM',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Language model for this step"
    )
    max_tokens = models.PositiveIntegerField(
        default=StepNodeDefaults.MAX_TOKENS,
        help_text="Maximum tokens for LLM response"
    )
    temperature = models.FloatField(
        default=StepNodeDefaults.TEMPERATURE,
        validators=[MinValueValidator(0.0), MaxValueValidator(2.0)],
        help_text="Temperature setting for the LLM"
    )
    max_context_snippets = models.PositiveIntegerField(
        default=StepNodeDefaults.MAX_CONTEXT_SNIPPETS,
        help_text="Maximum number of context snippets to retrieve"
    )
    document_similarity_threshold = models.FloatField(
        default=StepNodeDefaults.DOCUMENT_SIMILARITY_THRESHOLD,
        help_text="Similarity threshold for document retrieval"
    )
    use_previous_step_files = models.BooleanField(
        default=False,
        help_text="Inherit files from previous step"
    )
    use_previous_step_embeddings = models.BooleanField(
        default=False,
        help_text="Inherit embeddings from previous step"
    )
    text_input = models.TextField(
        blank=True,
        default='',
        help_text="Optional text input to be passed directly to the LLM"
    )
    enable_web_search = models.BooleanField(
        default=False,
        help_text="If true, enable web search for this step's LLM"
    )

    def to_dict(self, relations: PrefetchedNodeFileRelations | None = None):
        """Convert to React Flow node data format."""
        node_relations = relations or PrefetchedNodeFileRelations()
        content_file_refs = node_relations.get_step_content_files(self.id)
        embedding_file_refs = node_relations.get_step_embedding_files(self.id)
        tag_refs = node_relations.get_step_tags(self.id)
        content_file_ids, content_file_names = _serialize_file_refs(content_file_refs)
        embedding_file_ids, embedding_file_names = _serialize_file_refs(embedding_file_refs)
        tag_ids, tag_names = _serialize_file_refs(tag_refs)
        return {
            'label': self.label,
            'agent': self.agent.id if self.agent else None,
            'prompt': self.prompt.id if self.prompt else None,
            'promptTitle': self.prompt.title if self.prompt else None,
            'contentFiles': content_file_ids,
            'contentFileNames': content_file_names,
            'embeddingFiles': embedding_file_ids,
            'embeddingFileNames': embedding_file_names,
            'tags': tag_ids,
            'tagNames': tag_names,
            'llm': self.llm.id if self.llm else None,
            'maxTokens': self.max_tokens,
            'temperature': self.temperature,
            'maxContextSnippets': self.max_context_snippets,
            'documentSimilarityThreshold': self.document_similarity_threshold,
            'usePreviousStepFiles': self.use_previous_step_files,
            'usePreviousStepEmbeddings': self.use_previous_step_embeddings,
            'textInput': self.text_input,
            'enableWebSearch': self.enable_web_search,
        }

    def __str__(self):
        return f"StepNodeData {self.pk}: {self.prompt.title if self.prompt else 'No Prompt'}"


class StartNodeData(BaseNodeData):
    """Data model for 'start' type nodes."""
    title = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="Workflow title"
    )
    description = models.TextField(
        blank=True,
        null=True,
        help_text="Workflow description"
    )
    mode = models.CharField(
        max_length=20,
        choices=Mode.choices,
        default=Mode.SEQUENTIAL,
        help_text="Workflow execution mode"
    )

    def to_dict(self, relations: PrefetchedNodeFileRelations | None = None):
        return {
            'title': self.title,
            'description': self.description,
            'mode': self.mode,
        }

    def __str__(self):
        return f"Start: {self.title}"


class ChatOutputNodeData(BaseNodeData):
    """Data model for 'chatOutput' type nodes."""
    label = models.CharField(
        max_length=255,
        blank=True,
        default='',
        help_text="Display label (e.g. 'Step 1 Output')"
    )
    status = models.CharField(
        max_length=20,
        blank=True,
        help_text="Execution status"
    )
    response = models.TextField(
        blank=True,
        help_text="Step execution response"
    )
    error = models.TextField(
        blank=True,
        help_text="Error message if step failed"
    )

    def to_dict(self, relations: PrefetchedNodeFileRelations | None = None):
        return {
            'label': self.label,
            'status': self.status,
            'response': self.response,
            'error': self.error,
        }

    def __str__(self):
        return f"ChatOutputNodeData {self.pk}"



class StructuredOutputNodeData(BaseNodeData):
    """Data model for 'structuredOutput' type nodes - independent routing decision nodes."""
    label = models.CharField(
        max_length=255,
        blank=True,
        default='',
        help_text="Display label (e.g. 'Router 1')"
    )
    prompt = models.ForeignKey(
        'prompts.Prompt',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Optional prompt template for routing evaluation (falls back to base prompt if not provided)"
    )
    routes = models.JSONField(
        default=list,
        blank=True,
        help_text="List of route definitions: [{'name': '1', 'description': '...'}, ...]"
    )
    require_human_validation = models.BooleanField(
        default=False,
        help_text="If true, pause execution and ask user to choose route"
    )
    llm = models.ForeignKey(
        'conversations.LLM',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Language model for routing evaluation"
    )
    text_input = models.TextField(
        blank=True,
        default='',
        help_text="Optional text input to be passed directly to the LLM for routing decision"
    )

    def get_routes(self):
        """Get routes for structured output node."""
        return self.routes if self.routes else []

    def to_dict(self, relations: PrefetchedNodeFileRelations | None = None):
        return {
            'label': self.label,
            'prompt': self.prompt.id if self.prompt else None,
            'promptTitle': self.prompt.title if self.prompt else None,
            'routes': self.get_routes(),
            'requireHumanValidation': self.require_human_validation,
            'llm': self.llm.id if self.llm else None,
            'textInput': self.text_input,
        }

    def __str__(self):
        routes = self.get_routes()
        route_names = ' / '.join([r['name'] for r in routes[:3]])
        if len(routes) > 3:
            route_names += f' (+{len(routes) - 3} more)'
        return f"StructuredOutputNodeData {self.pk}: {route_names}"


class NotesNodeData(BaseNodeData):
    """
    Data model for 'notes' type nodes.

    Documentation/comment nodes for workflow annotation.
    Non-executable - purely for user documentation.
    """
    content = models.TextField(
        blank=True,
        default='',
        help_text="Note content/documentation text"
    )

    def to_dict(self, relations: PrefetchedNodeFileRelations | None = None) -> dict:
        """Convert to React Flow node data format (camelCase)."""
        return {
            'content': self.content,
        }

    def __str__(self) -> str:
        preview = self.content[:30] if self.content else 'Empty'
        return f"Note: {preview}..."


class FileNodeData(BaseNodeData):
    """
    Data model for 'file' type nodes — dedicated file retrieval.

    Simpler than step nodes: no LLM processing, purely retrieval.
    Supports embeddings (vector search) and full content modes.
    """
    label = models.CharField(
        max_length=255,
        blank=True,
        default='',
        help_text="Display label (e.g. 'File 1')"
    )
    files = models.ManyToManyField(
        'files.File',
        related_name='file_node_files',
        blank=True,
        help_text="Files to retrieve content from"
    )
    retrieval_mode = models.CharField(
        max_length=20,
        choices=RetrievalMode.choices,
        default=RetrievalMode.EMBEDDINGS,
        help_text="How to retrieve file content"
    )
    similarity_threshold = models.FloatField(
        default=FileNodeDefaults.SIMILARITY_THRESHOLD,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
        help_text="Minimum similarity score for vector search results (0.0-1.0)"
    )
    max_results = models.PositiveIntegerField(
        default=FileNodeDefaults.MAX_RESULTS,
        help_text="Maximum number of snippets to retrieve"
    )
    query_source = models.CharField(
        max_length=20,
        choices=QuerySource.choices,
        default=QuerySource.PREVIOUS_STEP,
        help_text="Source of query text for vector search"
    )
    text_input = models.TextField(
        blank=True,
        default='',
        help_text="Custom query text (used when query_source is 'text_input')"
    )
    include_metadata = models.BooleanField(
        default=True,
        help_text="Include file names and similarity scores in output"
    )

    def to_dict(self, relations: PrefetchedNodeFileRelations | None = None) -> dict:
        """Convert to React Flow node data format (camelCase)."""
        node_relations = relations or PrefetchedNodeFileRelations()
        file_refs = node_relations.get_file_node_files(self.id)
        file_ids, file_names = _serialize_file_refs(file_refs)
        return {
            'label': self.label,
            'files': file_ids,
            'fileNames': file_names,
            'retrievalMode': self.retrieval_mode,
            'similarityThreshold': self.similarity_threshold,
            'maxResults': self.max_results,
            'querySource': self.query_source,
            'textInput': self.text_input,
            'includeMetadata': self.include_metadata,
        }

    def __str__(self) -> str:
        file_count = self.files.count()
        return f"FileNodeData {self.pk}: {file_count} files, {self.retrieval_mode} mode"
