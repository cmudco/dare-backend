import random
import string
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.utils import timezone

from common.managers import ActiveObjectsManager
from common.models import BaseModel, TimeStampMixin
from core.fields import EncryptedCharField
from .constants import (
    Provider,
    SenderType,
    FeedbackType,
    ConversationSource,
    ArtifactType,
    ArtifactStatus,
    ModelTier,
    ModelEffort,
    ToolCallOrigin,
)


class LLM(models.Model):
    name = models.CharField(max_length=255, help_text="Display name of the Language Model.")
    identifier = models.CharField(max_length=255, unique=True, help_text="Technical identifier used in API calls (e.g., claude-3.5-sonnet-20240307).")
    description = models.TextField(blank=True, null=True, help_text="Description of the language model capabilities.")
    provider = models.CharField(
        max_length=20,
        choices=Provider.choices(),
        default="openai",
        help_text="Provider of the LLM (e.g., OpenAI, Claude)."
    )
    base_url = models.URLField(
        blank=True,
        null=True,
        max_length=500,
        help_text="Custom base URL for the API endpoint (required for CUSTOM provider, e.g., https://litellm-dev.pace.gatech.edu:4000/v1)."
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this model is available for new selections."
    )
    is_reasoning = models.BooleanField(default=False, help_text="Whether the model supports reasoning.")
    supports_vision = models.BooleanField(
        default=True,
        help_text="Whether the model supports vision/image analysis (e.g., GPT-4V, Claude 3+, Gemini Pro Vision)."
    )
    supports_temperature = models.BooleanField(
        default=True,
        help_text="Whether this model accepts the temperature sampling parameter."
    )
    supports_effort = models.BooleanField(
        default=False,
        help_text="Whether this model accepts an effort control parameter."
    )
    supports_adaptive_thinking = models.BooleanField(
        default=False,
        help_text="Whether this model supports provider-native adaptive thinking."
    )
    default_effort = models.CharField(
        max_length=20,
        choices=ModelEffort.choices,
        default=ModelEffort.HIGH,
        help_text="Default effort level when the conversation has no explicit effort override."
    )
    default_adaptive_thinking_enabled = models.BooleanField(
        default=False,
        help_text="Whether adaptive thinking should be sent by default for this model."
    )
    is_image_generator = models.BooleanField(
        default=False,
        help_text="Whether the model is an image generation model (e.g., DALL-E)."
    )
    is_audio_transcriber = models.BooleanField(
        default=False,
        help_text="Whether the model supports audio transcription (e.g., Whisper, Gemini)."
    )
    tier = models.CharField(
        max_length=20,
        choices=ModelTier.choices,
        default=ModelTier.ADVANCED,
        help_text=(
            "Cost/capability tier for grouping models in the UI. "
            "Premium: Flagship models (e.g., Claude Opus, GPT-4.5). "
            "Advanced: Mid-range models (e.g., Claude Sonnet, GPT-4o, Gemini Pro). "
            "Flash: Fast, cost-optimized models (e.g., Claude Haiku, GPT-4o-mini, Gemini Flash)."
        ),
    )

    input_token_rate_per_million = models.DecimalField(
            max_digits=10,
            decimal_places=2,
            default=Decimal('0.00'),
            validators=[MinValueValidator(0)],
            help_text="Cost per million input tokens in USD (e.g., 3.00 for $3 per 1M tokens)."
        )
    output_token_rate_per_million = models.DecimalField(
            max_digits=10,
            decimal_places=2,
            default=Decimal('0.00'),
            validators=[MinValueValidator(0)],
            help_text="Cost per million output tokens in USD (e.g., 15.00 for $15 per 1M tokens)."
        )


    def __str__(self):
        return self.name

    @property
    def is_special_purpose(self) -> bool:
        """Check if this model is a special-purpose model (not a chat model).

        Special-purpose models like image generators and audio transcribers
        cannot be used for standard chat completions.
        """
        return self.is_image_generator or self.is_audio_transcriber

    @property
    def supports_chat(self) -> bool:
        """Check if this model supports chat completions."""
        return not self.is_special_purpose

    @classmethod
    def get_default_chat_model(cls) -> 'LLM':
        """Get a default chat-capable model.

        Returns the first available model that supports chat completions
        (i.e., not an image generator or audio transcriber).

        Returns:
            LLM instance or None if no chat model is available
        """
        return cls.objects.filter(
            is_image_generator=False,
            is_audio_transcriber=False
        ).first()

    class Meta:
        verbose_name_plural = "LLMs"


