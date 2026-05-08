"""
Learning Progress Helpers Module

Functions for streaming learning progress assessments in Socratic mode.
Extracted from MessageCoordinator to improve modularity.

These functions handle the complete learning progress streaming flow:
- Billing checks during streaming
- Progress accumulation and chunk sending
- Assessment persistence
- Completion notification
"""

import logging
from typing import Any, Awaitable, Callable, Dict, Optional

from conversations.models import LLM, Conversation, Message
from conversations.services.message_helpers.db_helpers import (
    update_message_learning_progress,
)
from conversations.services.message_helpers.response_builders import (
    build_usage_with_totals,
)
from conversations.services.websocket_response_service import WebSocketResponseService

logger = logging.getLogger(__name__)


async def run_learning_progress_stream(
    conversation: Conversation,
    message_data: Dict[str, Any],
    message_obj: Message,
    llm: LLM,
    platform: str,
    learning_progress_service,
    billing_service,
    user,
    send_callback: Callable[[Dict], Awaitable[None]],
    get_llm_callback: Callable[[str], Awaitable[Optional[LLM]]],
) -> None:
    """
    Stream learning progress assessment (Socratic only).

    This function orchestrates the complete learning progress streaming flow:
    1. Resolves the progress LLM (may differ from chat LLM)
    2. Streams progress assessment chunks to client
    3. Performs billing checks during streaming (authenticated users only)
    4. Saves assessment to database on completion
    5. Updates message with learning progress metadata
    6. Sends completion notification

    Args:
        conversation: The conversation instance
        message_data: Original message data with Socratic config (bot_meta, progress_llm_id)
        message_obj: The AI message to assess
        llm: LLM instance used for the chat response (fallback for progress)
        platform: Platform name ("DARE" or "SocraticBots")
        learning_progress_service: LearningProgressService instance
        billing_service: BillingService instance
        user: User instance (None for public bots)
        send_callback: Async callback for sending WebSocket messages
        get_llm_callback: Async callback for fetching LLM by ID
    """
    try:
        progress_llm_id = message_data.get("progress_llm_id") or llm.id
        progress_llm = await get_llm_callback(progress_llm_id)

        if not progress_llm:
            logger.warning("Progress LLM not found, skipping assessment")
            return

        progress_accumulator = ""
        last_usage = None

        # All Socratic bot data comes from bot_meta (single source of truth)
        bot_meta = message_data.get("bot_meta", {})
        learning_goals = bot_meta.get("learning_goals", "")
        tracking_prompt = bot_meta.get("tracking_prompt", "")

        # Stream progress assessment
        async for chunk, usage in learning_progress_service.assess_learning_progress(
            conversation=conversation,
            last_message=message_obj,
            learning_goals=learning_goals,
            tracking_prompt=tracking_prompt,
            llm=progress_llm,
            max_tokens=2048,
            temperature=0.7,
            conversation_history_limit=80,
            bot_meta=bot_meta,
            user=user,
        ):
            # Track usage for billing (authenticated users only)
            if usage:
                last_usage = usage
                # Skip billing check for public bots (user is None)
                if user:
                    can_continue, _ = (
                        await billing_service.check_streaming_credit_usage(
                            user, progress_llm, usage
                        )
                    )
                    if not can_continue:
                        error_payload = WebSocketResponseService.format_progress_error(
                            "Insufficient credits during progress assessment"
                        )
                        await send_callback(error_payload)
                        return

            if chunk and chunk.strip():
                progress_accumulator += chunk
                progress_payload = WebSocketResponseService.format_progress_chunk(
                    conversation_id=str(conversation.id),
                    message_id=str(message_obj.id),
                    chunk=chunk,
                )
                await send_callback(progress_payload)

        if progress_accumulator.strip():
            # Build usage metadata for frontend
            metadata = {
                "llm_model": getattr(progress_llm, "identifier", None),
                "usage": build_usage_with_totals(last_usage),
                "platform": platform or "DARE",
                "tracking_prompt_used": (
                    tracking_prompt[:100] if tracking_prompt else ""
                ),
            }

            # Save assessment to database
            assessment = await learning_progress_service._save_progress_assessment(
                conversation=conversation,
                content=progress_accumulator,
                learning_goals=learning_goals,
                last_message=message_obj,
                metadata=metadata,
            )

            # Update message with learning progress data
            await update_message_learning_progress(
                message_obj,
                assessment,
                learning_goals,
                tracking_prompt,
                progress_llm,
                last_usage,
            )

            # Send completion notification
            completion_payload = WebSocketResponseService.format_progress_complete(
                conversation_id=str(conversation.id),
                message_id=str(message_obj.id),
                input_tokens=last_usage.get("input_tokens") if last_usage else None,
                output_tokens=last_usage.get("output_tokens") if last_usage else None,
            )
            await send_callback(completion_payload)

    except Exception as e:
        logger.exception(f"Error running learning progress stream: {str(e)}")
        # Non-fatal - don't interrupt the conversation
