"""
Regeneration Helpers Module

Functions for preparing message regeneration data.
Extracted from MessageCoordinator to improve modularity.

These functions handle:
- Preparing regeneration data based on original message type
- Switching models for image generation regeneration
- Handling audio transcription regeneration
"""

import logging
from typing import Dict, Any, Optional, Tuple, Callable, Awaitable

from channels.db import database_sync_to_async

from conversations.models import Message, LLM
from conversations.constants import ErrorCode
from conversations.services.message_helpers.db_helpers import get_message_media_file_ids

logger = logging.getLogger(__name__)


async def prepare_regeneration_data(
    ai_message: Message,
    llm: LLM,
    message_data: Dict[str, Any],
    preceding_user_message: Message,
    send_error_callback: Callable[[str, str, Optional[Dict]], Awaitable[None]],
) -> Tuple[Optional[LLM], Dict[str, Any]]:
    """
    Prepare message data for regeneration based on original message type.

    Handles special cases:
    - Image generation: Switch to chat model (can't regenerate images)
    - Audio transcription: Re-run transcription with original media files
    - Regular chat: Disable special modes

    Args:
        ai_message: The AI message being regenerated
        llm: The LLM to use (may be overridden)
        message_data: Original message data dictionary
        preceding_user_message: The user message that preceded the AI response
        send_error_callback: Async callback for sending error messages

    Returns:
        Tuple of (llm, regeneration_message_data) or (None, {}) on error
    """
    original_llm = ai_message.llm
    regeneration_message_data = message_data.copy()
    regeneration_message_data["message"] = preceding_user_message.message

    # Image generator: switch to chat model
    if original_llm and original_llm.is_image_generator and llm == original_llm:
        default_llm = await database_sync_to_async(LLM.get_default_chat_model)()
        if not default_llm:
            await send_error_callback(
                ErrorCode.VALIDATION_ERROR,
                "Cannot regenerate image: No chat model available.",
                None
            )
            return None, {}
        llm = default_llm

    elif original_llm and original_llm.is_audio_transcriber and llm == original_llm:
        media_file_ids = message_data.get("media_ids") or await get_message_media_file_ids(
            preceding_user_message
        )

        if not media_file_ids:
            await send_error_callback(
                ErrorCode.VALIDATION_ERROR,
                "Cannot regenerate transcription: No audio/video files found.",
                None
            )
            return None, {}

        regeneration_message_data["audio_transcription_enabled"] = True
        regeneration_message_data["media_ids"] = media_file_ids

    # Regular chat: disable special modes
    else:
        regeneration_message_data["image_generation_enabled"] = False
        regeneration_message_data["audio_transcription_enabled"] = False

    return llm, regeneration_message_data
