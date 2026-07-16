"""
Unified tool-call event vocabulary for the chat frontend.

One set of events for every tool origin (DARE, MCP, provider-native),
covering the full lifecycle the FE renders:

    tool_call_pending        the model is writing the call (name known)
    tool_call_args_progress  arguments are streaming (throttled char count)
    tool_call_executing      the tool is running (final arguments attached)
    tool_call_result         completed or failed, with the typed result
    tool_rounds_capped       the loop hit its round cap

Replaces the three legacy vocabularies (``mcp_tool_call``/``mcp_tool_result``,
``dareToolCall``/``dareToolResult``, ``tool_call``/``tool_result``). Payload
keys are camelized before send; the ``origin`` field routes the result into
exactly one typed field (``dareResult`` / ``mcpResult`` / ``providerResult``),
matching the persisted-history payload shape from
``conversation_service._build_tool_call_payload``.
"""

import logging
import time
from typing import Any, Callable, Dict, Optional

from djangorestframework_camel_case.util import camelize

from conversations.constants import ToolCallOrigin

logger = logging.getLogger(__name__)

# Minimum seconds between args-progress events per tool call.
ARGS_PROGRESS_MIN_INTERVAL = 0.4


class ToolEventEmitter:
    """Emits unified tool-call lifecycle events for one assistant message."""

    def __init__(self, send_callback: Callable, message_id: int) -> None:
        self._send = send_callback
        self._message_id = message_id
        self._last_progress_at: Dict[str, float] = {}

    async def tool_call_pending(
        self,
        tool_call_id: str,
        tool_name: str,
        server_slug: str,
        origin: str,
        round_index: int,
    ) -> None:
        await self._emit(
            "tool_call_pending",
            tool_call_id,
            tool_name,
            server_slug,
            origin,
            round_index,
            status="pending",
        )

    async def args_progress(self, tool_call_id: str, args_chars: int) -> None:
        """Throttled argument-size progress while the model writes the call."""
        now = time.monotonic()
        last = self._last_progress_at.get(tool_call_id)
        if last is not None and now - last < ARGS_PROGRESS_MIN_INTERVAL:
            return
        self._last_progress_at[tool_call_id] = now
        await self._send_payload(
            {
                "type": "tool_call_args_progress",
                "message_id": self._message_id,
                "tool_call_id": tool_call_id,
                "args_chars": args_chars,
            }
        )

    async def tool_call_executing(
        self,
        tool_call_id: str,
        tool_name: str,
        server_slug: str,
        origin: str,
        round_index: int,
        arguments: Dict[str, Any],
    ) -> None:
        await self._emit(
            "tool_call_executing",
            tool_call_id,
            tool_name,
            server_slug,
            origin,
            round_index,
            status="executing",
            arguments=arguments,
        )

    async def tool_call_result(
        self,
        tool_call_id: str,
        tool_name: str,
        server_slug: str,
        origin: str,
        round_index: int,
        status: str,
        result: Optional[Any] = None,
        error: Optional[str] = None,
    ) -> None:
        """Emit the outcome, routing ``result`` into the origin's typed field."""
        extra: Dict[str, Any] = {"error": error}
        if origin == ToolCallOrigin.DARE:
            extra["dare_result"] = result
        elif origin == ToolCallOrigin.PROVIDER:
            extra["provider_result"] = result
        else:
            extra["mcp_result"] = result
        await self._emit(
            "tool_call_result",
            tool_call_id,
            tool_name,
            server_slug,
            origin,
            round_index,
            status=status,
            **extra,
        )

    async def rounds_capped(self, round_index: int) -> None:
        await self._send_payload(
            {
                "type": "tool_rounds_capped",
                "message_id": self._message_id,
                "round": round_index,
            }
        )

    async def _emit(
        self,
        event_type: str,
        tool_call_id: str,
        tool_name: str,
        server_slug: str,
        origin: str,
        round_index: int,
        **extra: Any,
    ) -> None:
        await self._send_payload(
            {
                "type": event_type,
                "message_id": self._message_id,
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "server_slug": server_slug,
                "origin": origin,
                "round": round_index,
                **extra,
            }
        )

    async def _send_payload(self, payload: Dict[str, Any]) -> None:
        if payload.get("type") != "tool_call_args_progress":
            logger.info(
                "[journey] mid=%s event %s call=%s status=%s",
                self._message_id,
                payload.get("type"),
                payload.get("tool_call_id"),
                payload.get("status"),
            )
        try:
            await self._send(camelize(payload))
        except Exception as exc:
            logger.warning("Tool event emit failed (%s): %s", payload.get("type"), exc)
