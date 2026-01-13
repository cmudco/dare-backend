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
        generated_image: Optional[Dict] = None,
        generated_transcription: Optional[Dict] = None
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
            generated_transcription: Optional audio transcription data

        Returns:
            Dictionary with camelCase keys ready for JSON serialization
        """
        # Use MessageSerializer for proper formatting
        @database_sync_to_async
        def serialize_message():
            msg = Message.active_objects.prefetch_related(
                'files', 'tags', 'snippets__file', 'web_search_sources'
            ).get(id=message.id)
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
            "id": message.id,  # Keep as integer for consistency with conversation_history
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
            "webSearchSources": serialized_data.get("web_search_sources", []),
            "feedbackType": message.feedback_type,
            "feedbackText": message.feedback_text,
            "isEdited": message.is_edited,
            "isRegenerated": message.is_regenerated,
            "originalMessage": message.original_message,
            "cost": str(cost) if cost is not None else None,
            "inputTokens": input_tokens,
            "outputTokens": output_tokens,
            "learningProgressData": learning_progress_data or {},
            "generatedImage": generated_image,
            "generatedTranscription": generated_transcription
        }

        return cls._dict_to_camel_case(response)

    @classmethod
    def format_streaming_chunk(
        cls,
        message_id: int,
        chunk: str,
        is_complete: bool = False,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Format an AI streaming chunk for WebSocket transmission.

        Args:
            message_id: ID of the message being streamed (integer)
            chunk: The text chunk to send
            is_complete: Whether this is the final chunk
            metadata: Optional metadata (tokens, cost, etc.)

        Returns:
            Dictionary with camelCase keys ready for JSON serialization
        """
        payload = {
            "type": "ai_stream",
            "id": message_id,  # Keep as integer for consistency with conversation_history
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
        total_words: int,
        estimated_sections: int = 0
    ) -> Dict[str, Any]:
        """
        Format artifact completion message for WebSocket transmission.

        Sent when artifact generation is fully complete.

        Args:
            artifact_id: Unique identifier for the artifact
            total_words: Total word count of the generated artifact
            estimated_sections: Total number of sections (for frontend to mark all as complete)

        Returns:
            Dictionary ready for JSON serialization
        """
        return {
            "type": "artifact_complete",
            "artifactId": artifact_id,
            "totalWords": total_words,
            "estimatedSections": estimated_sections,
        }

    # ========== Workflow Execution Response Formatters ==========

    @classmethod
    def format_workflow_step_started(
        cls,
        node_id: str,
        step_number: int,
        node_type: str = "step"
    ) -> Dict[str, Any]:
        """
        Format workflow step started message for WebSocket transmission.

        Sent when a workflow node begins execution.

        Args:
            node_id: Unique identifier for the node
            step_number: Sequential step number in the workflow
            node_type: Type of node ('step', 'structuredOutput', etc.)

        Returns:
            Dictionary ready for JSON serialization
        """
        return {
            "type": "step_started",
            "nodeId": node_id,
            "stepNumber": step_number,
            "nodeType": node_type,
        }

    @classmethod
    def format_workflow_step_streaming(
        cls,
        node_id: str,
        chunk: str,
        accumulated_tokens: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Format workflow step streaming chunk for WebSocket transmission.

        Sent during LLM response streaming for real-time token display.

        Args:
            node_id: Unique identifier for the node
            chunk: The text chunk being streamed
            accumulated_tokens: Running count of output tokens (optional)

        Returns:
            Dictionary ready for JSON serialization
        """
        payload = {
            "type": "step_streaming",
            "nodeId": node_id,
            "chunk": chunk,
        }
        if accumulated_tokens is not None:
            payload["accumulatedTokens"] = accumulated_tokens
        return payload

    @classmethod
    def format_workflow_step_completed(
        cls,
        node_id: str,
        response: str,
        status: str,
        tokens: Optional[Dict[str, int]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Format workflow step completion message for WebSocket transmission.

        Sent when a workflow node finishes execution.

        Args:
            node_id: Unique identifier for the node
            response: The complete response from the node
            status: Execution status ('completed', 'failed', 'skipped')
            tokens: Token usage {'input': int, 'output': int}
            metadata: Additional metadata (routing decisions, snippets, etc.)

        Returns:
            Dictionary ready for JSON serialization
        """
        payload = {
            "type": "step_completed",
            "nodeId": node_id,
            "response": response,
            "status": status,
        }
        if tokens:
            payload["tokens"] = tokens
        if metadata:
            payload["metadata"] = metadata
        return payload

    @classmethod
    def format_workflow_execution_complete(
        cls,
        workflow_run_id: int,
        status: str,
        total_cost: Optional[float] = None,
        total_tokens: Optional[Dict[str, int]] = None,
        ended_at: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Format workflow execution complete message for WebSocket transmission.

        Sent when the entire workflow finishes execution.

        Args:
            workflow_run_id: ID of the workflow run
            status: Final execution status ('completed', 'failed', 'pending_human_input')
            total_cost: Total cost of the execution
            total_tokens: Total token usage {'input': int, 'output': int}
            ended_at: ISO timestamp of completion

        Returns:
            Dictionary ready for JSON serialization
        """
        payload = {
            "type": "execution_complete",
            "workflowRunId": workflow_run_id,
            "status": status,
        }
        if total_cost is not None:
            payload["totalCost"] = total_cost
        if total_tokens:
            payload["totalTokens"] = total_tokens
        if ended_at:
            payload["endedAt"] = ended_at
        return payload

    @classmethod
    def format_workflow_error(
        cls,
        node_id: Optional[str],
        error: str,
        error_type: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Format workflow error message for WebSocket transmission.

        Sent when a workflow step encounters an error.

        Args:
            node_id: ID of the node that errored (None for workflow-level errors)
            error: Error message
            error_type: Category of error (optional)

        Returns:
            Dictionary ready for JSON serialization
        """
        payload = {
            "type": "step_error",
            "error": error,
        }
        if node_id:
            payload["nodeId"] = node_id
        if error_type:
            payload["errorType"] = error_type
        return payload

    @classmethod
    def format_workflow_validation_required(
        cls,
        node_id: str,
        routes: List[Dict[str, Any]],
        context: Optional[Dict[str, Any]] = None,
        ai_recommendation: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Format human validation required message for WebSocket transmission.

        Sent when a routing node requires human decision.

        Args:
            node_id: ID of the routing node
            routes: List of available routes [{'name': str, 'description': str}, ...]
            context: Additional context about the decision
            ai_recommendation: AI's suggested route (optional)

        Returns:
            Dictionary ready for JSON serialization
        """
        payload = {
            "type": "validation_required",
            "nodeId": node_id,
            "routes": routes,
        }
        if context:
            payload["context"] = context
        if ai_recommendation:
            payload["aiRecommendation"] = ai_recommendation
        return payload

