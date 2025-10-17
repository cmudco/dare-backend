"""
Constants for API Keys app
"""
from django.db import models


class BillingModeChoice(models.TextChoices):
    """
    Billing mode choices for users.

    WALLET: User pays from their wallet balance using admin's API keys
    OWN_API: User provides their own API keys and pays directly to the provider
    """
    WALLET = 'wallet', 'Use Wallet Credits'
    OWN_API = 'own_api', 'Use Own API Keys'
