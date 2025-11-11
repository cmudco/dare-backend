from django.db import models
from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator

from common.managers import ActiveObjectsManager
from common.models import BaseModel
from workflows.node_handler_constants import StepNodeDefaults


class Agent(BaseModel):
    """
    Model for reusable agents that can be saved and used in workflows.
    Similar to Prompt model, agents encapsulate a complete LLM configuration.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="agents",
        help_text="User who owns this agent."
    )
    name = models.CharField(
        max_length=255,
        help_text="Name of the agent."
    )
    description = models.TextField(
        blank=True,
        default='',
        help_text="Description of the agent."
    )
    prompt = models.ForeignKey(
        'prompts.Prompt',
        on_delete=models.PROTECT,
        help_text="Prompt template for this agent"
    )
    content_files = models.ManyToManyField(
        'files.File',
        related_name='agent_content',
        blank=True,
        help_text="Files to be processed with full content"
    )
    embedding_files = models.ManyToManyField(
        'files.File',
        related_name='agent_embeddings',
        blank=True,
        help_text="Files to be processed using embeddings/vector search"
    )
    llm = models.ForeignKey(
        'conversations.LLM',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Language model for this agent"
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
    enable_web_search = models.BooleanField(
        default=False,
        help_text="If true, enable web search for this agent's LLM"
    )
    version = models.PositiveIntegerField(
        default=1,
        help_text="Version number of the agent. Increments when cloned."
    )
    parent = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='children',
        help_text="Original agent this was cloned from."
    )

    active_objects = ActiveObjectsManager()

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} ({self.user.email})"


class AgentNodeData(models.Model):
    """Data model for 'agent' type nodes in workflows - full configuration."""
    agent = models.ForeignKey(
        'agents.Agent',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        help_text="Agent to use as base configuration"
    )
    name = models.CharField(
        max_length=255,
        blank=True,
        default='',
        help_text="Override agent name for this workflow node"
    )
    description = models.TextField(
        blank=True,
        default='',
        help_text="Override agent description for this workflow node"
    )
    prompt = models.ForeignKey(
        'prompts.Prompt',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        help_text="Prompt template for this agent"
    )
    content_files = models.ManyToManyField(
        'files.File',
        related_name='agent_node_content',
        blank=True,
        help_text="Files to be processed with full content"
    )
    embedding_files = models.ManyToManyField(
        'files.File',
        related_name='agent_node_embeddings',
        blank=True,
        help_text="Files to be processed using embeddings/vector search"
    )
    llm = models.ForeignKey(
        'conversations.LLM',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Language model for this agent"
    )
    agent_number = models.PositiveIntegerField(
        help_text="Agent order in workflow"
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
    use_previous_agent_files = models.BooleanField(
        default=False,
        help_text="Inherit files from previous agent"
    )
    use_previous_agent_embeddings = models.BooleanField(
        default=False,
        help_text="Inherit embeddings from previous agent"
    )
    text_input = models.TextField(
        blank=True,
        default='',
        help_text="Optional text input to be passed directly to the LLM"
    )
    use_structured_output_node = models.BooleanField(
        default=False,
        help_text="If true, this agent uses a separate StructuredOutputNode for routing"
    )
    enable_web_search = models.BooleanField(
        default=False,
        help_text="If true, enable web search for this agent's LLM"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def to_dict(self):
        """Convert to React Flow node data format."""
        return {
            'agent': self.agent.id if self.agent else None,
            'name': self.name,
            'description': self.description,
            'prompt': self.prompt.id if self.prompt else None,
            'contentFiles': list(self.content_files.values_list('id', flat=True)),
            'embeddingFiles': list(self.embedding_files.values_list('id', flat=True)),
            'llm': self.llm.id if self.llm else None,
            'agentNumber': self.agent_number,
            'maxTokens': self.max_tokens,
            'temperature': self.temperature,
            'maxContextSnippets': self.max_context_snippets,
            'documentSimilarityThreshold': self.document_similarity_threshold,
            'usePreviousAgentFiles': self.use_previous_agent_files,
            'usePreviousAgentEmbeddings': self.use_previous_agent_embeddings,
            'textInput': self.text_input,
            'useStructuredOutputNode': self.use_structured_output_node,
            'enableWebSearch': self.enable_web_search,
        }

    def __str__(self):
        return f"Agent {self.agent_number}: {self.name or (self.prompt.title if self.prompt else 'No Prompt')}"


class TemplateAgentNodeData(models.Model):
    """Data model for 'templateAgent' type nodes - simplified, quick agent usage."""
    agent = models.ForeignKey(
        'agents.Agent',
        on_delete=models.PROTECT,
        help_text="Agent template to use"
    )
    name = models.CharField(
        max_length=255,
        blank=True,
        default='',
        help_text="Override agent name for this workflow node"
    )
    description = models.TextField(
        blank=True,
        default='',
        help_text="Override agent description for this workflow node"
    )
    agent_number = models.PositiveIntegerField(
        help_text="Agent order in workflow"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def to_dict(self):
        """Convert to React Flow node data format."""
        return {
            'agent': self.agent.id if self.agent else None,
            'name': self.name,
            'description': self.description,
            'agentNumber': self.agent_number,
        }

    def __str__(self):
        return f"Template Agent {self.agent_number}: {self.agent.name if self.agent else 'No Agent'}"
