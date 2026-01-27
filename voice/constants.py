"""
Voice app constants for ElevenLabs integration.
"""

from django.db import models

APP_NAME = "voice"


class VoiceAgentStatus(models.TextChoices):
    """Status choices for VoiceAgent."""
    DRAFT = 'draft', 'Draft'
    ACTIVE = 'active', 'Active'


class ConversationStatus(models.TextChoices):
    """Status choices for VoiceConversation."""
    IN_PROGRESS = 'in_progress', 'In Progress'
    COMPLETED = 'completed', 'Completed'
    FAILED = 'failed', 'Failed'
    CANCELLED = 'cancelled', 'Cancelled'


# ElevenLabs API endpoints
ELEVENLABS_API_BASE = "https://api.elevenlabs.io/v1"
ELEVENLABS_AGENTS_ENDPOINT = f"{ELEVENLABS_API_BASE}/convai/agents"
ELEVENLABS_SIGNED_URL_ENDPOINT = f"{ELEVENLABS_API_BASE}/convai/conversation/get_signed_url"
ELEVENLABS_CONVERSATION_ENDPOINT = f"{ELEVENLABS_API_BASE}/convai/conversations"
ELEVENLABS_VOICES_ENDPOINT = f"{ELEVENLABS_API_BASE}/voices"

# Default agent settings
DEFAULT_TEMPERATURE = 0.7
DEFAULT_MAX_DURATION = 1800  # 30 minutes in seconds
