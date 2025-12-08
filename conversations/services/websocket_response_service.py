"""
WebSocket Response Formatting Service

Centralizes all WebSocket response formatting logic to eliminate boilerplate
in consumer classes. Handles conversion of database models to WebSocket-friendly
dictionaries with camelCase keys.
"""

import logging
from typing import Dict, Any, Optional, List

from channels.db import database_sync_to_async

from conversations.api.serializers import MessageSerializer
from conversations.constants import SenderType
from conversations.models import Message, Artifact

logger = logging.getLogger(__name__)


class WebSocketResponseService:
    """Service for formatting WebSocket responses consistently."""

    @staticmethod
    def _to_camel_case(snake_str: str) -> str:
        """Convert snake_case to camelCase."""
        components = snake_str.split('_')
        return components[0] + ''.join(x.title() for x in components[1:])

    @classmethod
    def _dict_to_camel_case(cls, data: Dict[str, Any]) -> Dict[str, Any]:
        """Recursively convert dictionary keys from snake_case to camelCase."""
        if not isinstance(data, dict):
            return data

        camel_dict = {}
        for key, value in data.items():
            camel_key = cls._to_camel_case(key)
            if isinstance(value, dict):
                camel_dict[camel_key] = cls._dict_to_camel_case(value)
            elif isinstance(value, list):
                camel_dict[camel_key] = [
                    cls._dict_to_camel_case(item) if isinstance(item, dict) else item
                    for item in value
                ]
            else:
                camel_dict[camel_key] = value
        return camel_dict

    @classmethod
    async def format_message(
        cls,
        message: Message,
        message_type: str = "message",
        is_sender: bool = False,
        streaming: bool = False,
        regenerate: bool = False,
        generated_image: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        Format a Message object for WebSocket transmission.

        Args:
            message: The Message instance to format
            message_type: Type of message ("message", "ai_stream", etc.)
            is_sender: Whether this message is from the current user
            streaming: Whether this is a streaming message
            regenerate: Whether this is a regenerated message
            generated_image: Optional generated image data

        Returns:
            Dictionary with camelCase keys ready for JSON serialization
        """
        # Use MessageSerializer for proper formatting
        @database_sync_to_async
        def serialize_message():
            msg = Message.active_objects.prefetch_related('files', 'tags', 'snippets__file').get(id=message.id)
            return MessageSerializer(msg).data

        serialized_data = await serialize_message()

        # Get additional fields
        llm_id = await database_sync_to_async(lambda: getattr(message.llm, 'id', None))()
        cost = await database_sync_to_async(lambda: message.cost)()
        input_tokens = await database_sync_to_async(lambda: message.input_tokens)()
        output_tokens = await database_sync_to_async(lambda: message.output_tokens)()
        learning_progress_data = await database_sync_to_async(lambda: message.learning_progress_data)()

        # Get linked artifact ID if exists
        # Use fresh DB query to avoid stale cached relation
        @database_sync_to_async
        def get_artifact_id():
            artifact = Artifact.active_objects.filter(message_id=message.id).first()
            return str(artifact.id) if artifact else None

        artifact_id = await get_artifact_id()

        # Debug log to trace artifact ID resolution
        if artifact_id:
            logger.info(f"format_message: message_id={message.id}, resolved artifact_id={artifact_id}")
        else:
            logger.warning(f"format_message: message_id={message.id}, NO artifact found in DB")

        # Build response matching original format
        response = {
            "type": message_type,
            "id": str(message.id),
            "message": message.message,
            "artifactId": artifact_id,
            "senderType": message.sender_type,
            "senderName": message.sender or "AI Assistant",
            "isSender": is_sender,
            "streaming": streaming,
            "regenerate": regenerate,
            "date": message.created_at.isoformat(),
            "llmId": llm_id,
            "files": serialized_data.get("files", []),
            "tags": serialized_data.get("tags", []),
            "snippets": serialized_data.get("snippets", []),
            "feedbackType": message.feedback_type,
            "feedbackText": message.feedback_text,
            "isEdited": message.is_edited,
            "isRegenerated": message.is_regenerated,
            "originalMessage": message.original_message,
            "cost": str(cost) if cost is not None else None,
            "inputTokens": input_tokens,
            "outputTokens": output_tokens,
            "learningProgressData": learning_progress_data or {},
            "generatedImage": generated_image
        }

        return cls._dict_to_camel_case(response)

    @classmethod
    def format_streaming_chunk(
        cls,
        message_id: str,
        chunk: str,
        is_complete: bool = False,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Format an AI streaming chunk for WebSocket transmission.

        Args:
            message_id: ID of the message being streamed
            chunk: The text chunk to send
            is_complete: Whether this is the final chunk
            metadata: Optional metadata (tokens, cost, etc.)

        Returns:
            Dictionary with camelCase keys ready for JSON serialization
        """
        payload = {
            "type": "ai_stream",
            "id": message_id,
            "message": chunk,  # Frontend expects "message" not "chunk"
            "is_complete": is_complete,
        }

        # Merge metadata directly into payload (for senderName, senderType, etc.)
        if metadata:
            payload.update(metadata)

        return cls._dict_to_camel_case(payload)

    @classmethod
    def format_progress_chunk(
        cls,
        conversation_id: str,
        message_id: str,
        chunk: str
    ) -> Dict[str, Any]:
        """
        Format a learning progress streaming chunk for WebSocket transmission.

        Args:
            conversation_id: ID of the conversation
            message_id: ID of the message being assessed
            chunk: The progress assessment chunk to send

        Returns:
            Dictionary ready for JSON serialization (no camelCase - matches backup line 526-531)
            Matches consumers_backup.py lines 526-531:
            {type: "progress_stream", conversationId: "...", messageId: "...", chunk: "..."}
        """
        # NOTE: This is NOT camelized - sent as-is to match backup behavior
        return {
            "type": "progress_stream",
            "conversationId": conversation_id,
            "messageId": message_id,
            "chunk": chunk,
        }

    @classmethod
    def format_progress_complete(
        cls,
        conversation_id: str,
        message_id: str,
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Format a learning progress completion message.

        Args:
            conversation_id: ID of the conversation
            message_id: ID of the message that was assessed
            input_tokens: Optional input token count
            output_tokens: Optional output token count

        Returns:
            Dictionary ready for JSON serialization (no camelCase - matches backup line 583-593)
            Matches consumers_backup.py lines 583-593:
            {type: "progress_complete", conversationId: "...", messageId: "...", inputTokens: ..., outputTokens: ...}
        """
        # NOTE: This is NOT camelized - sent as-is to match backup behavior
        payload = {
            "type": "progress_complete",
            "conversationId": conversation_id,
            "messageId": message_id,
        }

        if input_tokens is not None:
            payload["inputTokens"] = input_tokens
        if output_tokens is not None:
            payload["outputTokens"] = output_tokens

        return payload

    @classmethod
    def format_error(
        cls,
        error_code: str,
        error_message: str,
        details: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Format an error response for WebSocket transmission.

        Args:
            error_code: Short error code (e.g., "INSUFFICIENT_CREDITS")
            error_message: Human-readable error message
            details: Optional additional error details

        Returns:
            Dictionary with camelCase keys ready for JSON serialization
        """
        payload = {
            "type": "error",
            "error_code": error_code,
            "error_message": error_message,
        }

        if details:
            payload["details"] = details

        return cls._dict_to_camel_case(payload)

    @classmethod
    def format_conversation_title(
        cls,
        title: str
    ) -> Dict[str, Any]:
        """
        Format conversation title for WebSocket transmission.

        Args:
            title: The generated conversation title

        Returns:
            Dictionary with camelCase keys ready for JSON serialization
            Matches consumers_backup.py line 383:
            {type: "conversation_title", title: "..."}
        """
        payload = {
            "type": "conversation_title",
            "title": title,
        }

        return cls._dict_to_camel_case(payload)

    @classmethod
    def format_progress_error(
        cls,
        message: str
    ) -> Dict[str, Any]:
        """
        Format progress error for WebSocket transmission.

        Args:
            message: Error message describing the progress error

        Returns:
            Dictionary ready for JSON serialization (no camelCase conversion)
            Matches consumers_backup.py lines 518-521, 597-600:
            {type: "progress_error", message: "..."}
        """
        # NOTE: This is NOT camelized - sent as-is to match backup behavior
        return {
            "type": "progress_error",
            "message": message,
        }

    @classmethod
    def format_conversation_history(
        cls,
        conversation_history: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Format conversation history for WebSocket transmission.

        Args:
            conversation_history: List of formatted message dictionaries

        Returns:
            Dictionary with camelCase keys ready for JSON serialization
            Frontend expects: {type: "conversation_history", conversationHistory: [...]}
        """
        payload = {
            "type": "conversation_history",
            "conversation_history": conversation_history,
        }

        return cls._dict_to_camel_case(payload)

    @classmethod
    def format_latest_progress(
        cls,
        conversation_id: str,
        assessment: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Format latest learning progress data for WebSocket transmission.

        Args:
            conversation_id: The conversation UUID as string
            assessment: The latest assessment data or None

        Returns:
            Dictionary with camelCase keys ready for JSON serialization
            Matches consumers_backup.py line 606-610:
            {type: "latest_progress", conversationId: "...", assessment: {...} | null}
        """
        payload = {
            "type": "latest_progress",
            "conversation_id": conversation_id,
            "assessment": assessment,
        }

        return cls._dict_to_camel_case(payload)

    # ========== Artifact Response Formatters ==========

    @classmethod
    def format_artifact_init(
        cls,
        artifact_id: str,
        title: str,
        outline: str,
        estimated_sections: int
    ) -> Dict[str, Any]:
        """
        Format artifact initialization message for WebSocket transmission.

        Sent when a new artifact is created and ready for section generation.

        Args:
            artifact_id: Unique identifier for the artifact
            title: Title of the artifact
            outline: Structured outline with section descriptions
            estimated_sections: Expected number of sections

        Returns:
            Dictionary ready for JSON serialization
        """
        return {
            "type": "artifact_init",
            "artifactId": artifact_id,
            "title": title,
            "outline": outline,
            "estimatedSections": estimated_sections,
        }

    @classmethod
    def format_artifact_stream(
        cls,
        artifact_id: str,
        chunk: str,
        section: int,
        progress: float
    ) -> Dict[str, Any]:
        """
        Format artifact content streaming chunk for WebSocket transmission.

        Sent during section-by-section content generation.

        Args:
            artifact_id: Unique identifier for the artifact
            chunk: Content chunk being streamed
            section: Current section number being generated
            progress: Generation progress (0.0 to 1.0)

        Returns:
            Dictionary ready for JSON serialization
        """
        return {
            "type": "artifact_stream",
            "artifactId": artifact_id,
            "chunk": chunk,
            "section": section,
            "progress": progress,
        }

    @classmethod
    def format_artifact_pause(
        cls,
        artifact_id: str,
        current_section: int,
        sections_remaining: int
    ) -> Dict[str, Any]:
        """
        Format artifact pause message for WebSocket transmission.

        Sent when artifact generation is paused awaiting user continuation.

        Args:
            artifact_id: Unique identifier for the artifact
            current_section: Last completed section number
            sections_remaining: Number of sections left to generate

        Returns:
            Dictionary ready for JSON serialization
        """
        return {
            "type": "artifact_pause",
            "artifactId": artifact_id,
            "currentSection": current_section,
            "sectionsRemaining": sections_remaining,
        }

    @classmethod
    def format_artifact_complete(
        cls,
        artifact_id: str,
        total_words: int
    ) -> Dict[str, Any]:
        """
        Format artifact completion message for WebSocket transmission.

        Sent when artifact generation is fully complete.

        Args:
            artifact_id: Unique identifier for the artifact
            total_words: Total word count of the generated artifact

        Returns:
            Dictionary ready for JSON serialization
        """
        return {
            "type": "artifact_complete",
            "artifactId": artifact_id,
            "totalWords": total_words,
        }