class ModelGroup(models.Model):
    """
    Model groups define which LLM models are available to different sets of users.
    Each user can belong to one model group.
    """
    name = models.CharField(max_length=255, help_text="Name of the model group (e.g., 'Basic', 'Premium', 'Enterprise').")
    description = models.TextField(blank=True, null=True, help_text="Description of the model group and its capabilities.")

        # Many-to-many relationship with LLM models
    allowed_models = models.ManyToManyField(
        LLM,
        related_name="model_groups",
        help_text="LLM models that users in this group can access."
    )

    # Whether this group is active
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this model group is currently active and can be assigned to users."
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Model Group"
        verbose_name_plural = "Model Groups"
        ordering = ['name']


class ProviderAPIKey(BaseModel):
    """
    Store API keys for LLM providers.
    Managed by admins only via Django admin.
    Each provider can have one active API key per DARE instance.

    Inherits from BaseModel:
    - is_active: Whether this API key should be used
    - is_deleted: Soft delete support
    - created_at, updated_at: Automatic timestamps
    """
    provider = models.CharField(
        max_length=20,
        choices=Provider.choices(),
        unique=True,
        help_text="LLM provider (e.g., OpenAI, Anthropic, Google, Meta)"
    )
    api_key = EncryptedCharField(
        max_length=500,
        help_text="API key for this provider (stored encrypted using AES-256)"
    )

    active_objects = ActiveObjectsManager()

    class Meta:
        verbose_name = "Provider API Key"
        verbose_name_plural = "Provider API Keys"
        ordering = ['provider']

    def __str__(self):
        return f"{self.get_provider_display()} API Key"

    def get_masked_key(self):
        """
        Return a masked version of the API key showing only the last 4 characters.
        Used for display in admin interface.
        """
        if self.api_key and len(self.api_key) > 4:
            return f"***{self.api_key[-4:]}"
        return "***"


class ModelCardData(TimeStampMixin):
    """
    Intelligence data for any LLM - powers the Model Cards feature.
    Separate from operational LLM model to allow us to track models not configured for use in chat/workflows/etc.
    """

    # Optional link to operational model
    llm = models.OneToOneField(
        'LLM',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='model_card_data',
        help_text="Link to LLM if this model is configured for use"
    )

    # Identity
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    name_variants = models.JSONField(default=list, blank=True)
    provider_name = models.CharField(max_length=255)

    # Public feedback (pilot JSON blob)
    public_feedback = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "Model Card Data"
        verbose_name_plural = "Model Card Data"

    def __str__(self):
        return self.name


class PublicFeedbackSourceCluster(TimeStampMixin):
    """
    A cluster of related sources about the same content.

    Example: An arXiv paper and its Hacker News discussion thread
    would be grouped into one cluster, since they represent the same
    underlying content/finding being discussed.

    The cluster_index is used for citations in the public_feedback JSON:
        "refs": [1, 3, 7] -> clusters with cluster_index 1, 3, 7
    """
    model_card = models.ForeignKey(
        'ModelCardData',  # String reference to avoid circular import
        on_delete=models.CASCADE,
        related_name='source_clusters'
    )

    # Citation index - this is what refs[] arrays point to
    cluster_index = models.PositiveIntegerField(
        help_text="Citation number [1], [2], etc. used in refs arrays"
    )

    # The "canonical" representation of this cluster
    canonical_title = models.CharField(
        max_length=500,
        help_text="Primary title for this source cluster"
    )
    canonical_url = models.URLField(
        max_length=2000,
        help_text="Primary URL (e.g., the original paper, not the HN thread)"
    )

    # Optional: identifier for deduplication (arXiv ID, DOI, etc.)
    identifier = models.CharField(
        max_length=100,
        blank=True,
        help_text="arXiv ID, DOI, or other unique identifier if available"
    )

    class Meta:
        unique_together = ['model_card', 'cluster_index']
        ordering = ['cluster_index']
        verbose_name = "Public Feedback Source Cluster"
        verbose_name_plural = "Public Feedback Source Clusters"

    def __str__(self):
        return f"[{self.cluster_index}] {self.canonical_title[:50]}"

    @property
    def source_count(self):
        return self.sources.count()


