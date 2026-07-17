"""
Billing Helpers Module

Functions for handling billing operations during message streaming.
Extracted from MessageCoordinator to improve modularity.

These functions handle:
- Mid-stream insufficient balance handling
"""

import logging
from typing import Awaitable, Callable, Dict, Optional

from channels.db import database_sync_to_async

from conversations.models import Message
from conversations.services.websocket_response_service import \
    WebSocketResponseService

logger = logging.getLogger(__name__)


async def handle_insufficient_balance(
    message_obj: Message,
    ai_response: str,
    token_usage: Dict,
    error_response: Dict,
    billing_service,
    send_callback: Callable[[Dict], Awaitable[None]],
    send_error_callback: Callable[[str, str, Optional[Dict]], Awaitable[None]],
) -> None:
    """
    Handle mid-stream insufficient balance.

    The streaming credit gate has already debited the wallet's remaining
    balance by the time this runs, so the message is finalized WITHOUT a
    second billing pass — ``finalize_ai_message`` would raise
    ``insufficient_balance`` against the now-empty wallet and the turn
    would die silently (no partial message, no error, permanent spinner).

    Each step is isolated: a finalize failure must never block the partial
    message or the error notification from reaching the client.

    Args:
        message_obj: Message object being streamed
        ai_response: Accumulated response so far
        token_usage: Current token usage
        error_response: Error details from billing service
        billing_service: BillingService instance
        send_callback: Async callback for sending WebSocket messages
        send_error_callback: Async callback for sending error messages
    """
    if not ai_response.strip():
        ai_response = (
            "This response was interrupted because your credits ran out. "
            "Add credits and try again."
        )

    finalized_message = None
    try:
        finalized_message, _cost = await database_sync_to_async(
            billing_service.finalize_ai_message_no_billing
        )(message_obj, ai_response, token_usage)
    except Exception:
        logger.exception("Failed to finalize interrupted message %s", message_obj.id)

    try:
        partial_payload = await WebSocketResponseService.format_message(
            message=finalized_message or message_obj,
            message_type="message",
            is_sender=False,
            streaming=False,
            regenerate=False,
        )
        await send_callback(partial_payload)
    except Exception:
        logger.exception("Failed to send partial message for %s", message_obj.id)

    try:
        await send_error_callback(
            error_response.get("error", "insufficient_balance"),
            error_response.get("message", "Insufficient balance to continue"),
            error_response,
        )
    except Exception:
        logger.exception(
            "Failed to send insufficient-balance error for %s", message_obj.id
        )
