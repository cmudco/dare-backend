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
    prompt = models.ForeignKey(
        'prompts.Prompt',
        on_delete=models.PROTECT,
        help_text="Prompt template for this step"
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

    def to_dict(self):
        """Convert to React Flow node data format."""
        return {
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
        }

    def __str__(self):
        return f"Step {self.step_number}: {self.prompt.title if self.prompt else 'No Prompt'}"


class StartNodeData(BaseNodeData):
    """Data model for 'start' type nodes."""
    title = models.CharField(
        max_length=255,
        help_text="Workflow title"
    )
    description = models.TextField(
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



class ConditionalNodeData(BaseNodeData):
    """Data model for 'conditional' type nodes."""
    custom_prompt = models.TextField(
        default='Evaluate the input and choose the appropriate route.',
        help_text="Custom evaluation prompt for routing decision"
    )
    route_a_name = models.CharField(
        max_length=100,
        default='Route A',
        help_text="Name for route A output"
    )
    route_b_name = models.CharField(
        max_length=100,
        default='Route B',
        help_text="Name for route B output"
    )
    route_a_description = models.TextField(
        blank=True,
        help_text="Optional description for route A"
    )
    route_b_description = models.TextField(
        blank=True,
        help_text="Optional description for route B"
    )
    step_number = models.PositiveIntegerField(
        help_text="Step number for execution ordering"
    )

    def to_dict(self):
        return {
            'customPrompt': self.custom_prompt,
            'routeAName': self.route_a_name,
            'routeBName': self.route_b_name,
            'routeADescription': self.route_a_description,
            'routeBDescription': self.route_b_description,
            'stepNumber': self.step_number,
        }

    def __str__(self):
        return f"Conditional {self.step_number}: {self.route_a_name} / {self.route_b_name}"