class PublicFeedbackSource(TimeStampMixin):
    """
    Individual source within a cluster.

    A cluster might contain:
    - The original arXiv paper (source_type='canonical')
    - A Hacker News discussion thread (source_type='hackernews')
    - An OpenReview comment thread (source_type='review')
    - A Reddit post about it (source_type='reddit')

    All of these discuss the same underlying content but may contain
    different perspectives and user reactions.
    """

    SOURCE_TYPE_CHOICES = [
        ('canonical', 'Canonical Source'),
        ('hackernews', 'Hacker News'),
        ('reddit', 'Reddit'),
        ('twitter', 'Twitter/X'),
        ('blog', 'Blog Post'),
        ('news', 'News Article'),
        ('review', 'Review/OpenReview'),
        ('forum', 'Forum Discussion'),
        ('other', 'Other'),
    ]

    cluster = models.ForeignKey(
        PublicFeedbackSourceCluster,
        on_delete=models.CASCADE,
        related_name='sources'
    )

    title = models.CharField(max_length=500)
    url = models.URLField(max_length=2000)

    source_type = models.CharField(
        max_length=20,
        choices=SOURCE_TYPE_CHOICES,
        default='other',
        help_text="Type of source for potential filtering"
    )

    page_date = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        help_text="Date string as returned by search (not normalized)"
    )

    snippet = models.TextField(
        blank=True,
        help_text="Text snippet from search results"
    )

    originating_query = models.CharField(
        max_length=200,
        blank=True,
        help_text="The search query that found this source"
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['source_type', 'title']
        verbose_name = "Public Feedback Source"
        verbose_name_plural = "Public Feedback Sources"

    def __str__(self):
        return f"{self.get_source_type_display()}: {self.title[:40]}"



class Conversation(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="conversations",
        null=True,
        blank=True,
        help_text="User who owns this conversation. Null for public bot conversations."
    )
    conversation_id = models.CharField(max_length=50, unique=True, help_text="Unique conversation ID.")
    title = models.CharField(max_length=255, blank=True, null=True, help_text="Title of the conversation.")
    source = models.CharField(
        max_length=20,
        choices=ConversationSource.choices,
        default=ConversationSource.DARE,
        help_text="Platform source where the conversation was created (DARE or SocraticBots)."
    )
    bot_id = models.IntegerField(
        null=True,
        blank=True,
        help_text="Associated Socratic Bot ID (only populated for SocraticBots source)."
    )
    access_code = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        db_index=True,
        help_text=(
            "Access code redeemed by the user when starting this conversation, "
            "denormalized at create time so the billing finalizer can resolve "
            "the matching AccessCodeGroup (and its GroupWallet) without a "
            "callback to SocraticBooks."
        ),
    )
    anonymous_session_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        db_index=True,
        help_text="Session ID for anonymous public bot conversations."
    )
    max_context_snippets = models.PositiveIntegerField(default=4, help_text="Maximum number of context snippets to retrieve.")
    document_similarity_threshold = models.FloatField(default=0.2, help_text="Similarity threshold for document retrieval.")
    temperature = models.FloatField(default=0.7, help_text="Temperature setting for the LLM.")
    effort = models.CharField(
        max_length=20,
        choices=ModelEffort.choices,
        null=True,
        blank=True,
        help_text="Optional effort override for models that support effort. Null uses the selected model default."
    )
    max_tokens = models.PositiveIntegerField(default=2048, help_text="Maximum tokens for LLM responses.")
    history_limit = models.PositiveIntegerField(default=20, help_text="Maximum number of messages to include in conversation history.")
    web_search_enabled = models.BooleanField(
        default=False,
        help_text="Enable real-time web search for up-to-date information."
    )
    web_fetch_enabled = models.BooleanField(
        default=False,
        help_text="Enable Claude web fetch for user-provided URLs and PDFs."
    )
    image_generation_enabled = models.BooleanField(
        default=False,
        help_text="Enable AI image generation for this conversation."
    )
    audio_transcription_enabled = models.BooleanField(
        default=False,
        help_text="Enable audio transcription for this conversation."
    )
    artifacts_enabled = models.BooleanField(
        default=False,
        help_text="Enable artifact generation for long-form content."
    )
    selected_model = models.ForeignKey(
        LLM,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="conversations_using_model",
        help_text="Selected LLM model for this conversation."
    )
    selected_media_ids = models.JSONField(
        default=list,
        blank=True,
        help_text="List of selected media file IDs for this conversation."
    )
    prompt = models.ForeignKey(
        'prompts.Prompt',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="conversations",
        help_text="Associated prompt for this conversation."
    )
    sort_order = models.PositiveIntegerField(
        default=0,
        help_text="Sort order for drag-and-drop functionality. Higher values appear later."
    )
    selected_embedding_ids = models.JSONField(
        default=list,
        blank=True,
        help_text="List of selected embedding file IDs for this conversation."
    )
    selected_file_ids = models.JSONField(
        default=list,
        blank=True,
        help_text="List of selected file IDs for this conversation."
    )
    learning_metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Learning-specific metadata including goals, tracking settings, and educational context."
    )
    # Auto-feedback tracking
    feedback_auto_prompt_count = models.IntegerField(
        default=0,
        help_text="Number of times auto-feedback prompt has been shown for this conversation"
    )
    feedback_last_prompt_message_count = models.IntegerField(
        default=0,
        help_text="Message count when feedback prompt was last shown"
    )
    feedback_last_prompt_timestamp = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when feedback prompt was last shown"
    )

    # Memory extraction tracking
    last_memory_extracted_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp of last memory extraction from this conversation."
    )

    # MCP Server integration
    selected_mcp_servers = models.ManyToManyField(
        'mcp.MCPServer',
        blank=True,
        related_name='conversations',
        help_text="MCP servers enabled for this conversation (tools become available to the LLM)."
    )

    # DARE Tools integration
    selected_dare_tools = models.ManyToManyField(
        'dare_tools.DareTool',
        blank=True,
        related_name='conversations',
        help_text="DARE tools enabled for this conversation."
    )

    # Agent template integration
    selected_agent = models.ForeignKey(
        'agents.Agent',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='conversations_using_agent',
        help_text="Selected agent template for this conversation."
    )

    is_favorite = models.BooleanField(
        default=False,
        help_text="Whether this conversation is marked as a favorite by its owner."
    )

    # Sharing / publishing
    is_published = models.BooleanField(
        default=False,
        help_text="Whether this conversation is published and visible to other users."
    )
    published_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when the conversation was published."
    )
    file_owner_id = models.IntegerField(
        null=True,
        blank=True,
        help_text="Original file owner's user ID for forked conversations. Used for vector search namespace."
    )

    active_objects = ActiveObjectsManager()


    def save(self, *args, **kwargs):
        if not self.conversation_id:
            # Generate random 5-character ID only if no custom ID provided
            self.conversation_id = "".join(
                random.choices(string.ascii_uppercase + string.digits, k=5)
            )
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Conversation {self.conversation_id}"

    def clone(self, include_messages=True, include_files=True, include_tags=True,
              include_snippets=True, custom_title=None, user=None, file_owner_id=None):
        """
        Clone this conversation with its messages and associated data.

        Args:
            include_messages (bool): Whether to clone messages
            include_files (bool): Whether to clone file associations
            include_tags (bool): Whether to clone tag associations
            include_snippets (bool): Whether to clone snippets
            custom_title (str): Custom title for cloned conversation
            user (User): Optional user to assign as owner (for forking)
            file_owner_id (int): Original file owner's user ID for cross-user forks.
                                 Used for vector search namespace access.

        Returns:
            Conversation: The cloned conversation instance
        """
        with transaction.atomic():
            # Determine cloned title
            if custom_title:
                cloned_title = custom_title
            elif self.title:
                cloned_title = f"COPY OF - {self.title}"
            else:
                cloned_title = "COPY OF - New Chat"

            # Create cloned conversation
            cloned_conversation = Conversation(
                user=user if user else self.user,
                title=cloned_title,
                source=self.source,
                max_context_snippets=self.max_context_snippets,
                document_similarity_threshold=self.document_similarity_threshold,
                temperature=self.temperature,
                effort=self.effort,
                max_tokens=self.max_tokens,
                history_limit=self.history_limit,
                web_search_enabled=self.web_search_enabled,
                web_fetch_enabled=self.web_fetch_enabled,
                image_generation_enabled=self.image_generation_enabled,
                artifacts_enabled=self.artifacts_enabled,
                selected_model=self.selected_model,
                selected_media_ids=self.selected_media_ids.copy() if self.selected_media_ids else [],
                prompt=self.prompt,
                sort_order=self.sort_order,
                selected_agent=self.selected_agent,
                # Copy file/embedding selections for forked conversations
                selected_file_ids=self.selected_file_ids.copy() if self.selected_file_ids else [],
                selected_embedding_ids=self.selected_embedding_ids.copy() if self.selected_embedding_ids else [],
                # Track original file owner for cross-user vector search
                file_owner_id=file_owner_id,
            )
            cloned_conversation.save()

            # Clone MCP server selections
            if self.selected_mcp_servers.exists():
                cloned_conversation.selected_mcp_servers.set(self.selected_mcp_servers.all())

            # Clone DARE tool selections
            if self.selected_dare_tools.exists():
                cloned_conversation.selected_dare_tools.set(self.selected_dare_tools.all())

            if include_messages:
                # Clone messages
                original_messages = Message.active_objects.filter(
                    conversation=self
                ).order_by('created_at')

                message_mapping = {}

                for original_message in original_messages:
                    cloned_message = Message(
                        conversation=cloned_conversation,
                        sender_type=original_message.sender_type,
                        sender=(
                            user.email
                            if user and original_message.sender_type == SenderType.PLAYER
                            else original_message.sender
                        ),
                        message=original_message.message,
                        llm=original_message.llm,
                        feedback_type=original_message.feedback_type,
                        feedback_text=original_message.feedback_text,
                        is_edited=original_message.is_edited,
                        is_regenerated=original_message.is_regenerated,
                        original_message=original_message.original_message,
                        # Don't copy usage metrics
                        input_tokens=None,
                        output_tokens=None,
                        cost=None
                    )
                    cloned_message.save()
                    message_mapping[original_message.id] = cloned_message

                    # Clone relationships
                    if include_files and original_message.files.exists():
                        cloned_message.files.set(original_message.files.all())

                    if include_tags and original_message.tags.exists():
                        cloned_message.tags.set(original_message.tags.all())

                # Clone snippets
                if include_snippets:
                    for original_message in original_messages:
                        cloned_message = message_mapping[original_message.id]
                        original_snippets = Snippet.active_objects.filter(
                            message=original_message
                        )

                        for original_snippet in original_snippets:
                            cloned_snippet = Snippet(
                                message=cloned_message,
                                file=original_snippet.file,
                                text=original_snippet.text,
                                similarity_score=original_snippet.similarity_score,
                                chunk_index=original_snippet.chunk_index
                            )
                            cloned_snippet.save()

            return cloned_conversation

    class Meta:
        indexes = [
            models.Index(fields=['user', 'source'], name='conv_user_source_idx'),
        ]

