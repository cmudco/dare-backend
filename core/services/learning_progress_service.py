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
        conversation_history_limit: int = 80,
        # New: include bot metadata for subject/topic/title
        bot_meta: Optional[Dict] = None,
    ) -> AsyncGenerator[Tuple[str, Dict], None]:
        """
        Stream a comprehensive learning progress assessment including:
        - Full conversation history
        - Previous assessment context for progression tracking
        - Learning goals and tracking instructions
        - Bot metadata (subject/topic/title)
        
        Uses system + user message format for better AI comprehension.
        """
        try:
            if not learning_goals or not learning_goals.strip():
                raise ValueError("Learning goals cannot be empty")
            if not tracking_prompt or not tracking_prompt.strip():
                raise ValueError("Tracking prompt cannot be empty")

            if not llm:
                llm = await self._get_default_progress_llm()

            # Get conversation history (following reference pattern)
            conversation_history = await self._get_conversation_history(
                conversation, limit=conversation_history_limit
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
    def _get_conversation_history(self, conversation: Conversation, limit: int = 20) -> str:
        """Get formatted conversation history as readable transcript."""
        # Get messages in reverse chronological order (newest first)
        messages = Message.active_objects.filter(conversation=conversation).order_by('-created_at')
        
        if limit > 0:
            messages = messages[:limit]  # Take most recent N messages
        conversation_history = ""
        if messages.exists():
            # Reverse to get chronological order (oldest to newest) for the transcript
            for msg in reversed(messages):
                print(msg.message)
                role_name = "User" if msg.sender_type == SenderType.PLAYER else "Assistant"
                conversation_history += f"{role_name}: {msg.message}\n\n"
        else:
            conversation_history = "No previous messages in this conversation.\n\n"
            
        return conversation_history.strip()

    @database_sync_to_async
    def _get_previous_assessment(self, conversation: Conversation) -> str:
        """Get the latest previous assessment content for the conversation."""
        latest_assessment = LearningProgressAssessment.active_objects.filter(
            conversation=conversation
        ).order_by('-created_at').first()
        
        if latest_assessment:
            return latest_assessment.content
        else:
            return "No previous progress assessment found, please provide an initial assessment."

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