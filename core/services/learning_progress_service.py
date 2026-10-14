import logging
from typing import AsyncGenerator, Dict, Optional, Tuple

from channels.db import database_sync_to_async

# Correctly map sender roles using the shared enum
from conversations.constants import SOCRATIC_HISTORY_LIMIT, SenderType
from conversations.models import LLM, Conversation, LearningProgressAssessment, Message
from core.services.llm_service import AIService, LLMService

logger = logging.getLogger(__name__)


class LearningProgressService:
    """Minimal service for streaming and persisting learning progress assessments."""

    def __init__(self):
        self.llm_service = LLMService()

    async def assess_learning_progress(
        self,
        conversation: Conversation,
        learning_goals: str,
        tracking_prompt: str,
        last_message: Message = None,
        llm: LLM = None,
        max_tokens: int = 32000,
        temperature: float = 0.7,
        history_limit: int = SOCRATIC_HISTORY_LIMIT,
        # New: include bot metadata for subject/topic/title
        bot_meta: Optional[Dict] = None,
        user: Optional[object] = None,
    ) -> AsyncGenerator[Tuple[str, Dict], None]:
        """
        Stream a comprehensive learning progress assessment including:
        - Full conversation history
        - Previous assessment context for progression tracking
        - Learning goals and tracking instructions
        - Bot metadata (subject/topic/title)

        Uses system + user message format for better AI comprehension.
        """
        # Progress tracking is optional. Incomplete bot setup is not a provider error.
        if not (learning_goals or "").strip() or not (tracking_prompt or "").strip():
            return

        try:
            if not llm:
                llm = await self._get_default_progress_llm()

            # Get conversation history (following reference pattern)
            conversation_history = await self._get_conversation_history(
                conversation, limit=history_limit
            )

            # Get latest previous assessment (following reference pattern)
            previous_assessment_text = await self._get_previous_assessment(conversation)

            # Build comprehensive system prompt (following reference pattern)
            meta = bot_meta or {}
            subject = meta.get("subject", "")
            topic = meta.get("topic", "")
            title = meta.get("title", "")

            system_prompt = f"""Learning Goals:
{learning_goals}

Conversation Context:
Subject: {subject}
Topic: {topic}
Title: {title}

Progress Tracking Instructions:
{tracking_prompt}"""

            # Build user message with full context (following reference pattern)
            user_message = f"""Analyze the new conversation and update the current progress status.
Refer to the conversation history when necessary:

{conversation_history}

Current Progress:
{previous_assessment_text}

If there is no current status, follow the system prompt to make a new status report."""

            # System + User message format (following reference pattern)
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ]

            ai_service = await self._get_ai_service(
                llm,
                user=user,
                bot_id=conversation.bot_id if conversation is not None else None,
            )
            event_stream = ai_service.stream_chat_completion(
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            async for chunk, usage in LLMService._stream_legacy_chunks(event_stream):
                yield chunk, usage

        except Exception as e:
            logger.exception(f"Error in learning progress assessment: {e}")
            yield "", {"error": True, "message": str(e)}

    @database_sync_to_async
    def _save_progress_assessment(
        self,
        conversation: Conversation,
        content: str,
        learning_goals: str,
        last_message: Message = None,
        metadata: Dict = None,
    ) -> LearningProgressAssessment:
        """Persist a completed assessment."""
        return LearningProgressAssessment.active_objects.create(
            conversation=conversation,
            last_message=last_message,
            content=content,
            learning_goals=learning_goals,
            metadata=metadata or {},
        )

    async def _get_ai_service(
        self,
        llm: LLM,
        user: Optional[object] = None,
        bot_id: Optional[int] = None,
    ) -> AIService:
        """Return the provider-specific AI service for the given LLM.

        Forwards ``user`` and ``bot_id`` so the same wallet that pays for the
        bot's replies decides which key pays for the assessment call.
        """
        return await self.llm_service._get_ai_service(llm, user=user, bot_id=bot_id)

    @database_sync_to_async
    def _get_default_progress_llm(self) -> LLM:
        """Pick a reasonable default LLM for assessments."""
        llm = LLM.objects.filter(is_reasoning=True, is_active=True).first()
        if llm:
            return llm
        llm = LLM.objects.filter(is_active=True).first()
        if llm:
            return llm
        llm = LLM.objects.first()
        if llm:
            return llm
        raise ValueError("No LLM models configured for progress tracking")

    @database_sync_to_async
    def _get_conversation_history(
        self, conversation: Conversation, limit: int = SOCRATIC_HISTORY_LIMIT
    ) -> str:
        """The latest ``limit`` messages (all when 0) as a chronological transcript."""
        messages = Message.active_objects.filter(conversation=conversation).order_by(
            "-created_at"
        )
        if limit:
            messages = messages[:limit]
        messages = list(reversed(messages))
        conversation_history = ""
        if messages:
            for msg in messages:
                role_name = (
                    "User" if msg.sender_type == SenderType.PLAYER else "Assistant"
                )
                conversation_history += f"{role_name}: {msg.message}\n\n"
        else:
            conversation_history = "No previous messages in this conversation.\n\n"

        return conversation_history.strip()

    @database_sync_to_async
    def _get_previous_assessment(self, conversation: Conversation) -> str:
        """Get the latest previous assessment content for the conversation."""
        latest_assessment = (
            LearningProgressAssessment.active_objects.filter(conversation=conversation)
            .order_by("-created_at")
            .first()
        )

        if latest_assessment:
            return latest_assessment.content
        else:
            return "No previous progress assessment found, please provide an initial assessment."

    @database_sync_to_async
    def get_latest_assessment(self, conversation: Conversation) -> Optional[Dict]:
        """Return the latest assessment as a serializable dict (or None)."""
        assessment = (
            LearningProgressAssessment.active_objects.filter(conversation=conversation)
            .order_by("-created_at")
            .first()
        )
        if not assessment:
            return None
        # Normalize metadata keys and camelCase fields for FE convenience
        meta = assessment.metadata or {}
        usage = meta.get("usage") or {}
        # Map snake_case to camelCase without mutating DB
        normalized_meta = {
            "llmModel": meta.get("llm_model") or meta.get("llmModel"),
            "usage": {
                "inputTokens": usage.get("input_tokens") or usage.get("inputTokens"),
                "outputTokens": usage.get("output_tokens") or usage.get("outputTokens"),
                "totalTokens": usage.get("total_tokens") or usage.get("totalTokens"),
            },
            "platform": meta.get("platform"),
            "trackingPromptUsed": meta.get("tracking_prompt_used")
            or meta.get("trackingPromptUsed"),
        }
        return {
            "id": str(assessment.id),
            "content": assessment.content,
            "learningGoals": assessment.learning_goals,
            "createdAt": assessment.created_at.isoformat(),
            "metadata": normalized_meta,
        }