class Message(BaseModel):
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="messages",
        help_text="Corresponding conversation."
    )
    sender_type = models.IntegerField(
        choices=SenderType.choices,
        help_text="Type of sender (User or AI)."
    )
    sender = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="Name or identifier of the sender."
    )
    message = models.TextField(help_text="Content of the message.")
    llm = models.ForeignKey(
        'conversations.LLM',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="messages",
        help_text="The LLM used to generate this message (null for user messages or LiteLLM-routed dispatches)."
    )
    litellm_key = models.ForeignKey(
        'billing.LiteLLMKey',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="messages",
        help_text="LiteLLM key used to dispatch this message. Populated only when wallet=LITELLM; null otherwise."
    )
    litellm_model_name = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="Model identifier sent to the LiteLLM proxy (e.g. 'gpt-4o'). Populated only when llm is null and a LiteLLM key was used."
    )

    files = models.ManyToManyField(
        'files.File',
        blank=True,
        related_name='chat_messages',
        help_text="Files referenced in this message"
    )

    tags = models.ManyToManyField(
        'files.Tag',
        blank=True,
        related_name='chat_messages',
        help_text="Tags associated with this message"
    )

    input_tokens = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Number of input tokens used in the LLM request."
    )
    output_tokens = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Number of output tokens generated by the LLM."
    )
    cost = models.DecimalField(
        max_digits=10,
        decimal_places=6,
        null=True,
        blank=True,
        default=Decimal('0.000000'),
        validators=[MinValueValidator(0)],
        help_text="Cost of this message in USD based on token usage and LLM pricing."
    )

    # Energy/environmental impact tracking
    energy_wh = models.DecimalField(
        max_digits=10,
        decimal_places=6,
        null=True,
        blank=True,
        help_text="Estimated energy consumption in Watt-hours."
    )
    carbon_g = models.DecimalField(
        max_digits=10,
        decimal_places=6,
        null=True,
        blank=True,
        help_text="Estimated carbon emissions in grams CO2 equivalent."
    )
    water_ml = models.DecimalField(
        max_digits=10,
        decimal_places=6,
        null=True,
        blank=True,
        help_text="Estimated water usage in milliliters."
    )

    # Unified feedback system
    feedback_type = models.CharField(
        max_length=10,
        choices=FeedbackType.choices,
        null=True,
        blank=True,
        help_text="Type of feedback provided by the user (like/dislike)."
    )
    feedback_text = models.TextField(
        blank=True,
        null=True,
        help_text="Optional feedback text provided by the user."
    )
    feedback_source = models.CharField(
        max_length=10,
        choices=[
            ('thumbs', 'Thumbs Click'),
            ('manual', 'Manual Feedback Button'),
            ('auto', 'Automatic Prompt')
        ],
        null=True,
        blank=True,
        default='thumbs',
        help_text="Source that triggered the feedback submission"
    )
    is_edited = models.BooleanField(
        default=False,
        help_text="Whether this message has been edited by the user (applies to user messages only)."
    )
    is_regenerated = models.BooleanField(
        default=False,
        help_text="Whether this message has been regenerated (applies to AI messages only)."
    )
    original_message = models.TextField(
        blank=True,
        null=True,
        help_text="Original content of the message before editing or regeneration."
    )
    learning_progress_data = models.JSONField(
        default=dict,
        blank=True,
        help_text="Learning progress data associated with this message, such as assessment triggers and educational metadata."
    )
    memory_context_data = models.JSONField(
        default=list,
        blank=True,
        help_text="Memory items used as context for this message. List of {content, memory_type, categories}."
    )

    # Content type for specialized rendering (diagrams, charts, etc.)
    content_type = models.CharField(
        max_length=30,
        default="text",
        help_text="Type of content for rendering (text, mermaid_diagram, chart, image, audio)."
    )
    content_metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Metadata for content rendering (e.g., chart config, diagram type)."
    )

    active_objects = ActiveObjectsManager()

    @property
    def sender_name(self):
        """
        Returns the display name of the sender.
        If sender is provided, use that.
        Otherwise fall back to predefined labels based on sender_type.
        """
        if self.sender:
            return self.sender
        elif self.sender_type == SenderType.AI_ASSISTANT:
            return SenderType.AI_ASSISTANT.label
        else:
            return self.conversation.user.email

    @property
    def short_message(self):
        return self.message[:30] + "..." if len(self.message) > 30 else self.message

    def __str__(self):
        return f"{self.sender_name} ({self.short_message})"

    class Meta:
        indexes = [
            models.Index(fields=['conversation', 'created_at'], name='msg_conv_created_idx'),
            models.Index(fields=['conversation', 'sender_type'], name='msg_conv_sender_idx'),
        ]


