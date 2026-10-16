"""Agent versions, copies, and applying an agent's setup to a chat."""

from typing import Any, Dict, Optional

from django.db import transaction

from agents.models import Agent
from conversations.models import Conversation
from core.services.model_ref import model_ref

# Settings an agent carries; copied whole when it is versioned or cloned.
SETTING_FIELDS = (
    "description",
    "prompt",
    "llm",
    "litellm_key",
    "litellm_model_name",
    "max_tokens",
    "temperature",
    "effort",
    "history_limit",
    "rag_mode",
    "max_context_snippets",
    "document_similarity_threshold",
    "enable_web_search",
    "web_fetch_enabled",
    "artifacts_enabled",
    "memory_enabled",
)
RELATION_FIELDS = (
    "content_files",
    "embedding_files",
    "libraries",
    "mcp_servers",
    "dare_tools",
)


def agent_model_ref(agent: Agent) -> Optional[str]:
    return model_ref(agent.llm_id, agent.litellm_key_id, agent.litellm_model_name)


def save_new_version(agent: Agent, changes: Dict[str, Any]) -> Agent:
    """Save `changes` as the next version of `agent`, which stays as history."""
    return _copy(
        agent,
        changes,
        name=changes.get("name", agent.name),
        version=agent.version + 1,
        parent=agent,
    )


def clone_agent(agent: Agent) -> Agent:
    """A new, independent agent with the same setup."""
    return _copy(agent, {}, name=f"COPY OF - {agent.name}", version=1, parent=None)


def _copy(
    agent: Agent, changes: Dict[str, Any], *, name: str, version: int, parent
) -> Agent:
    with transaction.atomic():
        copy = Agent.active_objects.create(
            user=agent.user,
            name=name,
            version=version,
            parent=parent,
            **{
                field: changes.get(field, getattr(agent, field))
                for field in SETTING_FIELDS
            },
        )
        for field in RELATION_FIELDS:
            values = changes[field] if field in changes else getattr(agent, field).all()
            getattr(copy, field).set(values)
    return copy


def apply_agent(conversation: Conversation, agent: Optional[Agent]) -> None:
    """Give the chat the agent's whole setup, or detach the agent.

    Detaching keeps the chat's model settings and drops the agent's sources
    (files, embeddings, libraries), as those belonged to the agent.
    """
    with transaction.atomic():
        conversation.selected_agent = agent
        if agent is None:
            conversation.selected_file_ids = []
            conversation.selected_embedding_ids = []
            conversation.selected_library_ids = []
            conversation.save()
            return

        if agent.prompt_id:
            conversation.prompt_id = agent.prompt_id
        # A gateway agent clears the DARE model so the chat's selection falls
        # through to the agent's model ref; an agent without a model keeps the chat's.
        if agent.llm_id or agent.litellm_key_id:
            conversation.selected_model_id = agent.llm_id
        conversation.temperature = agent.temperature
        conversation.effort = agent.effort
        conversation.max_tokens = agent.max_tokens
        conversation.history_limit = agent.history_limit
        conversation.rag_mode = agent.rag_mode
        conversation.max_context_snippets = agent.max_context_snippets
        conversation.document_similarity_threshold = agent.document_similarity_threshold
        conversation.web_search_enabled = agent.enable_web_search
        conversation.web_fetch_enabled = agent.web_fetch_enabled
        conversation.artifacts_enabled = agent.artifacts_enabled
        conversation.memory_enabled = agent.memory_enabled
        conversation.selected_file_ids = _live_ids(agent.content_files)
        conversation.selected_embedding_ids = _live_ids(agent.embedding_files)
        conversation.selected_library_ids = list(
            agent.libraries.values_list("pk", flat=True)
        )
        conversation.save()
        conversation.selected_mcp_servers.set(agent.mcp_servers.all())
        conversation.selected_dare_tools.set(agent.dare_tools.all())


def _live_ids(files) -> list:
    return list(files.filter(is_deleted=False).values_list("pk", flat=True))
