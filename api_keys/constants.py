"""
Constants for API Keys app
"""
from django.db import models


class BillingModeChoice(models.TextChoices):
    """
    Billing mode choices for users / for individual transactions.

    WALLET:  User pays from their wallet balance using admin's API keys.
    OWN_API: User provides their own API keys (BYO) and pays directly to the provider.
    LITELLM: Request was routed through a LiteLLM proxy key (user-self-served or
             admin-issued cohort/individual). External billing — no DARE wallet debit.
    """
    WALLET = 'wallet', 'Use Wallet Credits'
    OWN_API = 'own_api', 'Use Own API Keys'
    LITELLM = 'litellm', 'Use LiteLLM Proxy Key'
