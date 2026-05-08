"""
Billing Helpers Module

Functions for handling billing operations during message streaming.
Extracted from MessageCoordinator to improve modularity.

These functions handle:
- Mid-stream insufficient balance handling
"""

import logging
from typing import Dict, Optional, Callable, Awaitable

from channels.db import database_sync_to_async

from conversations.models import Message
from conversations.services.websocket_response_service import WebSocketResponseService

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

    When a user runs out of credits during streaming, this function:
    1. Finalizes the partial message with billing
    2. Sends the partial message to the client
    3. Sends an error notification

    Args:
        message_obj: Message object being streamed
        ai_response: Accumulated response so far
        token_usage: Current token usage
        error_response: Error details from billing service
        billing_service: BillingService instance
        send_callback: Async callback for sending WebSocket messages
        send_error_callback: Async callback for sending error messages
    """
    try:
        # Finalize partial message (platform auto-detected from conversation)
        await database_sync_to_async(billing_service.finalize_ai_message)(
            message_obj, ai_response, token_usage
        )

        # Send partial message to client
        partial_payload = await WebSocketResponseService.format_message(
            message=message_obj,
            message_type="message",
            is_sender=False,
            streaming=False,
            regenerate=False,
        )
        await send_callback(partial_payload)

        # Send error
        await send_error_callback(
            error_response.get("error", "insufficient_balance"),
            error_response.get("message", "Insufficient balance to continue"),
            error_response,
        )

    except Exception as e:
        logger.exception(f"Error handling insufficient balance: {str(e)}")