class MessageToolCall(BaseModel):
    """
    Tracks tool calls within a message.

    Tool calls can originate from DARE tools, external MCP servers, or
    provider-native server tools. This enables:
    - Multi-turn tool use (feeding results back to LLM)
    - User confirmation for write operations
    - Audit trail of all tool executions
    - UI display of tool status and results
    """
    message = models.ForeignKey(
        Message,
        on_delete=models.CASCADE,
        related_name='mcp_tool_calls',
        help_text="The AI message that requested this tool call."
    )
    
    # From LLM response
    tool_call_id = models.CharField(
        max_length=100,
        help_text="Unique ID from LLM (e.g., 'call_abc123' or 'toolu_abc123')."
    )
    server_slug = models.CharField(
        max_length=100,
        help_text="Concrete tool server/provider slug (e.g., 'slack', 'dare', 'anthropic')."
    )
    origin = models.CharField(
        max_length=20,
        choices=ToolCallOrigin.choices,
        default=ToolCallOrigin.MCP,
        help_text="Execution origin: DARE internal, MCP external, or provider-native."
    )
    tool_name = models.CharField(
        max_length=200,
        help_text="Name of the tool (e.g., 'channels_list')."
    )
    arguments = models.JSONField(
        default=dict,
        help_text="Arguments passed to the tool."
    )
    
    # Execution state
    status = models.CharField(
        max_length=30,
        choices=[
            ('pending', 'Pending'),
            ('awaiting_confirmation', 'Awaiting Confirmation'),
            ('executing', 'Executing'),
            ('completed', 'Completed'),
            ('failed', 'Failed'),
            ('cancelled', 'Cancelled'),
        ],
        default='pending',
        help_text="Current status of the tool call."
    )
    requires_confirmation = models.BooleanField(
        default=False,
        help_text="Whether this tool requires user confirmation before execution."
    )
    
    # Result after execution
    result = models.TextField(
        null=True,
        blank=True,
        help_text="Result text from tool execution."
    )
    error = models.TextField(
        null=True,
        blank=True,
        help_text="Error message if execution failed."
    )
    executed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the tool was executed."
    )
    
    # Link to MCP execution audit
    mcp_execution = models.ForeignKey(
        'mcp.MCPToolExecution',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='message_tool_calls',
        help_text="Link to the MCP execution audit record."
    )
    
    class Meta:
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['message', 'status'], name='mtc_msg_status_idx'),
            models.Index(fields=['tool_call_id'], name='mtc_call_id_idx'),
        ]
    
    def __str__(self):
        return f"{self.server_slug}.{self.tool_name} ({self.status})"


