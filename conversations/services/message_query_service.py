from django.db.models import Prefetch, QuerySet

from conversations.models import Artifact, Conversation, Message
from files.models import File


def attached_files() -> QuerySet[File]:
    """Files as ``AttachedFileSerializer`` reads them: tags and OCR request
    loaded, the parsed document and extracted text each row carries left out.
    """
    return (
        File.active_objects.defer(
            "document_model", "extracted_text", "processing_journey"
        )
        .select_related("ocr_request")
        .prefetch_related("tags")
    )


def message_file_prefetches() -> tuple:
    """A message's attached files and its snippets' source files."""
    return (
        Prefetch("files", queryset=attached_files()),
        Prefetch("snippets__file", queryset=attached_files()),
    )


def messages_for_display(conversation: Conversation) -> QuerySet[Message]:
    """A chat's active messages with everything ``MessageSerializer`` reads.

    ``sender_name`` reads the conversation owner's email. Callers add ordering
    and slicing.
    """
    return (
        Message.active_objects.filter(conversation=conversation)
        .select_related("llm", "conversation__user")
        .prefetch_related(
            *message_file_prefetches(),
            "tags",
            "snippets__library",
            "web_search_sources",
            "mcp_tool_calls",
            Prefetch("artifacts", queryset=Artifact.active_objects.all()),
        )
    )
