from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from workflows.node_handler_constants import StepNodeDefaults


class BaseNodeData(models.Model):
    """Base class for type-safe node data storage."""
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

    def to_dict(self):
        """Convert to dict for API serialization. Override in subclasses."""
        raise NotImplementedError("Subclasses must implement to_dict()")


class StepNodeData(BaseNodeData):
    """Data model for 'step' type nodes - replaces Step model entirely."""
    agent = models.ForeignKey(
        'agents.Agent',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Agent template that was loaded for this step"
    )
    prompt = models.ForeignKey(
        'prompts.Prompt',
        on_delete=models.PROTECT,
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
    llm = models.ForeignKey(
        'conversations.LLM',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Language model for this step"
    )
    step_number = models.PositiveIntegerField(help_text="Step order in workflow")
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

    def to_dict(self):
        """Convert to React Flow node data format."""
        return {
            'agent': self.agent.id if self.agent else None,
            'prompt': self.prompt.id if self.prompt else None,
            'contentFiles': list(self.content_files.values_list('id', flat=True)),
            'embeddingFiles': list(self.embedding_files.values_list('id', flat=True)),
            'llm': self.llm.id if self.llm else None,
            'stepNumber': self.step_number,
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
        return f"Step {self.step_number}: {self.prompt.title if self.prompt else 'No Prompt'}"


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
        choices=[('sequential', 'Sequential'), ('parallel', 'Parallel')],
        default='sequential',
        help_text="Workflow execution mode"
    )

    def to_dict(self):
        return {
            'title': self.title,
            'description': self.description,
            'mode': self.mode,
        }

    def __str__(self):
        return f"Start: {self.title}"


class ChatOutputNodeData(BaseNodeData):
    """Data model for 'chatOutput' type nodes."""
    step_number = models.PositiveIntegerField(
        help_text="Associated step number for output"
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

    def to_dict(self):
        return {
            'stepNumber': self.step_number,
            'status': self.status,
            'response': self.response,
            'error': self.error,
        }

    def __str__(self):
        return f"Output for Step {self.step_number}"



class StructuredOutputNodeData(BaseNodeData):
    """Data model for 'structuredOutput' type nodes - independent routing decision nodes."""
    prompt = models.ForeignKey(
        'prompts.Prompt',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        help_text="Optional prompt template for routing evaluation (falls back to base prompt if not provided)"
    )
    routes = models.JSONField(
        default=list,
        blank=True,
        help_text="List of route definitions: [{'name': '1', 'description': '...'}, ...]"
    )
    step_number = models.PositiveIntegerField(
        help_text="Step number for execution ordering"
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

    def to_dict(self):
        return {
            'prompt': self.prompt.id if self.prompt else None,
            'routes': self.get_routes(),
            'stepNumber': self.step_number,
            'requireHumanValidation': self.require_human_validation,
            'llm': self.llm.id if self.llm else None,
            'textInput': self.text_input,
        }

    def __str__(self):
        routes = self.get_routes()
        route_names = ' / '.join([r['name'] for r in routes[:3]])
        if len(routes) > 3:
            route_names += f' (+{len(routes) - 3} more)'
        return f"Structured Output {self.step_number}: {route_names}"


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

    def to_dict(self) -> dict:
        """Convert to React Flow node data format (camelCase)."""
        return {
            'content': self.content,
        }

    def __str__(self) -> str:
        preview = self.content[:30] if self.content else 'Empty'
        return f"Note: {preview}..."


