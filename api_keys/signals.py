"""
Signal handlers for API Keys app.

Auto-creates UserProviderAPIKey records for all providers when a user is created,
similar to how Wallet is auto-created for users.
"""
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.conf import settings
from conversations.constants import Provider
from api_keys.models import UserProviderAPIKey


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_user_provider_api_keys(sender, instance, created, **kwargs):
    """
    Create UserProviderAPIKey records for all providers when a new user is created.

    This ensures every user has API key slots for all providers, just like
    every user gets a Wallet. Initially, these records will have null api_key values.

    Args:
        sender: User model class
        instance: User instance that was saved
        created: Boolean indicating if this is a new user
        **kwargs: Additional signal arguments
    """
    if created:
        # Create a UserProviderAPIKey for each provider
        for provider_choice in Provider:
            UserProviderAPIKey.active_objects.get_or_create(
                user=instance,
                provider=provider_choice.value,
                defaults={
                    'api_key': None,
                    'is_active': True
                }
            )


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def ensure_user_provider_api_keys_exist(sender, instance, created, **kwargs):
    """
    Ensure UserProviderAPIKey records exist for all providers for existing users.

    This handles the case where new providers are added to the system and existing
    users need to have UserProviderAPIKey records created for them.

    Args:
        sender: User model class
        instance: User instance that was saved
        created: Boolean indicating if this is a new user
        **kwargs: Additional signal arguments
    """
    if not created:
        # For existing users, ensure all providers have a record
        for provider_choice in Provider:
            UserProviderAPIKey.active_objects.get_or_create(
                user=instance,
                provider=provider_choice.value,
                defaults={
                    'api_key': None,
                    'is_active': True
                }
            )