class Snippet(BaseModel):
    """
    Model to track retrieved snippets from Pinecone vector search.
    """
    message = models.ForeignKey(
        Message,
        on_delete=models.CASCADE,
        related_name="snippets",
        help_text="The message this snippet was retrieved for."
    )
    file = models.ForeignKey(
        'files.File',
        on_delete=models.CASCADE,
        related_name="snippets",
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

    active_objects = ActiveObjectsManager()

    def __str__(self):
        return f"Snippet for Message {self.message.id} from File {self.file.id} (Score: {self.similarity_score})"


class WebSearchSource(BaseModel):
    """
    Model to store web search sources/citations from LLM responses.

    When web search is enabled, LLMs return citations linking their responses
    to source URLs. This model captures those sources for display in the UI,
    similar to how Snippet stores RAG-retrieved document chunks.

    Provider-specific fields:
    - OpenAI: url, title (from annotations)
    - Claude: url, title, cited_text, page_age (from web_search_tool_result)
    - Gemini: url, title (from grounding_metadata.grounding_chunks)
    """
    message = models.ForeignKey(
        Message,
        on_delete=models.CASCADE,
        related_name="web_search_sources",
        help_text="The message this source was cited in."
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
            models.Index(fields=['message'], name='websearch_message_idx'),
        ]

    def __str__(self):
        return f"WebSearchSource for Message {self.message.id}: {self.title or self.url[:50]}"


class LearningProgressAssessment(BaseModel):
    """
    Model to store AI-generated learning progress assessments for conversations.
    Tracks student understanding and learning progression over time.
    """
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="progress_assessments",
        help_text="The conversation this assessment belongs to."
    )
    last_message = models.ForeignKey(
        Message,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="triggered_assessments",
        help_text="The message that triggered this assessment (optional)."
    )
    content = models.TextField(
        help_text="AI-generated progress assessment content (Markdown formatted)."
    )
    learning_goals = models.TextField(
        blank=True,
        help_text="Learning goals that were used for this assessment."
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Additional metadata including assessment parameters, AI model used, etc."
    )

    active_objects = ActiveObjectsManager()

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Learning Progress Assessment"
        verbose_name_plural = "Learning Progress Assessments"

    def __str__(self):
        return f"Progress Assessment for {self.conversation.conversation_id} at {self.created_at.strftime('%Y-%m-%d %H:%M')}"


