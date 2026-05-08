import logging
from dataclasses import dataclass
from typing import Optional

from asgiref.sync import async_to_sync

from conversations.constants import SenderType
from conversations.models import LLM, Conversation, Message
from core.services.llm_service import LLMService

logger = logging.getLogger(__name__)

SUMMARY_MAX_TOKENS = 1024
SUMMARY_TEMPERATURE = 0.5
SUMMARY_SYSTEM_PROMPT = (
    "You are a helpful assistant. Summarize this conversation in concise "
    "third-person prose. Capture main topics, decisions, recurring themes, "
    "and open follow-ups."
)


@dataclass(frozen=True)
class ConversationSummaryResult:
    summary: str
    llm: Optional[LLM]
    input_tokens: int = 0
    output_tokens: int = 0


def generate_conversation_summary(
    conversation: Conversation,
    completed_message_count: int,
) -> ConversationSummaryResult:
    """Generate a rolling summary for a conversation up to the given threshold.

    Routed through ``LLMService._get_ai_service`` with the conversation owner
    so the call honors their active wallet (DARE / BYO / LITELLM) — pre-
    wallet-refactor this always billed the system DARE wallet regardless of
    the user's preference.
    """
    transcripts = _build_transcript(conversation, completed_message_count)
    if not transcripts.strip():
        logger.warning("No transcript content available for conversation summary")
        return ConversationSummaryResult(summary="", llm=None)

    llm = LLM.get_default_chat_model()
    if llm is None:
        logger.warning("No chat-capable LLM available for conversation summary")
        return ConversationSummaryResult(summary="", llm=None)

    messages = [
        {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Summarize this conversation after {completed_message_count} completed assistant responses.\n\n"
                f"{transcripts}"
            ),
        },
    ]

    try:
        summary = async_to_sync(_generate_summary_text)(
            llm, messages, conversation.user
        )
    except Exception:
        logger.exception(
            "Conversation summary generation failed for conversation %s",
            conversation.conversation_id,
        )
        return ConversationSummaryResult(summary="", llm=llm)

    return ConversationSummaryResult(summary=summary, llm=llm)


def _build_transcript(
    conversation: Conversation,
    completed_message_count: int,
) -> str:
    """Build plain-text transcript content up to the given completed-message threshold."""
    cutoff_message_id = _get_cutoff_message_id(conversation.id, completed_message_count)
    if cutoff_message_id is None:
        return ""

    transcript_lines: list[str] = []
    message_rows = Message.active_objects.filter(conversation=conversation).order_by(
        "created_at",
        "id",
    )
    for message in message_rows:
        speaker = "User" if message.sender_type == SenderType.PLAYER else "Assistant"
        transcript_lines.append(f"{speaker}: {message.message}")
        if message.id == cutoff_message_id:
            break

    if not transcript_lines:
        return ""

    title = conversation.title or f"Conversation {conversation.conversation_id}"
    return f"--- {title} ---\n" + "\n".join(transcript_lines)


def _get_cutoff_message_id(
    conversation_pk: int,
    completed_message_count: int,
) -> Optional[int]:
    """Return the last AI assistant message ID included in the summary threshold."""
    ai_message_rows = list(
        Message.active_objects.filter(
            conversation_id=conversation_pk,
            sender_type=SenderType.AI_ASSISTANT,
        )
        .order_by("created_at", "id")
        .values_list("id", flat=True)[:completed_message_count]
    )
    if len(ai_message_rows) < completed_message_count:
        return None
    return ai_message_rows[-1]


async def _generate_summary_text(
    llm: LLM,
    messages: list[dict[str, str]],
    user: Optional[object] = None,
) -> str:
    """Run a non-streaming LLM call for grouped conversation summaries."""
    llm_service = LLMService()
    ai_service = await llm_service._get_ai_service(llm, user=user)
    return await ai_service.get_chat_completion(
        messages=messages,
        max_tokens=SUMMARY_MAX_TOKENS,
        temperature=SUMMARY_TEMPERATURE,
    )
