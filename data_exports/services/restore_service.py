"""Rebuild an account from an archive.

Restore is additive: it creates rows and never edits or removes what is
already there, so running it twice yields two copies rather than a merge
conflict. That is the honest behavior for an archive whose intended
destination is a freshly created account.

Nothing is trusted from the file. Primary keys inside the bundle are join keys
only — every row is created fresh and every reference is resolved through a
map built during this restore, so an archive can never make one account point
at another account's rows.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from django.db import transaction

from conversations.constants import SenderType
from conversations.models import LLM, Conversation, Message
from data_exports.constants import (CONVERSATIONS_PATH, MAX_CONVERSATIONS,
                                    MAX_MESSAGES, MAX_PROMPTS, MAX_WORKFLOWS,
                                    MEMORIES_PATH, PROFILE_PATH, PROMPTS_PATH,
                                    SCHEMA, WORKFLOWS_PATH)
from data_exports.services.archive import ArchiveError, read_archive
from data_exports.services.node_data import create_node_data
from memory.services import portability
from prompts.models import Prompt
from workflows.models import Workflow, WorkflowEdge, WorkflowNode

logger = logging.getLogger(__name__)

MESSAGE_BATCH = 500


class RestoreError(Exception):
    """A readable refusal to restore an archive."""


@dataclass
class RestoreReport:
    """What the restore created, and what it deliberately did not."""

    prompts: int = 0
    conversations: int = 0
    messages: int = 0
    workflows: int = 0
    memories: int = 0
    skipped: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "prompts": self.prompts,
            "conversations": self.conversations,
            "messages": self.messages,
            "workflows": self.workflows,
            "memories": self.memories,
            "skipped": self.skipped,
        }


def restore_archive(user, raw: bytes) -> RestoreReport:
    """Read an archive and rebuild its contents under ``user``."""
    try:
        documents = read_archive(raw)
    except ArchiveError as error:
        raise RestoreError(str(error)) from error

    _validate_manifest(documents)
    report = RestoreReport()

    prompts = _rows(documents, PROMPTS_PATH, MAX_PROMPTS, "prompts")
    conversations = _rows(
        documents, CONVERSATIONS_PATH, MAX_CONVERSATIONS, "conversations"
    )
    workflows = _rows(documents, WORKFLOWS_PATH, MAX_WORKFLOWS, "workflows")

    total_messages = sum(
        len(row.get("messages") or []) for row in conversations if isinstance(row, dict)
    )
    if total_messages > MAX_MESSAGES:
        raise RestoreError(
            f"The archive holds {total_messages} messages, over the "
            f"{MAX_MESSAGES} limit."
        )

    with transaction.atomic():
        prompt_map = _restore_prompts(user, prompts, report)
        _restore_conversations(user, conversations, prompt_map, report)
        _restore_workflows(user, workflows, prompt_map, report)
        _restore_profile(user, documents.get(PROFILE_PATH), prompt_map)

    # Memory owns its own transaction and its own emptiness rule, so it runs
    # last and outside: a store that already has rows should cost the person
    # their conversations, not the whole restore.
    _restore_memories(user, documents.get(MEMORIES_PATH), report)

    logger.info("Restored archive for user %s: %s", user.id, report.as_dict())
    return report


def _validate_manifest(documents: Dict[str, Any]) -> None:
    manifest = documents.get("manifest.json")
    if not isinstance(manifest, dict):
        raise RestoreError("The archive has no manifest — it is not a DARE export.")
    if manifest.get("schema") != SCHEMA:
        raise RestoreError(
            f"Unrecognized archive schema {manifest.get('schema')!r} — this "
            f"build restores {SCHEMA}."
        )


def _rows(documents: Dict[str, Any], path: str, limit: int, label: str) -> List[Dict]:
    rows = documents.get(path)
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise RestoreError(f"{path} inside the archive is not a list.")
    if len(rows) > limit:
        raise RestoreError(
            f"The archive holds {len(rows)} {label}, over the {limit} limit."
        )
    return [row for row in rows if isinstance(row, dict)]


def _restore_prompts(
    user, rows: List[Dict], report: RestoreReport
) -> Dict[Any, Prompt]:
    prompt_map: Dict[Any, Prompt] = {}
    for row in rows:
        prompt = Prompt.active_objects.create(
            user=user,
            title=_text(row.get("title"), 255),
            content=_text(row.get("content")),
            version=_positive_int(row.get("version"), default=1),
        )
        prompt_map[row.get("ref")] = prompt
    report.prompts = len(prompt_map)

    # Lineage second, once every prompt exists.
    for row in rows:
        parent = prompt_map.get(row.get("parent_ref"))
        prompt = prompt_map.get(row.get("ref"))
        if parent is not None and prompt is not None and parent.pk != prompt.pk:
            Prompt.active_objects.filter(pk=prompt.pk).update(parent=parent)
    return prompt_map


def _restore_conversations(
    user, rows: List[Dict], prompt_map: Dict[Any, Prompt], report: RestoreReport
) -> None:
    llm_cache: Dict[str, Optional[LLM]] = {}

    for row in rows:
        conversation = Conversation(
            user=user,
            prompt=prompt_map.get(row.get("prompt_ref")),
            selected_model=_llm(row.get("model_identifier"), llm_cache),
            title=_text(row.get("title"), 255),
        )
        _apply_conversation_settings(conversation, row)
        # conversation_id is globally unique and generated in save(); leaving it
        # empty is what makes the restored row a new conversation rather than a
        # collision with the archived one.
        conversation.conversation_id = ""
        conversation.save()
        _write_created_at(Conversation, conversation.pk, row.get("created_at"))
        report.conversations += 1
        report.messages += _restore_messages(
            conversation, row.get("messages") or [], user, llm_cache
        )


def _apply_conversation_settings(conversation: Conversation, row: Dict) -> None:
    """Copy the settings that carry a value, leaving the rest at model defaults.

    File and library selections are deliberately not carried: they are arrays
    of primary keys into rows this account does not own.
    """
    for name in (
        "source",
        "rag_mode",
        "effort",
        "max_context_snippets",
        "document_similarity_threshold",
        "temperature",
        "max_tokens",
        "history_limit",
        "web_search_enabled",
        "web_fetch_enabled",
        "image_generation_enabled",
        "audio_transcription_enabled",
        "artifacts_enabled",
        "memory_enabled",
        "sort_order",
        "learning_metadata",
    ):
        if name in row and row[name] is not None:
            setattr(conversation, name, row[name])


def _restore_messages(
    conversation: Conversation,
    rows: List[Any],
    user,
    llm_cache: Dict[str, Optional[LLM]],
) -> int:
    pending: List[Message] = []
    timestamps: List[Any] = []

    for row in rows:
        if not isinstance(row, dict):
            continue
        sender_type = (
            row.get("sender_type")
            if row.get("sender_type") in SenderType.values
            else SenderType.AI_ASSISTANT
        )
        message = Message(
            conversation=conversation,
            llm=_llm(row.get("model_identifier"), llm_cache),
            sender_type=sender_type,
            # The archived sender is the old account's address; the transcript
            # belongs to whoever is restoring it now.
            sender=(
                user.email
                if sender_type == SenderType.PLAYER
                else _text(row.get("sender"), 255)
            ),
            message=_text(row.get("message")),
            input_tokens=_positive_int(row.get("input_tokens")),
            output_tokens=_positive_int(row.get("output_tokens")),
            is_edited=bool(row.get("is_edited")),
            is_regenerated=bool(row.get("is_regenerated")),
            original_message=_text(row.get("original_message")),
            content_type=_text(row.get("content_type"), 30) or "text",
            content_metadata=row.get("content_metadata") or {},
        )
        pending.append(message)
        timestamps.append(row.get("created_at"))

    if not pending:
        return 0

    # bulk_create skips post_save, which otherwise enqueues one conversation
    # summary job per restored assistant message.
    created = Message._base_manager.bulk_create(pending, batch_size=MESSAGE_BATCH)

    # created_at is auto_now_add, so every row landed at "now" and the
    # transcript lost its order. Put the archived times back.
    restored: List[Message] = []
    for message, stamp in zip(created, timestamps):
        if stamp:
            message.created_at = stamp
            restored.append(message)
    if restored:
        Message._base_manager.bulk_update(
            restored, ["created_at"], batch_size=MESSAGE_BATCH
        )

    return len(created)


def _restore_workflows(
    user, rows: List[Dict], prompt_map: Dict[Any, Prompt], report: RestoreReport
) -> None:
    for row in rows:
        workflow = Workflow.objects.create(
            user=user,
            version=_positive_int(row.get("version"), default=1),
            viewport_x=_number(row.get("viewport_x")),
            viewport_y=_number(row.get("viewport_y")),
            viewport_zoom=_number(row.get("viewport_zoom"), default=1.0),
            manual_mode_enabled=bool(row.get("manual_mode_enabled")),
            display_order=_positive_int(row.get("display_order")),
            output_display_mode=_one_of(
                row.get("output_display_mode"), ("panel", "nodes"), "panel"
            ),
        )
        _write_created_at(Workflow, workflow.pk, row.get("created_at"))
        _restore_workflow_graph(workflow, row, prompt_map)
        report.workflows += 1


def _restore_workflow_graph(
    workflow: Workflow, row: Dict, prompt_map: Dict[Any, Prompt]
) -> None:
    for node_row in row.get("nodes") or []:
        if not isinstance(node_row, dict):
            continue
        data_object, content_type = create_node_data(node_row.get("data"), prompt_map)
        if data_object is None:
            continue
        WorkflowNode.objects.create(
            workflow=workflow,
            data_content_type=content_type,
            data_object_id=data_object.pk,
            **_node_columns(node_row),
        )

    for edge_row in row.get("edges") or []:
        if not isinstance(edge_row, dict):
            continue
        WorkflowEdge.objects.create(workflow=workflow, **_edge_columns(edge_row))

    # The root is recomputed rather than copied: it is a foreign key into the
    # nodes that were just created, and the helper finds it structurally.
    workflow.resolve_root_start_node()


def _node_columns(row: Dict) -> Dict[str, Any]:
    """Node identity and geometry. node_id is copied verbatim — edges address
    nodes by that string, so regenerating it would sever every connection."""
    return {
        "node_id": _text(row.get("node_id"), 255),
        "node_type": _text(row.get("node_type"), 100),
        "position_x": _number(row.get("position_x")),
        "position_y": _number(row.get("position_y")),
        "width": _optional_number(row.get("width")),
        "height": _optional_number(row.get("height")),
        "draggable": _flag(row.get("draggable"), True),
        "selectable": _flag(row.get("selectable"), True),
        "connectable": _flag(row.get("connectable"), True),
        "deletable": _flag(row.get("deletable"), True),
        "hidden": bool(row.get("hidden")),
        "source_position": _text(row.get("source_position"), 20),
        "target_position": _text(row.get("target_position"), 20),
        "parent_id": _text(row.get("parent_id"), 255) or None,
        "z_index": _int(row.get("z_index")),
        "drag_handle": _text(row.get("drag_handle"), 255),
        "style": row.get("style") or {},
        "class_name": _text(row.get("class_name"), 500),
    }


def _edge_columns(row: Dict) -> Dict[str, Any]:
    return {
        "edge_id": _text(row.get("edge_id"), 255),
        "edge_type": _text(row.get("edge_type"), 100) or "default",
        "source": _text(row.get("source"), 255),
        "target": _text(row.get("target"), 255),
        "source_handle": _text(row.get("source_handle"), 255) or None,
        "target_handle": _text(row.get("target_handle"), 255) or None,
        "data": row.get("data") or {},
        "animated": bool(row.get("animated")),
        "hidden": bool(row.get("hidden")),
        "deletable": _flag(row.get("deletable"), True),
        "selectable": _flag(row.get("selectable"), True),
    }


def _restore_profile(user, profile: Any, prompt_map: Dict[Any, Prompt]) -> None:
    if not isinstance(profile, dict):
        return

    changed = []
    for name, length in (
        ("country", 100),
        ("role", None),
        ("industry", None),
        ("purpose", None),
        ("referral_source", None),
        ("avatar_type", 20),
        ("avatar_preset", 50),
    ):
        value = profile.get(name)
        if value:
            setattr(user, name, _text(value, length))
            changed.append(name)

    for name in ("chunk_size", "overlap_size"):
        value = profile.get(name)
        if isinstance(value, int) and value > 0:
            setattr(user, name, value)
            changed.append(name)

    default_prompt = prompt_map.get(profile.get("default_prompt_ref"))
    if default_prompt is not None:
        user.default_prompt = default_prompt
        changed.append("default_prompt")

    if changed:
        user.save(update_fields=changed)


def _restore_memories(user, bundle: Any, report: RestoreReport) -> None:
    if not isinstance(bundle, dict) or not bundle.get("records"):
        return
    try:
        result = portability.import_bundle(user, bundle)
    except portability.ImportError_ as error:
        report.skipped.append(f"Memories were not restored: {error}")
        return
    report.memories = result.get("records", 0)


def _write_created_at(model, pk, value) -> None:
    """Put the archived creation time back.

    created_at is auto_now_add, so the insert stamped it with "now"; an UPDATE
    is the only way to keep an account's history in order.
    """
    if value:
        model._base_manager.filter(pk=pk).update(created_at=value)


def _llm(identifier: Any, cache: Dict[str, Optional[LLM]]) -> Optional[LLM]:
    """Resolve a catalog model by natural key, tolerating a retired model."""
    if not isinstance(identifier, str) or not identifier:
        return None
    if identifier not in cache:
        cache[identifier] = LLM.objects.filter(identifier=identifier).first()
    return cache[identifier]


def _text(value: Any, length: Optional[int] = None) -> str:
    text = value if isinstance(value, str) else ("" if value is None else str(value))
    return text[:length] if length else text


def _int(value: Any, default: int = 0) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _positive_int(value: Any, default: int = 0) -> int:
    number = _int(value, default)
    return number if number >= 0 else default


def _number(value: Any, default: float = 0.0) -> float:
    return (
        float(value)
        if isinstance(value, (int, float)) and not isinstance(value, bool)
        else default
    )


def _optional_number(value: Any) -> Optional[float]:
    return (
        float(value)
        if isinstance(value, (int, float)) and not isinstance(value, bool)
        else None
    )


def _flag(value: Any, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _one_of(value: Any, allowed: tuple, default: str) -> str:
    return value if value in allowed else default
