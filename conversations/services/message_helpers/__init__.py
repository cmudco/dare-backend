"""
Message Helpers Package

Helper modules for MessageCoordinator containing pure utility functions
for data transformation, media processing, and response building.

These functions are stateless and easily testable.
"""

from .billing_helpers import handle_insufficient_balance
from .db_helpers import (
    fetch_preceding_user_message,
    get_ai_message_by_id,
    get_conversation_default_descriptor,
    get_message_media_file_ids,
    parse_model_id,
    should_generate_title,
    update_message_learning_progress,
)
from .finalization_helpers import finalize_message
from .learning_progress_helpers import run_learning_progress_stream
from .media_helpers import build_generated_image_data
from .regeneration_helpers import prepare_regeneration_data
from .response_builders import build_transcription_data, build_usage_with_totals

__all__ = [
    # Response builders
    "build_transcription_data",
    "build_usage_with_totals",
    # Media helpers
    "build_generated_image_data",
    # Database helpers
    "get_ai_message_by_id",
    "get_message_media_file_ids",
    "parse_model_id",
    "get_conversation_default_descriptor",
    "fetch_preceding_user_message",
    "should_generate_title",
    "update_message_learning_progress",
    # Learning progress helpers
    "run_learning_progress_stream",
    # Billing helpers
    "handle_insufficient_balance",
    # Finalization helpers
    "finalize_message",
    # Regeneration helpers
    "prepare_regeneration_data",
]