class ArtifactGroup(BaseModel):
    """
    Groups all versions of an artifact together.
    Enables version history, diffing, and rollback capabilities.
    """
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="artifact_groups",
        help_text="The conversation this artifact group belongs to."
    )
    base_title = models.CharField(
        max_length=500,
        help_text="Original title of the artifact (from first version)."
    )
    latest_version = models.OneToOneField(
        'Artifact',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='is_latest_for',
        help_text="The most recent version of this artifact."
    )

    active_objects = ActiveObjectsManager()

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Artifact Group"
        verbose_name_plural = "Artifact Groups"

    def __str__(self):
        return f"ArtifactGroup: {self.base_title}"


class Artifact(BaseModel):
    """
    Model for long-form generated content (documents, code, diagrams).
    Supports section-by-section generation with pause/resume capability.
    """
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="artifacts",
        help_text="The conversation this artifact belongs to."
    )
    message = models.ForeignKey(
        Message,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="artifacts",
        help_text="The AI message associated with this artifact."
    )
    artifact_group = models.ForeignKey(
        'ArtifactGroup',
        on_delete=models.CASCADE,
        related_name='versions',
        null=True,
        blank=True,
        help_text="The group containing all versions of this artifact."
    )
    parent_artifact = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='child_versions',
        help_text="The previous version this artifact was derived from."
    )

    artifact_type = models.CharField(
        max_length=20,
        choices=ArtifactType.choices,
        default=ArtifactType.DOCUMENT,
        help_text="Type of artifact (document, code, diagram)."
    )
    title = models.CharField(
        max_length=500,
        help_text="Title of the artifact."
    )
    language = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        help_text="Programming language for code artifacts."
    )
    outline = models.TextField(
        blank=True,
        default="",
        help_text="Structured outline of the artifact sections."
    )
    content = models.TextField(
        blank=True,
        default="",
        help_text="Generated content of the artifact."
    )

    estimated_sections = models.PositiveIntegerField(
        default=10,
        help_text="Estimated number of sections in the artifact."
    )
    current_section = models.PositiveIntegerField(
        default=0,
        help_text="Current section being generated (0 = not started)."
    )

    status = models.CharField(
        max_length=20,
        choices=ArtifactStatus.choices,
        default=ArtifactStatus.PLANNING,
        help_text="Current status of artifact generation."
    )

    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Additional metadata (LLM used, token counts, etc.)."
    )

    # Unified artifact system fields
    filename = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Filename with extension (e.g., 'chart.json', 'diagram.mmd') - determines renderer."
    )
    content_type = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="MIME-like content type (e.g., 'application/vnd.dare.chart+json', 'text/mermaid')."
    )
    source_tool = models.CharField(
        max_length=50,
        blank=True,
        default="",
        help_text="DARE tool that created this artifact (e.g., 'create_chart', 'create_diagram')."
    )

    # Version tracking for modifications
    version = models.PositiveIntegerField(
        default=1,
        help_text="Version number, incremented on each modification."
    )

    active_objects = ActiveObjectsManager()

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Artifact"
        verbose_name_plural = "Artifacts"
        indexes = [
            models.Index(fields=['conversation', 'status'], name='artifact_conv_status_idx'),
        ]

    def __str__(self):
        return f"{self.title} ({self.get_status_display()})"

    @property
    def progress(self) -> float:
        """Calculate generation progress as a percentage."""
        if self.estimated_sections == 0:
            return 0.0
        return min(1.0, self.current_section / self.estimated_sections)

    @property
    def sections_remaining(self) -> int:
        """Calculate remaining sections to generate."""
        return max(0, self.estimated_sections - self.current_section)

    @property
    def word_count(self) -> int:
        """Calculate word count of generated content."""
        return len(self.content.split()) if self.content else 0

    def increment_version(self):
        """Increment version number on modification."""
        self.version += 1
        self.save(update_fields=['version', 'updated_at'])

    def create_new_version(self) -> 'Artifact':
        """
        Create a new version based on this artifact.
        Copies current content and increments version number.
        The new artifact is linked as a child of this one.

        Returns:
            Artifact: The newly created version
        """
        new_artifact = Artifact(
            conversation=self.conversation,
            artifact_group=self.artifact_group,
            parent_artifact=self,
            artifact_type=self.artifact_type,
            title=self.title,
            language=self.language,
            outline=self.outline,
            content=self.content,
            estimated_sections=self.estimated_sections,
            current_section=self.current_section,
            status=ArtifactStatus.PLANNING,
            version=self.version + 1,
        )
        new_artifact.save()

        # Update group's latest_version
        if self.artifact_group:
            self.artifact_group.latest_version = new_artifact
            self.artifact_group.save(update_fields=['latest_version', 'updated_at'])

        return new_artifact


