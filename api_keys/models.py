from django.db import models
from django.conf import settings
from common.models import BaseModel
from common.managers import ActiveObjectsManager
from core.fields import EncryptedCharField
from conversations.constants import Provider


class UserProviderAPIKey(BaseModel):
    """
    Store user-provided API keys for LLM providers.
    Each user can have their own API keys for each provider.

    Similar to Wallet model - automatically created for each user.

    Inherits from BaseModel:
    - is_active: Whether this API key should be used
    - is_deleted: Soft delete support
    - created_at, updated_at: Automatic timestamps
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='provider_api_keys',
        help_text="User who owns this API key"
    )
    provider = models.CharField(
        max_length=20,
        choices=Provider.choices(),
        help_text="LLM provider (e.g., OpenAI, Anthropic, Google, Meta)"
    )
    api_key = EncryptedCharField(
        max_length=500,
        blank=True,
        null=True,
        help_text="API key for this provider (stored encrypted using AES-256)"
    )

    active_objects = ActiveObjectsManager()

    class Meta:
        verbose_name = "User Provider API Key"
        verbose_name_plural = "User Provider API Keys"
        unique_together = ['user', 'provider']
        ordering = ['user', 'provider']
        indexes = [
            models.Index(fields=['user', 'provider']),
        ]

    def __str__(self):
        status = "Set" if self.api_key else "Not Set"
        return f"{self.user.email} - {self.get_provider_display()} ({status})"

    def get_masked_key(self):
        """
        Return a masked version of the API key for display purposes.
        Shows first 7 and last 4 characters with asterisks in between.

        Example: sk-proj-***********xyz123
        """
        if not self.api_key:
            return None

        key = str(self.api_key)
        if len(key) <= 11:
            # If key is too short, just mask most of it
            return f"{key[:3]}{'*' * (len(key) - 6)}{key[-3:]}" if len(key) > 6 else '*' * len(key)

        # Standard masking: show first 7, mask middle, show last 4
        return f"{key[:7]}{'*' * (len(key) - 11)}{key[-4:]}"

    @property
    def has_key(self):
        """Check if this provider has an API key set"""
        return bool(self.api_key)
