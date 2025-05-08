from django.db.models.signals import post_save
from django.dispatch import receiver
from django.conf import settings
from decimal import Decimal
from users.models import User
from billing.models import Wallet, Transaction
from billing.constants import TransactionTypeChoice


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_user_wallet(sender, instance, created, **kwargs):
    """
    Create a wallet when a new user is registered.
    """
    if created:
            wallet = Wallet.objects.create(user=instance)