"""Runs one platform-assistant turn through the shared tool loop."""

import logging
from typing import Callable

from asgiref.sync import sync_to_async
from django.utils import timezone

from assistant.constants import (
    ACCOUNT_TOOLS,
    DOCS_SEARCH_TOP_K,
    MAX_OUTPUT_TOKENS,
    MAX_TOOL_CALLS,
    MAX_TOOL_ROUNDS,
    SEARCH_PLATFORM_DOCS,
    AssistantMessageStatus,
)
from assistant.domain.page_context import resolve_page_context
from assistant.domain.prompt import build_messages
from assistant.models import AssistantMessage
from assistant.services.thread_service import Turn, begin_turn, finish_turn
from assistant.services.tool_loop_binding import AssistantToolLoopBinding
from conversations.services.tool_loop_service import ToolLoopResult, ToolLoopService
from core.services.dtos import GenerationConfig, LLMQueryRequest, ToolLoopConfig
from core.services.llm_service import LLMService
from dare_tools.services.retrieval_tool_executor import RetrievalScope

logger = logging.getLogger(__name__)


class AssistantTurnService:
    def __init__(self) -> None:
        self.tool_loop = ToolLoopService(
            LLMService(),
            ToolLoopConfig(max_rounds=MAX_TOOL_ROUNDS, max_tool_calls=MAX_TOOL_CALLS),
        )

    async def begin(self, user, question: str, path: str) -> Turn:
        """Persist the question; raises AssistantLimitReached/Unavailable."""
        return await sync_to_async(begin_turn)(user, question, path)

    async def complete(
        self, turn: Turn, user, path: str, emit: Callable
    ) -> AssistantMessage:
        """Stream the answer to ``emit`` and persist the finished reply."""
        knowledge = turn.knowledge
        tool_slugs = set(ACCOUNT_TOOLS)
        retrieval_scope = None
        if knowledge is not None:
            tool_slugs.add(SEARCH_PLATFORM_DOCS)
            # Vectors live under the docs owner's index. There is no payer: the
            # model already writes a focused search query, so retrieval skips
            # the query-analysis LLM call, and the asking user pays nothing.
            retrieval_scope = RetrievalScope(
                embedding_ids=knowledge.file_ids,
                file_owner_id=knowledge.owner_id,
                max_context_snippets=DOCS_SEARCH_TOP_K,
            )
        messages = build_messages(
            question=turn.question.content,
            page=resolve_page_context(path),
            history=turn.history,
            docs_available=knowledge is not None,
            today=timezone.localdate(),
        )
        # No user on the request: dispatch uses the platform's provider key
        # and nothing is charged to the user's wallet.
        request = LLMQueryRequest(
            message=turn.question.content,
            llm=turn.llm,
            generation=GenerationConfig(max_tokens=MAX_OUTPUT_TOKENS),
            dare_tool_slugs=tuple(sorted(tool_slugs)),
        )
        binding = AssistantToolLoopBinding(reply=turn.reply, user=user, emit=emit)
        try:
            result = await self.tool_loop.run(
                request, binding, retrieval_scope, messages=messages
            )
        except Exception:
            logger.exception(
                "[assistant] turn failed", extra={"reply_id": turn.reply.id}
            )
            result = ToolLoopResult()
        return await sync_to_async(finish_turn)(
            turn.reply,
            text=result.text,
            status=_status(result),
            token_usage=result.token_usage,
            tool_calls=binding.store.tool_calls,
        )


def _status(result: ToolLoopResult) -> str:
    if result.cancelled:
        return AssistantMessageStatus.STOPPED
    if not result.text.strip():
        return AssistantMessageStatus.FAILED
    return AssistantMessageStatus.COMPLETED
