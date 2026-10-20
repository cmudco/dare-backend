"""Saved model choices that may be a DARE model or a LiteLLM gateway model.

Projects and agents store the same picker id chats use: an LLM PK, or
``litellm:<key>:<model>`` for a model reached through one of the user's keys.
"""

import uuid
from dataclasses import dataclass
from typing import Optional

from django.utils.translation import gettext as _

from billing.models import LiteLLMKey
from conversations.models import LLM
from core.services.dtos.llm_descriptor_dto import (
    litellm_picker_id,
    split_litellm_picker_id,
)


class ModelRefUnavailable(ValueError):
    """The picker id names a model or key this user cannot use."""


@dataclass(frozen=True)
class ModelChoice:
    """Exactly one of `llm` or (`litellm_key`, `litellm_model_name`), or neither."""

    llm: Optional[LLM] = None
    litellm_key: Optional[LiteLLMKey] = None
    litellm_model_name: str = ""


def resolve_model_ref(user, ref: Optional[str]) -> ModelChoice:
    """Turn a picker id into a ModelChoice; an empty ref clears the choice."""
    if not ref:
        return ModelChoice()

    litellm = split_litellm_picker_id(ref)
    if litellm is not None:
        key_id, model_name = litellm
        try:
            key = LiteLLMKey.visible_for_user(user).filter(pk=uuid.UUID(key_id)).first()
        except ValueError:
            key = None
        if key is None:
            raise ModelRefUnavailable(_("That LiteLLM key is not available."))
        return ModelChoice(litellm_key=key, litellm_model_name=model_name)

    llm = (
        LLM.objects.filter(is_active=True, pk=int(ref)).first()
        if ref.isdigit()
        else None
    )
    if llm is None:
        raise ModelRefUnavailable(_("That model is not available."))
    return ModelChoice(llm=llm)


def model_ref(
    llm_id: Optional[int], litellm_key_id, litellm_model_name: str
) -> Optional[str]:
    """Picker id of a stored choice, in the same form chats use."""
    if litellm_key_id and litellm_model_name:
        return litellm_picker_id(litellm_key_id, litellm_model_name)
    if llm_id:
        return str(llm_id)
    return None
