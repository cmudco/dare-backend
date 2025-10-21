"""
Signal handlers for API Keys app.

Auto-creates UserProviderAPIKey records for all providers when a user is saved.
This ensures every user has API key slots for all providers, similar to how
Wallet is auto-created for users.
"""
from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from api_keys.models import UserProviderAPIKey
from conversations.constants import Provider


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def ensure_user_provider_api_keys(sender, instance, **kwargs):
    """
    Ensure UserProviderAPIKey records exist for all providers.

    This signal handler runs on every user save (both new and existing users).
    It uses get_or_create to ensure all provider API key records exist without
    duplication, handling both scenarios:
    - New users: Creates initial API key records for all providers
    - Existing users: Fills in missing records if new providers were added

    Args:
        sender: User model class
        instance: User instance that was saved
        **kwargs: Additional signal arguments (including 'created' flag)
    """
    for provider_choice in Provider:
        UserProviderAPIKey.active_objects.get_or_create(
            user=instance,
            provider=provider_choice.value,
            defaults={'api_key': None, 'is_active': True}
        )
