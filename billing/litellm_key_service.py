"""Lifecycle of a user's LiteLLM key where SocraticBooks bots depend on it.

A bot saved with a ``litellm:<key>:<model>`` id is sponsored through that key,
so deleting the key must clear it from those bots in the same step, as model
deletion does for DARE catalog models.
"""

import uuid
from dataclasses import dataclass

from django.db import transaction

from billing import litellm_models_service
from billing.litellm_model_policy import recommend_background_models
from billing.models import LiteLLMKey
from core.services.sb_client import (
    SocraticBooksClient,
    SocraticBooksRequestError,
    SocraticBotDependency,
)


class LiteLLMKeyDependencyError(RuntimeError):
    """SocraticBooks is configured but could not be checked or updated."""


def bot_dependents(key: LiteLLMKey) -> tuple[SocraticBotDependency, ...]:
    """Bots whose chat model routes through ``key``."""
    if not SocraticBooksClient.is_configured():
        return ()
    try:
        return SocraticBooksClient.get_litellm_key_dependencies(str(key.pk))
    except SocraticBooksRequestError as error:
        raise LiteLLMKeyDependencyError(str(error)) from error


def delete_key(key: LiteLLMKey) -> None:
    """Delete ``key`` and clear it from every bot that uses it.

    SocraticBooks cleanup runs last inside the transaction, so a failed
    cleanup rolls the deletion back instead of leaving bots pointing at a key
    that no longer exists.
    """
    key_id = str(key.pk)
    with transaction.atomic():
        key.delete()
        if SocraticBooksClient.is_configured():
            try:
                SocraticBooksClient.nullify_litellm_key_references(key_id)
            except SocraticBooksRequestError as error:
                raise LiteLLMKeyDependencyError(str(error)) from error


@dataclass(frozen=True)
class BackgroundModelOptions:
    """What a key's gateway serves, with DARE's ranked background picks first."""

    models: list[str]
    recommended: list[str]
    error: str


class BackgroundModelNotServed(ValueError):
    """The chosen background model is not on the key's gateway."""


def background_model_options(key: LiteLLMKey) -> BackgroundModelOptions:
    probe = litellm_models_service.list_models(key)
    names = sorted({model.name for model in probe.models}, key=str.casefold)
    return BackgroundModelOptions(
        models=names,
        recommended=recommend_background_models(names),
        error=probe.error or "",
    )


def group_key(group, key_id: str) -> LiteLLMKey:
    """A key issued to ``group``; raises ``LiteLLMKey.DoesNotExist`` otherwise."""
    try:
        pk = uuid.UUID(str(key_id))
    except ValueError as error:
        raise LiteLLMKey.DoesNotExist(key_id) from error
    return LiteLLMKey.objects.get(pk=pk, source_group=group)


def set_background_model(key: LiteLLMKey, model_name: str) -> LiteLLMKey:
    """Blank restores DARE's default; anything else must be served by the gateway."""
    if model_name and model_name not in background_model_options(key).models:
        raise BackgroundModelNotServed(model_name)
    key.background_model = model_name
    key.save(update_fields=["background_model", "updated_at"])
    return key
