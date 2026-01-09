"""
Message Helpers Package

Helper modules for MessageCoordinator containing pure utility functions
for data transformation, media processing, and response building.

These functions are stateless and easily testable.
"""

from .response_builders import (
    build_transcription_data,
    build_usage_with_totals,
)

from .media_helpers import (
    build_generated_image_data,
)

from .db_helpers import (
    get_ai_message_by_id,
    get_message_media_file_ids,
    fetch_llm_by_id,
    get_conversation_default_llm,
    fetch_preceding_user_message,
    should_generate_title,
    update_message_learning_progress,
)

__all__ = [
    # Response builders
    "build_transcription_data",
    "build_usage_with_totals",
    # Media helpers
    "build_generated_image_data",
    # Database helpers
    "get_ai_message_by_id",
    "get_message_media_file_ids",
    "fetch_llm_by_id",
    "get_conversation_default_llm",
    "fetch_preceding_user_message",
    "should_generate_title",
    "update_message_learning_progress",
]

