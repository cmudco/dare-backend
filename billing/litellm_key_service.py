"""Lifecycle of a user's LiteLLM key where SocraticBooks bots depend on it.

A bot saved with a ``litellm:<key>:<model>`` id is sponsored through that key,
so deleting the key must clear it from those bots in the same step, as model
deletion does for DARE catalog models.
"""

from django.db import transaction

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
