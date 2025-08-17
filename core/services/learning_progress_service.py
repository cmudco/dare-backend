from typing import AsyncGenerator, Tuple, Dict, Optional
from channels.db import database_sync_to_async
from conversations.models import Conversation, Message, LearningProgressAssessment, LLM
from core.services.llm_service import LLMService, AIService
import logging

# Correctly map sender roles using the shared enum
from conversations.constants import SenderType

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
        max_tokens: int = 2048,
        temperature: float = 0.7,
        conversation_history_limit: int = 20,
        # New: include bot metadata for subject/topic/title
        bot_meta: Optional[Dict] = None,
    ) -> AsyncGenerator[Tuple[str, Dict], None]:
        """
        Stream an assessment using the exact evaluation prompt shape requested:

        Learning Goals: ...
        Conversation Context: Subject/Topic/Title (from bot_meta)
        Student's Last Message and AI Response to Assess: (last_message.message)
        Assessment Instructions: tracking_prompt
        """
        try:
            if not learning_goals or not learning_goals.strip():
                raise ValueError("Learning goals cannot be empty")
            if not tracking_prompt or not tracking_prompt.strip():
                raise ValueError("Tracking prompt cannot be empty")

            if not llm:
                llm = await self._get_default_progress_llm()

            # Build the evaluation input exactly as specified
            meta = bot_meta or {}
            subject = meta.get("subject", "")
            topic = meta.get("topic", "")
            title = meta.get("title", "")

            last_msg_text = (last_message.message if last_message and last_message.message else "").strip()

            evaluation_input = f"""Learning Goals:
{learning_goals}

Conversation Context:
Subject: {subject}
Topic: {topic}
Title: {title}

Student's Last Message and AI Response to Assess:
{last_msg_text}

Assessment Instructions:
{tracking_prompt}

Please provide a concise assessment focusing on the student's progress toward the learning goals."""

            # Single-message prompt (no separate system message per request)
            messages = [
                {"role": "user", "content": evaluation_input},
            ]

            ai_service = self._get_ai_service(llm)
            async for chunk, usage in ai_service.stream_chat_completion(
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            ):
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

    def _get_ai_service(self, llm: LLM) -> AIService:
        """Return the provider-specific AI service for the given LLM."""
        return self.llm_service._get_ai_service(llm)

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
    def get_latest_assessment(self, conversation: Conversation) -> Optional[Dict]:
        """Return the latest assessment as a serializable dict (or None)."""
        assessment = LearningProgressAssessment.active_objects.filter(
            conversation=conversation
        ).order_by('-created_at').first()
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
            "trackingPromptUsed": meta.get("tracking_prompt_used") or meta.get("trackingPromptUsed"),
        }
        return {
            "id": str(assessment.id),
            "content": assessment.content,
            "learningGoals": assessment.learning_goals,
            "createdAt": assessment.created_at.isoformat(),
            "metadata": normalized_meta,
        }