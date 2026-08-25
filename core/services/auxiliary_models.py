"""Which model handles the work a turn needs but the user never asked for.

Naming a conversation and writing memory are side jobs. DARE picks a cheap
model for each from settings, which is right when DARE holds the keys — but a
user routing through a LiteLLM proxy is on a different roster. DARE's
``gemini-3.1-flash-lite`` is served as ``gemini/gemini-3.1-flash-lite`` there,
so the configured name is rejected and conversations end up called "New Chat".

A LiteLLM key can therefore name the model on its own roster that should do
each job. This module owns that lookup and nothing else; callers fall back to
their existing behaviour when it returns None.
"""

import logging
from typing import Optional

from billing.constants import UserWalletPreferenceTypeChoice
from billing.models import LiteLLMKey
from billing.wallet_router import resolve_active_wallet
from core.services.dtos import LLMDescriptor

logger = logging.getLogger(__name__)

TITLE = "title_model"
MEMORY = "memory_model"


def auxiliary_descriptor(user, purpose: str) -> Optional[LLMDescriptor]:
    """The proxy model chosen for this job, or None to use DARE's default.

    ``purpose`` is the field name on the key — ``TITLE`` or ``MEMORY``.
    """
    if user is None:
        return None

    wallet = resolve_active_wallet(user)
    if wallet.type != UserWalletPreferenceTypeChoice.LITELLM:
        return None

    key = LiteLLMKey.objects.filter(pk=wallet.ref_id).first()
    model_name = (getattr(key, purpose, "") or "").strip() if key else ""
    if not model_name:
        return None

    return LLMDescriptor.from_litellm(
        litellm_key=key,
        model_name=model_name,
        provider="custom",
    )
