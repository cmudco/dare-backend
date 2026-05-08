"""
Finalization Helpers Module

Functions for finalizing AI messages after streaming completes.
Extracted from MessageCoordinator to improve modularity.

These functions handle:
- Saving original message content for regenerations
- Billing finalization (authenticated users vs public bots)
- Sending final message payload to client
"""

import logging
from typing import Dict, Optional, Callable, Awaitable
from decimal import Decimal

from channels.db import database_sync_to_async
from django.core.exceptions import ValidationError as DjangoValidationError

from conversations.models import Message
from conversations.constants import ErrorCode, ErrorMessage
from conversations.services.websocket_response_service import WebSocketResponseService
from conversations.services.message_helpers.billing_helpers import (
    update_public_bot_budget,
)

logger = logging.getLogger(__name__)


async def finalize_message(
    message_obj: Message,
    ai_response: str,
    token_usage: Optional[Dict],
    regenerate: bool,
    generated_image_data: Optional[Dict],
    generated_transcription_data: Optional[Dict],
    user,
    conversation,
    billing_service,
    send_callback: Callable[[Dict], Awaitable[None]],
    send_error_callback: Callable[[str, str, Optional[Dict]], Awaitable[None]],
    mark_as_regenerated_callback: Callable[[Message], Awaitable[None]],
) -> None:
    """
    Finalize AI message with billing or budget update.

    This function handles the complete finalization flow:
    1. Saves original message content on first regeneration
    2. Finalizes with appropriate billing strategy (authenticated vs public)
    3. Marks message as regenerated if applicable
    4. Updates bot budget for public bots
    5. Sends final message payload to client

    Args:
        message_obj: Message object to finalize
        ai_response: Complete AI response text
        token_usage: Token usage dictionary
        regenerate: Whether this is a regeneration
        generated_image_data: Optional image generation data
        generated_transcription_data: Optional audio transcription data
        user: User instance (None for public bots)
        conversation: Conversation instance
        billing_service: BillingService instance
        send_callback: Async callback for sending WebSocket messages
        send_error_callback: Async callback for sending error messages
        mark_as_regenerated_callback: Async callback for marking message as regenerated
    """
    try:
        # Save original message content on first regeneration
        if regenerate and not message_obj.original_message:
            message_obj.original_message = message_obj.message
            await database_sync_to_async(message_obj.save)(
                update_fields=["original_message"]
            )

        # Finalize with appropriate billing strategy
        if user:
            # Authenticated user - use wallet billing (platform auto-detected from conversation)
            finalized_message = await database_sync_to_async(
                billing_service.finalize_ai_message
            )(message_obj, ai_response, token_usage or {})

            # Mark as regenerated if applicable
            if regenerate:
                await mark_as_regenerated_callback(finalized_message)
        else:
            # Public bot - no billing, just calculate cost
            finalized_message, cost = await database_sync_to_async(
                billing_service.finalize_ai_message_no_billing
            )(message_obj, ai_response, token_usage or {})

            # Mark as regenerated if applicable
            if regenerate:
                await mark_as_regenerated_callback(finalized_message)

            # Update bot budget if applicable
            await update_public_bot_budget(conversation, cost, message_obj)

        # Send final message to client
        final_payload = await WebSocketResponseService.format_message(
            message=finalized_message,
            message_type="message",
            is_sender=False,
            streaming=False,
            regenerate=regenerate,
            generated_image=generated_image_data,
            generated_transcription=generated_transcription_data,
        )

        await send_callback(final_payload)

    except DjangoValidationError as e:
        logger.error(f"Validation error finalizing message: {str(e)}")
        await send_error_callback(ErrorCode.VALIDATION_ERROR, str(e), None)
    except Exception as e:
        logger.exception(f"Error finalizing message: {str(e)}")
        await send_error_callback(
            ErrorCode.FINALIZE_ERROR, ErrorMessage.FINALIZE_ERROR, None
        )
