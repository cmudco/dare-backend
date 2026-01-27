"""
Models for ElevenLabs Voice Agent integration.
"""

from django.db import models
from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator

from common.models import BaseModel
from common.managers import ActiveObjectsManager
from voice.constants import VoiceAgentStatus, ConversationStatus, DEFAULT_TEMPERATURE, DEFAULT_MAX_DURATION


class VoiceAgent(BaseModel):
    """
    Model for ElevenLabs Conversational AI agents.
    Stores configuration and links to ElevenLabs agent_id.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='voice_agents',
        help_text="User who owns this agent"
    )
    name = models.CharField(
        max_length=255,
        help_text="Display name for the agent"
    )
    description = models.TextField(
        blank=True,
        default='',
        help_text="Description of the agent's purpose"
    )

    # ElevenLabs Configuration
    elevenlabs_agent_id = models.CharField(
        max_length=100,
        unique=True,
        null=True,
        blank=True,
        help_text="ElevenLabs agent ID (set after creation via API)"
    )
    voice_id = models.CharField(
        max_length=100,
        blank=True,
        default='',
        help_text="ElevenLabs voice ID to use"
    )

    # Agent Configuration
    system_prompt = models.TextField(
        help_text="System prompt / instructions for the agent"
    )
    first_message = models.TextField(
        blank=True,
        default='',
        help_text="Initial greeting message from the agent"
    )
    temperature = models.FloatField(
        default=DEFAULT_TEMPERATURE,
        validators=[MinValueValidator(0.0), MaxValueValidator(2.0)],
        help_text="LLM temperature setting"
    )
    max_duration_seconds = models.PositiveIntegerField(
        default=DEFAULT_MAX_DURATION,
        help_text="Maximum conversation duration in seconds"
    )

    # Full ElevenLabs conversation_config (for advanced settings)
    # Stores: turn, tts, conversation, agent settings
    conversation_config = models.JSONField(
        default=dict,
        blank=True,
        help_text="Full ElevenLabs conversation_config JSON"
    )

    # Exam Configuration
    exam_title = models.CharField(
        max_length=255,
        blank=True,
        default='',
        help_text="Title of the exam"
    )
    exam_instructions = models.TextField(
        blank=True,
        default='',
        help_text="Instructions shown to users before starting"
    )

    # Status
    status = models.CharField(
        max_length=20,
        choices=VoiceAgentStatus.choices,
        default=VoiceAgentStatus.DRAFT,
        help_text="Agent status (active = accessible to all users)"
    )

    objects = models.Manager()
    active_objects = ActiveObjectsManager()

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'status']),
            models.Index(fields=['elevenlabs_agent_id']),
            models.Index(fields=['status']),
        ]

    def __str__(self):
        return f"{self.name} ({self.user.email})"


class VoiceConversation(BaseModel):
    """
    Model for storing voice conversation sessions.
    Records each user's exam attempt with transcript.
    """
    agent = models.ForeignKey(
        VoiceAgent,
        on_delete=models.CASCADE,
        related_name='conversations'
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='voice_conversations'
    )

    # ElevenLabs Session
    elevenlabs_conversation_id = models.CharField(
        max_length=100,
        unique=True,
        help_text="ElevenLabs conversation ID"
    )

    # Session Metadata
    started_at = models.DateTimeField(
        auto_now_add=True,
        help_text="When the conversation started"
    )
    ended_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the conversation ended"
    )
    duration_seconds = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Total duration in seconds"
    )

    # Status
    status = models.CharField(
        max_length=20,
        choices=ConversationStatus.choices,
        default=ConversationStatus.IN_PROGRESS
    )

    # Transcript stored as JSON
    transcript = models.JSONField(
        default=list,
        blank=True,
        help_text="Full conversation transcript"
    )

    objects = models.Manager()
    active_objects = ActiveObjectsManager()

    class Meta:
        ordering = ['-started_at']
        indexes = [
            models.Index(fields=['agent', 'user']),
            models.Index(fields=['elevenlabs_conversation_id']),
            models.Index(fields=['user', 'status']),
        ]

    def __str__(self):
        return f"Conversation {self.elevenlabs_conversation_id} - {self.user.email}"
