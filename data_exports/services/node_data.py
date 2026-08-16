"""Serialize and rebuild the typed payload behind a workflow node.

A node reaches its data through a GenericForeignKey, so the content type
travels as its natural key — ``ContentType`` primary keys are per-installation
and mean nothing in the account that receives the archive.

Scalar columns are read generically rather than listed per type: there are six
node-data models and a listing would rot the first time one gains a field.
Relationships are the opposite — each one is a decision about what survives a
move between accounts, so they are named explicitly here.
"""

import logging
from typing import Any, Dict, Optional

from django.apps import apps
from django.contrib.contenttypes.models import ContentType

from conversations.models import LLM

logger = logging.getLogger(__name__)

# Named relations are resolved by hand below; everything else is dropped.
# Files, tags, agents and MCP servers do not survive a move between accounts:
# the rows they point at belong to the old account or to a catalog the new
# deployment may not have.
_PROMPT_RELATION = "prompt"
_LLM_RELATION = "llm"


def node_data_payload(data_object) -> Optional[Dict[str, Any]]:
    """Serialize a node-data row, or ``None`` when the node has no payload."""
    if data_object is None:
        return None

    model = type(data_object)
    payload = {
        "model": f"{model._meta.app_label}.{model._meta.model_name}",
        "fields": _scalar_fields(data_object),
    }
    if _has_field(model, _PROMPT_RELATION):
        payload["prompt_ref"] = getattr(data_object, "prompt_id")
    if _has_field(model, _LLM_RELATION):
        payload["model_identifier"] = (
            data_object.llm.identifier if data_object.llm_id else None
        )
    return payload


def create_node_data(payload: Optional[Dict[str, Any]], prompt_map: Dict[Any, Any]):
    """Rebuild a node-data row from its payload.

    Returns ``(instance, content_type)``; ``(None, None)`` when the payload is
    missing or names a model this deployment does not have.
    """
    if not isinstance(payload, dict):
        return None, None

    model = _resolve_model(payload.get("model"))
    if model is None:
        return None, None

    fields = payload.get("fields")
    values = {
        name: value
        for name, value in (fields or {}).items()
        if _has_concrete_field(model, name)
    }

    if _has_field(model, _PROMPT_RELATION):
        values["prompt"] = prompt_map.get(payload.get("prompt_ref"))
    if _has_field(model, _LLM_RELATION):
        values["llm"] = _resolve_llm(payload.get("model_identifier"))

    instance = model.objects.create(**values)
    return instance, ContentType.objects.get_for_model(model)


def _scalar_fields(data_object) -> Dict[str, Any]:
    return {
        field.name: getattr(data_object, field.name)
        for field in type(data_object)._meta.concrete_fields
        if not field.is_relation and not field.primary_key and not _is_auto(field)
    }


def _is_auto(field) -> bool:
    return getattr(field, "auto_now", False) or getattr(field, "auto_now_add", False)


def _has_field(model, name: str) -> bool:
    try:
        model._meta.get_field(name)
    except Exception:
        return False
    return True


def _has_concrete_field(model, name: str) -> bool:
    return any(
        field.name == name and not field.is_relation
        for field in model._meta.concrete_fields
    )


def _resolve_model(label: Optional[str]):
    if not isinstance(label, str) or "." not in label:
        return None
    app_label, model_name = label.split(".", 1)
    try:
        return apps.get_model(app_label, model_name)
    except LookupError:
        logger.warning("Archive names unknown node-data model %s", label)
        return None


def _resolve_llm(identifier: Optional[str]):
    if not identifier:
        return None
    return LLM.objects.filter(identifier=identifier).first()