class ArtifactCheckpoint(BaseModel):
    """
    Model to store checkpoints for artifact generation.
    Enables pause/resume functionality for long-form content.
    """
    artifact = models.ForeignKey(
        Artifact,
        on_delete=models.CASCADE,
        related_name="checkpoints",
        help_text="The artifact this checkpoint belongs to."
    )
    content_snapshot = models.TextField(
        help_text="Content snapshot at this checkpoint."
    )
    current_section = models.PositiveIntegerField(
        help_text="Section number at this checkpoint."
    )
    iteration_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of generation iterations completed."
    )
    state_data = models.JSONField(
        default=dict,
        help_text="Serialized state data for resuming generation."
    )

    active_objects = ActiveObjectsManager()

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Artifact Checkpoint"
        verbose_name_plural = "Artifact Checkpoints"
        indexes = [
            models.Index(fields=['artifact', 'created_at'], name='checkpoint_artifact_idx'),
        ]

    def __str__(self):
        return f"Checkpoint for {self.artifact.title} at section {self.current_section}"


class Feedback(BaseModel):
    """
    Model to store general user feedback from the FAB feedback widget.
    This is for platform-wide feedback, not message-specific feedback.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="feedbacks",
        help_text="User who submitted this feedback."
    )
    emotion = models.CharField(
        max_length=20,
        help_text="User's emotional response (love, happy, neutral, confused, sad)."
    )
    category = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        help_text="Feedback category (bug, idea, ui, performance, docs, other)."
    )
    message = models.TextField(
        blank=True,
        help_text="Detailed feedback message from the user."
    )
    screenshot = models.TextField(
        blank=True,
        null=True,
        help_text="Base64 encoded screenshot (optional)."
    )
    page = models.CharField(
        max_length=500,
        help_text="Page URL where feedback was submitted."
    )
    browser_info = models.TextField(
        blank=True,
        help_text="User's browser information."
    )

    active_objects = ActiveObjectsManager()

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Feedback"
        verbose_name_plural = "Feedbacks"

    def __str__(self):
        return f"Feedback from {self.user.email}: {self.emotion} - {self.category or 'No category'}"


class ConversationSummary(BaseModel):
    """Stores a rolling summary for a single conversation."""

    conversation = models.OneToOneField(
        Conversation,
        on_delete=models.CASCADE,
        related_name="conversation_summary",
    )
    summary = models.TextField(
        help_text="LLM-generated summary for the conversation."
    )
    llm = models.ForeignKey(
        "LLM",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    input_tokens = models.IntegerField(default=0)
    output_tokens = models.IntegerField(default=0)
    summarized_message_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of completed AI assistant messages covered by this summary.",
    )

    active_objects = ActiveObjectsManager()

    class Meta:
        ordering = ["-updated_at"]
        verbose_name = "Conversation Summary"
        verbose_name_plural = "Conversation Summaries"

    def __str__(self):
        return f"Summary for {self.conversation.conversation_id}"
