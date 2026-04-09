"""
Artifact Intent Detection Service

LLM-based intent detection for artifact operations.
Uses Gemini Flash for fast, reliable classification.
"""

import logging
from typing import Optional, Dict, Literal

import google as genai
from asgiref.sync import sync_to_async

from conversations.models import Artifact
from conversations.constants import Provider
from core.services.api_key_service import get_provider_api_key

logger = logging.getLogger(__name__)


class ArtifactIntentService:
    """
    Detects user intent when artifact mode is enabled.
    Uses LLM for reliable classification.

    Intent categories:
    - "create": User wants to GENERATE new content (document, code, essay, etc.)
    - "edit": User wants to MODIFY/UPDATE the active artifact
    - "chat": User is asking questions, seeking clarification, or having a conversation
    - "diagram": User wants to CREATE a visual diagram, flowchart, sequence diagram, mindmap (mermaid-based)
    - "chart": User wants to CREATE a data visualization chart (bar, line, pie, area) with numerical data
    """

    # Intent classification prompt
    INTENT_PROMPT = """You are classifying user intent in a chat application with artifact mode enabled.

CONTEXT:
{context}

USER MESSAGE: {message}

Classify the user's intent as one of:
- "create": User wants to GENERATE new content (document, code, essay, etc.)
- "edit": User wants to MODIFY/UPDATE the active artifact
- "chat": User is asking questions, seeking clarification, or having a conversation (NOT generating content)
- "diagram": User wants to CREATE a visual diagram, flowchart, sequence diagram, mindmap, state diagram, class diagram, or architecture diagram (structural/flow visualizations)
- "chart": User wants to CREATE a data visualization chart with numerical data like bar chart, line chart, pie chart, area chart, histogram, or graph showing statistics/trends

Respond with ONLY one word: create, edit, chat, diagram, or chart"""

    # Default LLM for intent detection (Gemini Flash - fast and cheap)
    DEFAULT_INTENT_MODEL = "gemini-2.0-flash"
    
    async def detect_intent(
        self,
        message: str,
        active_artifact: Optional[Dict] = None,
        llm=None,  # Not used anymore - using Gemini Flash directly
        user=None,
    ) -> Literal["create", "edit", "chat", "diagram", "chart"]:
        """
        Detect user intent using LLM.
        
        Args:
            message: User's message
            active_artifact: Currently active artifact summary (title, content preview)
            llm: Ignored - uses Gemini Flash directly
            user: User for API key resolution
            
        Returns:
            "create", "edit", "chat", "diagram", or "chart"
        """
        try:
            # Clean up message - strip quotes that might be present
            clean_message = message.strip().strip('"\'')

            # Build context
            context = "No active artifact."
            if active_artifact:
                context = f"Active artifact: \"{active_artifact.get('title', 'Untitled')}\""

            # Format prompt
            prompt = self.INTENT_PROMPT.format(context=context, message=clean_message)

            # Get API key and configure
            api_key = await get_provider_api_key(Provider.GEMINI.value)
            if not api_key:
                logger.warning("No Gemini API key available, using heuristics")
                return self._heuristic_intent_detection(clean_message, active_artifact is not None)

            genai.configure(api_key=api_key)

            # Use direct google.generativeai (same as test that works)
            model = genai.GenerativeModel('gemini-2.0-flash')

            # Run in thread to avoid blocking
            def _generate():
                response = model.generate_content(
                    prompt,
                    generation_config=genai.types.GenerationConfig(
                        temperature=0.0,
                        max_output_tokens=10,
                    )
                )
                return response.text.strip().lower()

            intent = await sync_to_async(_generate)()

            logger.debug(f"Intent detection raw response: '{intent}'")

            # Check exact match first
            if intent in ("create", "edit", "chat", "diagram", "chart"):
                logger.info(f"Intent detected: {intent} for message: {clean_message[:50]}...")
                return intent

            # Check if intent word is contained in response
            # Check chart before diagram since "chart" might appear in "flowchart"
            for word in ["chart", "diagram", "create", "edit", "chat"]:
                if word in intent:
                    logger.info(f"Intent detected (extracted): {word} for message: {clean_message[:50]}...")
                    return word

            # Fallback to heuristics if LLM response is unclear
            logger.warning(f"Unclear intent response: '{intent}', using heuristics")
            return self._heuristic_intent_detection(clean_message, active_artifact is not None)

        except Exception as e:
            logger.exception(f"Error detecting intent: {e}")
            # Fallback to heuristics on error
            return self._heuristic_intent_detection(message.strip().strip('"\''), active_artifact is not None)

    def _heuristic_intent_detection(
        self,
        message: str,
        has_active_artifact: bool
    ) -> Literal["create", "edit", "chat", "diagram", "chart"]:
        """
        Fallback heuristic-based intent detection.
        Used when LLM is not available.
        """
        message_lower = message.lower().strip()

        # Data chart indicators - check BEFORE diagram patterns
        # These are for numerical data visualizations (recharts)
        chart_patterns = [
            "bar chart", "line chart", "pie chart", "area chart",
            "histogram", "data chart", "sales chart", "revenue chart",
            "comparison chart", "statistics chart", "trend chart",
            "show me a chart", "create a chart", "make a chart",
            "plot the data", "visualize the data", "graph the"
        ]

        for pattern in chart_patterns:
            if pattern in message_lower:
                return "chart"

        # Diagram indicators (mermaid-based structural diagrams)
        diagram_patterns = [
            "diagram", "flowchart", "flow chart", "sequence diagram",
            "mindmap", "mind map", "state diagram", "class diagram",
            "architecture diagram", "workflow", "process flow",
            "entity relationship", "er diagram", "uml"
        ]

        for pattern in diagram_patterns:
            if pattern in message_lower:
                return "diagram"

        # Strong create indicators
        create_patterns = [
            "write", "create", "generate", "make me", "draft",
            "compose", "produce", "build"
        ]

        # Strong edit indicators (only when artifact is active)
        edit_patterns = [
            "add", "remove", "change", "update", "modify",
            "make it", "rewrite", "shorten", "expand", "fix"
        ]

        # Strong chat indicators
        chat_patterns = [
            "what", "why", "how", "explain", "clarify",
            "tell me", "?", "can you explain", "what do you mean"
        ]

        # Check chat patterns first (questions/clarifications)
        for pattern in chat_patterns:
            if pattern in message_lower:
                return "chat"

        # Check edit patterns (only if artifact active)
        if has_active_artifact:
            for pattern in edit_patterns:
                if pattern in message_lower:
                    return "edit"

        # Check create patterns
        for pattern in create_patterns:
            if pattern in message_lower:
                return "create"

        # Default based on context
        if has_active_artifact:
            # If artifact is active and no clear pattern, assume edit
            return "edit"
        else:
            # No artifact, assume create
            return "create"
    
    async def get_active_artifact_summary(
        self, 
        artifact_id: int,
        conversation_id: str = None,
    ) -> Optional[Dict]:
        """
        Get summary of an artifact for intent detection context.
        
        Args:
            artifact_id: ID of the artifact
            conversation_id: ID of the current conversation (for validation)
            
        Returns:
            Dict with title and content preview, or None if not found
            OR if artifact doesn't belong to the specified conversation
        """
        def _get():
            try:
                artifact = Artifact.active_objects.get(id=artifact_id)
                
                # Validate artifact belongs to the specified conversation
                if conversation_id and str(artifact.conversation.conversation_id) != str(conversation_id):
                    logger.warning(
                        f"Artifact {artifact_id} belongs to conversation {artifact.conversation.conversation_id}, "
                        f"not {conversation_id} - ignoring stale artifact reference"
                    )
                    return None
                
                return {
                    "id": artifact.id,
                    "title": artifact.title,
                    "content": artifact.content[:500] if artifact.content else "",
                }
            except Artifact.DoesNotExist:
                return None
        
        return await sync_to_async(_get)()
