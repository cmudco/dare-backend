"""
Billing Helpers Module

Functions for handling billing operations during message streaming.
Extracted from MessageCoordinator to improve modularity.

These functions handle:
- Public bot budget updates
- Mid-stream insufficient balance handling
"""

import logging
from decimal import Decimal
from typing import Dict, Optional, Callable, Awaitable

from channels.db import database_sync_to_async

from conversations.models import Conversation, Message
from conversations.services.websocket_response_service import WebSocketResponseService
from conversations.services.bot_budget_service import BotBudgetService

logger = logging.getLogger(__name__)


async def update_public_bot_budget(
    conversation: Conversation,
    cost: Decimal,
    message_obj: Message,
) -> None:
    """
    Update bot budget for public bot conversations.

    Deducts the specified cost from the bot's budget and records
    metadata about the transaction for auditing purposes.

    Args:
        conversation: Conversation instance (must have bot_id for public bots)
        cost: Cost to deduct from bot budget
        message_obj: Message for metadata (tokens, IDs)
    """
    if cost <= Decimal("0") or not conversation.bot_id:
        return

    await BotBudgetService.update_bot_budget(
        bot_id=conversation.bot_id,
        cost=cost,
        metadata={
            "conversation_id": str(conversation.conversation_id),
            "message_id": str(message_obj.id),
            "input_tokens": message_obj.input_tokens,
            "output_tokens": message_obj.output_tokens,
        },
    )


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
