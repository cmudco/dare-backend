from decimal import Decimal
import random
import string

from django.db import models
from django.conf import settings
from django.core.validators import MinValueValidator
from common.managers import ActiveObjectsManager
from common.models import BaseModel, TimeStampMixin
from .constants import Provider, SenderType, FeedbackType, ConversationSource


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
    is_reasoning = models.BooleanField(default=False, help_text="Whether the model supports reasoning.")

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


class Conversation(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="conversations",
        help_text="User who owns this conversation."
    )
    conversation_id = models.CharField(max_length=50, unique=True, help_text="Unique conversation ID.")
    title = models.CharField(max_length=255, blank=True, null=True, help_text="Title of the conversation.")
    source = models.CharField(
        max_length=20,
        choices=ConversationSource.choices,
        default=ConversationSource.DARE,
        help_text="Platform source where the conversation was created (DARE or SocraticBots)."
    )
    max_context_snippets = models.PositiveIntegerField(default=4, help_text="Maximum number of context snippets to retrieve.")
    document_similarity_threshold = models.FloatField(default=0.2, help_text="Similarity threshold for document retrieval.")
    temperature = models.FloatField(default=0.7, help_text="Temperature setting for the LLM.")
    max_tokens = models.PositiveIntegerField(default=2048, help_text="Maximum tokens for LLM responses.")
    history_limit = models.PositiveIntegerField(default=20, help_text="Maximum number of messages to include in conversation history.")
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
              include_snippets=True, custom_title=None):
        """
        Clone this conversation with its messages and associated data.

        Args:
            include_messages (bool): Whether to clone messages
            include_files (bool): Whether to clone file associations
            include_tags (bool): Whether to clone tag associations
            include_snippets (bool): Whether to clone snippets
            custom_title (str): Custom title for cloned conversation

        Returns:
            Conversation: The cloned conversation instance
        """
        from django.db import transaction

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
                user=self.user,
                title=cloned_title,
                source=self.source,
                max_context_snippets=self.max_context_snippets,
                document_similarity_threshold=self.document_similarity_threshold,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                history_limit=self.history_limit,
                prompt=self.prompt,
                sort_order=self.sort_order
            )
            cloned_conversation.save()

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
                        sender=original_message.sender,
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
        help_text="The LLM used to generate this message (null for user messages)."
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