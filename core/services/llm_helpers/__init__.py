"""
LLM Helpers Package

Helper modules for LLMService containing pure utility functions
for context manipulation and data transformation.

These functions are stateless and easily testable.
"""

from .context_helpers import (
    build_transcription_context,
    insert_context_before_last_user_message,
)

from .db_helpers import (
    get_prompt,
    get_conversation_history,
    get_files_from_tags,
    get_files_from_folders,
    get_audio_or_video_files,
    get_full_file_contents,
    get_media_files_as_images,
    get_referenced_conversations_context,
    get_referenced_summaries_context,
    convert_file_to_base64_dict,
)

from .socratic_helpers import (
    build_classic_socratic_messages,
    build_advanced_socratic_messages,
)

from .media_helpers import (
    add_video_transcriptions_to_messages,
    execute_audio_transcription,
)

from .semantic_context_helpers import (
    collect_embedding_file_ids,
    add_semantic_context_to_messages,
)

from .standard_message_helpers import (
    build_standard_messages,
)

__all__ = [
    # Context helpers
    "build_transcription_context",
    "insert_context_before_last_user_message",
    # Database helpers
    "get_prompt",
    "get_conversation_history",
    "get_files_from_tags",
    "get_files_from_folders",
    "get_audio_or_video_files",
    "get_full_file_contents",
    "get_media_files_as_images",
    "get_referenced_conversations_context",
    "get_referenced_summaries_context",
    "convert_file_to_base64_dict",
    # Socratic message builders
    "build_classic_socratic_messages",
    "build_advanced_socratic_messages",
    # Media helpers
    "add_video_transcriptions_to_messages",
    "execute_audio_transcription",
    # Semantic context helpers
    "collect_embedding_file_ids",
    "add_semantic_context_to_messages",
    # Standard message helpers
    "build_standard_messages",
]

