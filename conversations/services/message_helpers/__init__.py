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

from .learning_progress_helpers import (
    run_learning_progress_stream,
)

from .billing_helpers import (
    update_public_bot_budget,
    handle_insufficient_balance,
)

from .artifact_helpers import (
    handle_artifact_intent,
)

from .finalization_helpers import (
    finalize_message,
)

from .regeneration_helpers import (
    prepare_regeneration_data,
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
    # Learning progress helpers
    "run_learning_progress_stream",
    # Billing helpers
    "update_public_bot_budget",
    "handle_insufficient_balance",
    # Artifact helpers
    "handle_artifact_intent",
    # Finalization helpers
    "finalize_message",
    # Regeneration helpers
    "prepare_regeneration_data",
]

