"""
Artifact Helpers Module

Functions for handling artifact intent detection and routing.
Extracted from MessageCoordinator to improve modularity.

These functions handle:
- Artifact intent detection (chat, diagram, chart, create, edit)
- Routing to appropriate artifact generation handlers
"""

import logging
from typing import Dict, Any
from uuid import UUID

from conversations.models import Conversation, Message, LLM

logger = logging.getLogger(__name__)


async def handle_artifact_intent(
    message_data: Dict[str, Any],
    message_obj: Message,
    llm: LLM,
    conversation: Conversation,
    user,
    intent_service,
    simple_artifact_coordinator,
) -> bool:
    """
    Handle artifact intent detection and routing.

    Detects the user's intent from their message and routes to the
    appropriate artifact generation handler. Supports:
    - chat: Normal conversation (returns False to continue normal flow)
    - diagram: Mermaid diagram generation
    - chart: Chart.js chart generation
    - create/edit: Code artifact creation or editing

    Args:
        message_data: Validated message data containing the user message
        message_obj: AI message object to populate
        llm: LLM instance for intent detection and generation
        conversation: Current conversation instance
        user: User instance (None for public bots)
        intent_service: ArtifactIntentService instance
        simple_artifact_coordinator: SimpleArtifactCoordinator instance

    Returns:
        True if artifact was handled (caller should return), False to continue normal flow
    """
    active_artifact_id = message_data.get("active_artifact_id")

    try:
        # Get active artifact summary for context
        active_artifact = None
        if active_artifact_id:
            active_artifact = await intent_service.get_active_artifact_summary(
                active_artifact_id,
                conversation_id=conversation.conversation_id,
            )

        # Detect intent using LLM
        intent = await intent_service.detect_intent(
            message=message_data["message"],
            active_artifact=active_artifact,
            llm=llm,
            user=user,
        )

        logger.info(f"Artifact intent detected: {intent}")

        if intent == "chat":
            return False  # Continue to normal message streaming

        if intent == "diagram":
            logger.info("Intent is 'diagram', using tool-based diagram generation")
            await simple_artifact_coordinator.stream_diagram_response(
                message_data=message_data, message_obj=message_obj, llm=llm
            )
            return True

        if intent == "chart":
            logger.info("Intent is 'chart', using tool-based chart generation")
            await simple_artifact_coordinator.stream_chart_response(
                message_data=message_data, message_obj=message_obj, llm=llm
            )
            return True

        # Create or edit artifact
        await simple_artifact_coordinator.stream_artifact_response(
            message_data=message_data,
            message_obj=message_obj,
            llm=llm,
            intent=intent,
            active_artifact_id=active_artifact_id,
        )
        return True

    except Exception as e:
        logger.exception(f"Error in artifact intent detection: {e}")
        logger.warning("Falling back to normal message flow due to intent detection error")
        return False
