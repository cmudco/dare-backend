"""
Chat's implementation of the tool-loop seam.

Everything Message/Conversation-specific the loop used to do inline lives
here: MessageToolCall persistence, context-trace saves, chat's streaming
chunk format, and the mid-stream billing gate. ``ToolLoopService`` itself
is host-agnostic — workflows provide their own binding over the same
protocols (``core.services.tool_loop.binding``).
"""

import logging
from typing import Any, Callable, Dict, Optional, Tuple

from channels.db import database_sync_to_async
from django.utils import timezone

from conversations.constants import (DEFAULT_AI_SENDER_NAME, SenderType,
                                     ToolCallOrigin)
from conversations.models import MessageToolCall
from conversations.services.websocket_response_service import \
    WebSocketResponseService
from core.services.tool_loop.persistence import serialize_persisted_result

logger = logging.getLogger(__name__)


class ChatToolLoopStore:
    """Persists one assistant message's tool calls and context trace."""

    def __init__(self, message_obj) -> None:
        self._message = message_obj
        self.turn_key = str(message_obj.id)

    @property
    def retrieval_target(self) -> Any:
        return self._message

    @database_sync_to_async
    def clear_prior_tool_calls(self) -> None:
        """Regeneration: drop ALL of the message's previous tool-call rows."""
        deleted, _ = MessageToolCall.objects.filter(message=self._message).delete()
        if deleted:
            logger.info(
                "[ChatToolLoopStore] Cleared %s prior tool calls for "
                "regenerated message %s",
                deleted,
                self._message.id,
            )

    @database_sync_to_async
    def save_context_trace(self, trace: Dict[str, Any]) -> None:
        self._message.context_trace = trace
        self._message.save(update_fields=["context_trace"])

    @database_sync_to_async
    def save_tool_call(
        self,
        *,
        call: Any,
        server_slug: str,
        origin: str,
        arguments: Dict[str, Any],
        raw_result: Dict[str, Any],
        is_error: bool,
        error: str,
        round_index: int,
        execution_time_ms: int,
    ) -> None:
        """Persist the MessageToolCall row rendered in conversation history."""
        try:
            result_text = None
            if raw_result and not is_error:
                result_text = serialize_persisted_result(raw_result)
            MessageToolCall.objects.create(
                message=self._message,
                tool_call_id=call.id,
                server_slug=server_slug,
                origin=(
                    origin if origin in ToolCallOrigin.values else ToolCallOrigin.MCP
                ),
                tool_name=call.name,
                arguments=arguments,
                status="failed" if is_error else "completed",
                result=result_text,
                error=error or None,
                executed_at=timezone.now(),
                round_index=round_index,
            )
        except Exception as exc:
            logger.error("Failed to save MessageToolCall: %s", exc)


class ChatStreamSink:
    """Streams accumulated text/thinking in chat's chunk payload format."""

    def __init__(self, message_obj, send_callback: Callable, regenerate: bool) -> None:
        self._message = message_obj
        self._send = send_callback
        self._regenerate = regenerate

    async def text(self, accumulated_text: str) -> None:
        await self._send(
            WebSocketResponseService.format_streaming_chunk(
                message_id=self._message.id,
                chunk=accumulated_text,
                is_complete=False,
                metadata=self._metadata(),
            )
        )

    async def thinking(self, accumulated_text: str, thinking_summary: str) -> None:
        await self._send(
            WebSocketResponseService.format_streaming_chunk(
                message_id=self._message.id,
                chunk=accumulated_text,
                is_complete=False,
                metadata={
                    **self._metadata(),
                    "thinkingSummary": thinking_summary,
                },
            )
        )

    def _metadata(self) -> Dict[str, Any]:
        return {
            "senderName": DEFAULT_AI_SENDER_NAME,
            "senderType": SenderType.AI_ASSISTANT,
            "streaming": True,
            "regenerate": self._regenerate,
            "createdAt": self._message.created_at.isoformat(),
        }


class ChatBillingGate:
    """Chat's mid-stream credit check; public bots (no user) pass freely."""

    def __init__(self, billing_service, user, llm) -> None:
        self._billing_service = billing_service
        self._user = user
        self._llm = llm

    async def check(
        self, usage_totals: Dict[str, Any]
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        if not self._user:
            return True, None
        return await self._billing_service.check_streaming_credit_usage(
            self._user, self._llm, usage_totals
        )


class ChatToolLoopBinding:
    """Bundles the chat implementations of the tool-loop seam."""

    def __init__(
        self,
        *,
        message_obj,
        conversation,
        user,
        llm,
        send_callback: Callable,
        billing_service,
        regenerate: bool = False,
    ) -> None:
        self.message = message_obj
        self.conversation = conversation
        self.user = user
        self.send_callback = send_callback
        self.correlation = {"message_id": message_obj.id}
        self.store = ChatToolLoopStore(message_obj)
        self.sink = ChatStreamSink(message_obj, send_callback, regenerate)
        self.gate = ChatBillingGate(billing_service, user, llm)
