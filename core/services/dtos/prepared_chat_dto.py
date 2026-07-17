"""Prepared-chat DTO: everything a tool loop needs to stream rounds."""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .generation_dto import GenerationConfig


@dataclass(frozen=True)
class PreparedChat:
    """One-time chat preparation, reused across tool-loop rounds.

    Built by ``LLMService.prepare_chat()``: the full prompt (system, context,
    RAG, history, current message), the resolved tools, media, and the
    dispatch-ready provider service. Rounds append tool turns to a copy of
    ``messages`` and re-stream — the prompt is never rebuilt, so retrieval
    pre-injection and snippet/trace persistence run exactly once per turn.

    Attributes:
        messages: Internal (OpenAI-format) message list for round 1.
        tools: Resolved tool schemas (MCP + DARE + provider-native), or None.
        images: Processed media passed to the provider on every round.
        ai_service: Provider service resolved for the user's wallet.
        generation: Generation config (max_tokens, temperature, effort).
        memory_context: Memory items used while building the prompt.
        llm: Resolved LLM model row.
        context_trace: Timed context-assembly stages for the turn (the
            ``context_trace`` event payload), or None for flows that don't
            record one (Socratic mode).
    """

    messages: List[Dict[str, Any]]
    tools: Optional[List[Dict[str, Any]]]
    images: List[Dict[str, Any]]
    ai_service: Any
    generation: GenerationConfig
    memory_context: List[Dict[str, Any]]
    llm: Any
    context_trace: Optional[Dict[str, Any]] = None
