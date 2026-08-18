"""Build a restorable archive of one account.

Every row is written with its primary key as ``ref`` — a join key that is
local to the bundle and meaningless outside it. Restore rebuilds the graph
from those refs rather than from database ids, which is what lets an archive
land in an account that did not exist when it was written.

References to shared catalog rows (an LLM) travel as their natural key, so a
restore re-points them at whatever the destination deployment actually has.
"""

import logging
from typing import Any, Dict, List, Optional

from django.db.models import Prefetch
from django.utils import timezone

from conversations.models import Conversation, Message
from data_exports.constants import (CONVERSATIONS_PATH, EXCLUSIONS,
                                    MANIFEST_PATH, MEMORIES_PATH, PROFILE_PATH,
                                    PROMPTS_PATH, SCHEMA, WORKFLOWS_PATH,
                                    ExportScope)
from data_exports.services.archive import write_archive
from data_exports.services.node_data import node_data_payload
from memory.services import portability
from prompts.models import Prompt
from workflows.models import Workflow, WorkflowEdge, WorkflowNode

logger = logging.getLogger(__name__)

# Conversation columns that carry a value rather than a relationship. Anything
# naming a file, model, prompt or MCP server is resolved explicitly below.
_CONVERSATION_SETTINGS = (
    "title",
    "source",
    "max_context_snippets",
    "document_similarity_threshold",
    "rag_mode",
    "temperature",
    "effort",
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
)

_MESSAGE_FIELDS = (
    "sender_type",
    "sender",
    "message",
    "input_tokens",
    "output_tokens",
    "is_edited",
    "is_regenerated",
    "original_message",
    "content_type",
    "content_metadata",
)

_PROFILE_FIELDS = (
    "country",
    "chunk_size",
    "overlap_size",
    "role",
    "industry",
    "purpose",
    "referral_source",
    "avatar_type",
    "avatar_preset",
)

# A workflow's title and description are properties reading through its start
# node, not columns — they travel inside that node's data payload.
_WORKFLOW_FIELDS = (
    "version",
    "viewport_x",
    "viewport_y",
    "viewport_zoom",
    "manual_mode_enabled",
    "output_display_mode",
    "display_order",
)

_NODE_FIELDS = (
    "node_id",
    "node_type",
    "position_x",
    "position_y",
    "width",
    "height",
    "draggable",
    "selectable",
    "connectable",
    "deletable",
    "hidden",
    "source_position",
    "target_position",
    "parent_id",
    "z_index",
    "drag_handle",
    "style",
    "class_name",
)

_EDGE_FIELDS = (
    "edge_id",
    "edge_type",
    "source",
    "target",
    "source_handle",
    "target_handle",
    "data",
    "animated",
    "hidden",
    "deletable",
    "selectable",
)


def build_archive(user, scope: str) -> bytes:
    """Serialize the account at ``scope`` into archive bytes."""
    documents = build_documents(user, scope)
    return write_archive(documents)


def build_documents(user, scope: str) -> Dict[str, Any]:
    """Serialize the account into ``{archive path: document}``."""
    memories = _memories(user)

    if scope == ExportScope.MEMORIES:
        counts = {"memories": len(memories["records"])}
        return {
            MANIFEST_PATH: _manifest(user, scope, counts),
            MEMORIES_PATH: memories,
        }

    prompts = _prompts(user)
    conversations = _conversations(user)
    workflows = _workflows(user)
    counts = {
        "memories": len(memories["records"]),
        "prompts": len(prompts),
        "conversations": len(conversations),
        "messages": sum(len(row["messages"]) for row in conversations),
        "workflows": len(workflows),
    }
    return {
        MANIFEST_PATH: _manifest(user, scope, counts),
        PROFILE_PATH: _profile(user),
        MEMORIES_PATH: memories,
        PROMPTS_PATH: prompts,
        CONVERSATIONS_PATH: conversations,
        WORKFLOWS_PATH: workflows,
    }


def archive_filename(user, scope: str) -> str:
    stamp = timezone.now().strftime("%Y-%m-%d")
    suffix = "memories" if scope == ExportScope.MEMORIES else "account"
    return f"dare-{suffix}-export-{stamp}.zip"


def _manifest(user, scope: str, counts: Dict[str, int]) -> Dict[str, Any]:
    return {
        "schema": SCHEMA,
        "scope": str(scope),
        "exported_at": timezone.now().isoformat(),
        "source_user_id": user.id,
        "counts": counts,
        "excludes": list(EXCLUSIONS),
    }


def _memories(user) -> Dict[str, Any]:
    """The memory page's own bundle, embedded verbatim.

    Reusing it keeps one owner for the memory format: a memories-only archive
    and a Memory-page export are the same document.
    """
    return portability.export_bundle(user)


def _profile(user) -> Dict[str, Any]:
    profile = {field: getattr(user, field) for field in _PROFILE_FIELDS}
    profile["default_prompt_ref"] = user.default_prompt_id
    return profile


def _prompts(user) -> List[Dict[str, Any]]:
    prompts = Prompt.active_objects.filter(user=user).order_by("created_at")
    return [
        {
            "ref": prompt.id,
            "title": prompt.title,
            "content": prompt.content,
            "version": prompt.version,
            "parent_ref": prompt.parent_id,
            "created_at": prompt.created_at,
        }
        for prompt in prompts
    ]


def _conversations(user) -> List[Dict[str, Any]]:
    messages = Message.active_objects.select_related("llm").order_by("created_at")
    conversations = (
        Conversation.active_objects.filter(user=user)
        .select_related("selected_model")
        .prefetch_related(Prefetch("messages", queryset=messages))
        .order_by("created_at")
    )
    return [_conversation_payload(conversation) for conversation in conversations]


def _conversation_payload(conversation: Conversation) -> Dict[str, Any]:
    payload = {
        "ref": conversation.id,
        "created_at": conversation.created_at,
        "model_identifier": _identifier(conversation.selected_model),
        "prompt_ref": conversation.prompt_id,
        "messages": [
            _message_payload(message) for message in conversation.messages.all()
        ],
    }
    payload.update(
        {field: getattr(conversation, field) for field in _CONVERSATION_SETTINGS}
    )
    return payload


def _message_payload(message: Message) -> Dict[str, Any]:
    payload = {
        "ref": message.id,
        "created_at": message.created_at,
        "model_identifier": _identifier(message.llm),
    }
    payload.update({field: getattr(message, field) for field in _MESSAGE_FIELDS})
    return payload


def _workflows(user) -> List[Dict[str, Any]]:
    workflows = Workflow.active_objects.filter(user=user).order_by("created_at")
    return [_workflow_payload(workflow) for workflow in workflows]


def _workflow_payload(workflow: Workflow) -> Dict[str, Any]:
    nodes = WorkflowNode.objects.filter(workflow=workflow).select_related(
        "data_content_type"
    )
    edges = WorkflowEdge.objects.filter(workflow=workflow)

    payload = {
        "ref": workflow.id,
        "created_at": workflow.created_at,
        "nodes": [_node_payload(node) for node in nodes],
        "edges": [
            {field: getattr(edge, field) for field in _EDGE_FIELDS} for edge in edges
        ],
    }
    payload.update({field: getattr(workflow, field) for field in _WORKFLOW_FIELDS})
    return payload


def _node_payload(node: WorkflowNode) -> Dict[str, Any]:
    payload = {field: getattr(node, field) for field in _NODE_FIELDS}
    payload["data"] = node_data_payload(node.data_object)
    return payload


def _identifier(model_row) -> Optional[str]:
    return model_row.identifier if model_row is not None else None
