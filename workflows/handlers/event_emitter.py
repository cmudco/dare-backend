"""
WebSocket event emitter for workflow handlers.

Replaces 6+ instances of the pattern:
    if callback:
        try:
            await callback(WebSocketResponseService.format_...)
        except Exception as e:
            logger.warning(f"Failed to send event: {e}")

One class, three methods, zero scattered try/except blocks.
"""
import logging
from typing import Any, Callable, Coroutine, Dict, Optional

from conversations.services.websocket_response_service import WebSocketResponseService


logger = logging.getLogger(__name__)

# Type alias for the async send callback
SendCallback = Optional[Callable[[Dict[str, Any]], Coroutine[Any, Any, None]]]


class EventEmitter:
    """
    Emits WebSocket events to the frontend via a send callback.

    Usage:
        emitter = EventEmitter(context.send_callback, workflow_run_id=run.id)
        await emitter.step_started(node_id, label, node_type, started_at)
        await emitter.step_streaming(node_id, chunk, accumulated_tokens)
        await emitter.step_completed(node_id, response, status, tokens, metadata)

    If no callback is provided, all methods are silent no-ops.
    If the callback raises, the error is logged and execution continues.
    """

    __slots__ = ("_send", "_workflow_run_id")

    def __init__(
        self,
        send_callback: SendCallback,
        workflow_run_id: Optional[int] = None,
    ) -> None:
        self._send = send_callback
        self._workflow_run_id = workflow_run_id

    async def _emit(self, payload: Dict[str, Any]) -> None:
        """Send a payload, swallowing errors so execution never fails on WS issues."""
        if not self._send:
            return
        try:
            await self._send(payload)
        except Exception as e:
            logger.warning(f"WebSocket emit failed ({payload.get('type', '?')}): {e}")

    async def step_started(
        self,
        node_id: str,
        label: Optional[str],
        node_type: str,
        started_at=None,
    ) -> None:
        await self._emit(
            WebSocketResponseService.format_workflow_step_started(
                node_id=node_id,
                label=label,
                node_type=node_type,
                started_at=started_at,
                workflow_run_id=self._workflow_run_id,
            )
        )

    async def step_streaming(
        self,
        node_id: str,
        chunk: str,
        accumulated_tokens: Optional[int] = None,
    ) -> None:
        await self._emit(
            WebSocketResponseService.format_workflow_step_streaming(
                node_id=node_id,
                chunk=chunk,
                accumulated_tokens=accumulated_tokens,
                workflow_run_id=self._workflow_run_id,
            )
        )

    async def step_completed(
        self,
        node_id: str,
        response: str,
        status: str,
        tokens: Optional[Dict[str, int]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        await self._emit(
            WebSocketResponseService.format_workflow_step_completed(
                node_id=node_id,
                response=response,
                status=status,
                tokens=tokens,
                metadata=metadata,
                workflow_run_id=self._workflow_run_id,
            )
        )
