from django.db.models import Prefetch, QuerySet

from conversations.models import Artifact, Conversation, Message


def messages_for_display(conversation: Conversation) -> QuerySet[Message]:
    """A chat's active messages with everything ``MessageSerializer`` reads.

    ``FileSerializer`` reads each file's tags and OCR request, and is used for
    both attached files and snippet sources, so those relations are prefetched
    on both paths. ``sender_name`` reads the conversation owner's email.
    Callers add ordering and slicing.
    """
    return (
        Message.active_objects.filter(conversation=conversation)
        .select_related("llm", "conversation__user")
        .prefetch_related(
            "files__tags",
            "files__ocr_request",
            "tags",
            "snippets__file__tags",
            "snippets__file__ocr_request",
            "snippets__library",
            "web_search_sources",
            "mcp_tool_calls",
            Prefetch("artifacts", queryset=Artifact.active_objects.all()),
        )
    )
