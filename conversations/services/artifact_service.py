"""
Artifact Service

Orchestrates the generation of long-form artifacts (documents, code, diagrams)
using LangGraph for robust checkpointing and pause/resume capability.

This service provides the public interface while delegating to LangGraph
for state management and checkpointing.
"""

import logging
import uuid
from typing import AsyncGenerator, Tuple, Dict, Any, Optional, Callable

from channels.db import database_sync_to_async

from conversations.models import Artifact, ArtifactCheckpoint, Conversation, Message, LLM
from conversations.constants import (
    ArtifactStatus,
    ArtifactType,
    DEFAULT_ARTIFACT_SECTIONS_PER_ITERATION,
    DEFAULT_ARTIFACT_MAX_ITERATIONS,
    ErrorCode,
    ErrorMessage,
)
from conversations.services.artifact_graph import (
    ArtifactState,
)
from conversations.services.artifact_graph.graph import (
    ArtifactMode,
    run_artifact_workflow,
)

logger = logging.getLogger(__name__)


class ArtifactService:
    """
    Service for orchestrating artifact generation.
    
    Uses LangGraph under the hood for:
    - Automatic state checkpointing at each node transition
    - Crash recovery from exact failure point
    - Robust pause/resume functionality
    - Postgres-backed state persistence
    """

    def __init__(
        self,
        conversation: Conversation,
        user=None,
        send_callback: Optional[Callable] = None,
    ):
        """
        Initialize the artifact service.

        Args:
            conversation: The conversation this artifact belongs to
            user: User object (None for public bots)
            send_callback: Async callback for sending WebSocket messages
        """
        self.conversation = conversation
        self.user = user
        self.send_callback = send_callback

    async def send(self, data: Dict[str, Any]):
        """Send data through callback if available."""
        if self.send_callback:
            await self.send_callback(data)

    async def execute(
        self,
        message: str,
        llm: LLM,
        message_obj: Message,
        artifact_id: Optional[str] = None,
        is_modification: bool = False,
        target_artifact_id: Optional[str] = None,
        artifact_context: Optional[Dict[str, Any]] = None,  # Context for RAG, files, etc.
    ) -> AsyncGenerator[Tuple[str, Optional[Dict]], None]:
        """
        Execute artifact generation or modification flow.

        This is the main entry point that delegates to LangGraph.

        Args:
            message: User's message/request
            llm: LLM to use for generation
            message_obj: The AI message object to associate with artifact
            artifact_id: Optional existing artifact ID for continuation
            is_modification: True if modifying existing artifact (append sections)
            target_artifact_id: ID of artifact to modify (for modification mode)

        Yields:
            Tuple of (chunk: str, usage: Dict) for streaming responses
        """
        # Modification mode - append new sections to existing artifact
        if is_modification and target_artifact_id:
            async for chunk, usage in self._modify_artifact(
                message=message,
                llm=llm,
                message_obj=message_obj,
                target_artifact_id=target_artifact_id,
                artifact_context=artifact_context,
            ):
                yield chunk, usage
            return

        # If artifact_id provided, this is a continuation (resume paused)
        if artifact_id:
            async for chunk, usage in self._continue_artifact(
                artifact_id=artifact_id,
                llm=llm,
                message_obj=message_obj,
                artifact_context=artifact_context,
            ):
                yield chunk, usage
            return

        # New artifact generation
        async for chunk, usage in self._create_new_artifact(
            message=message,
            llm=llm,
            message_obj=message_obj,
            artifact_context=artifact_context,
        ):
            yield chunk, usage

    async def _create_new_artifact(
        self,
        message: str,
        llm: LLM,
        message_obj: Message,
        artifact_context: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[Tuple[str, Optional[Dict]], None]:
        """
        Create a new artifact using LangGraph workflow.

        Args:
            message: User's request
            llm: LLM to use
            message_obj: AI message object

        Yields:
            Tuple of (chunk: str, usage: Dict)
        """
        # Generate unique thread ID for this artifact generation
        thread_id = f"artifact_{self.conversation.conversation_id}_{uuid.uuid4().hex[:8]}"
        
        user_id = self.user.id if self.user else None
        
        logger.info(f"Starting new artifact generation: thread={thread_id}")
        
        # Get message ID for linking artifact to message
        message_id = message_obj.id if message_obj else None

        try:
            async for chunk, metadata in run_artifact_workflow(
                mode=ArtifactMode.CREATE,
                conversation_id=self.conversation.conversation_id,
                user_message=message,
                llm_id=llm.id,
                llm_provider=llm.provider,
                thread_id=thread_id,
                user_id=user_id,
                message_id=message_id,
                send_callback=self.send_callback,
                artifact_context=artifact_context,
            ):
                yield chunk, metadata
                
        except Exception as e:
            logger.exception(f"Error in artifact generation: {str(e)}")
            await self.send({
                "type": "error",
                "errorCode": ErrorCode.ARTIFACT_ERROR,
                "errorMessage": str(e),
            })
            yield f"Error: {str(e)}", {"error": str(e)}

    async def _continue_artifact(
        self,
        artifact_id: str,
        llm: LLM,
        message_obj: Message,
        artifact_context: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[Tuple[str, Optional[Dict]], None]:
        """
        Continue a paused artifact.

        Args:
            artifact_id: ID of the artifact to continue
            llm: LLM to use
            message_obj: AI message object

        Yields:
            Tuple of (chunk: str, usage: Dict)
        """
        # Get artifact from database
        try:
            artifact = await self._get_artifact(int(artifact_id))
        except (ValueError, Artifact.DoesNotExist):
            error_msg = ErrorMessage.ARTIFACT_NOT_FOUND
            await self.send({
                "type": "error",
                "errorCode": ErrorCode.ARTIFACT_NOT_FOUND,
                "errorMessage": error_msg,
            })
            yield error_msg, {"error": error_msg}
            return

        # Check if artifact can be continued
        if artifact.status == ArtifactStatus.COMPLETED:
            error_msg = ErrorMessage.ARTIFACT_ALREADY_COMPLETE
            await self.send({
                "type": "error",
                "errorCode": ErrorCode.ARTIFACT_ALREADY_COMPLETE,
                "errorMessage": error_msg,
            })
            yield error_msg, {"error": error_msg}
            return

        # Get thread_id from latest checkpoint
        checkpoint = await self._get_latest_checkpoint(artifact)
        thread_id = checkpoint.state_data.get("thread_id") if checkpoint else None
        
        if not thread_id:
            # Generate new thread ID if no checkpoint
            thread_id = f"artifact_{self.conversation.conversation_id}_{uuid.uuid4().hex[:8]}"
        
        user_id = self.user.id if self.user else None
        
        logger.info(f"Resuming artifact {artifact_id}: thread={thread_id}")
        
        try:
            async for chunk, metadata in run_artifact_workflow(
                mode=ArtifactMode.RESUME,
                conversation_id=self.conversation.conversation_id,
                llm_id=llm.id,
                llm_provider=llm.provider,
                thread_id=thread_id,
                user_id=user_id,
                send_callback=self.send_callback,
                # RESUME-specific params
                artifact_id=artifact.id,
                content=artifact.content,
                current_section=artifact.current_section,
                estimated_sections=artifact.estimated_sections,
                iteration_count=checkpoint.iteration_count if checkpoint else 0,
                title=artifact.title,
                outline=artifact.outline,
                artifact_type=artifact.artifact_type,
                language=artifact.language,
                artifact_context=artifact_context,
            ):
                yield chunk, metadata
                
        except Exception as e:
            logger.exception(f"Error resuming artifact: {str(e)}")
            await self.send({
                "type": "error",
                "errorCode": ErrorCode.ARTIFACT_ERROR,
                "errorMessage": str(e),
            })
            yield f"Error: {str(e)}", {"error": str(e)}

    async def _modify_artifact(
        self,
        message: str,
        llm: LLM,
        message_obj: Message,
        target_artifact_id: str,
        artifact_context: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[Tuple[str, Optional[Dict]], None]:
        """
        Modify an existing artifact by appending new sections.

        Args:
            message: User's modification request
            llm: LLM to use
            message_obj: AI message object
            target_artifact_id: ID of the artifact to modify

        Yields:
            Tuple of (chunk: str, usage: Dict)
        """
        # Get artifact from database
        try:
            artifact = await self._get_artifact(int(target_artifact_id))
        except (ValueError, Artifact.DoesNotExist):
            error_msg = ErrorMessage.ARTIFACT_NOT_FOUND
            await self.send({
                "type": "error",
                "errorCode": ErrorCode.ARTIFACT_NOT_FOUND,
                "errorMessage": error_msg,
            })
            yield error_msg, {"error": error_msg}
            return

        # Check if artifact can be modified (must be completed or paused)
        if artifact.status not in [ArtifactStatus.COMPLETED, ArtifactStatus.PAUSED]:
            error_msg = "Cannot modify artifact that is currently being generated"
            await self.send({
                "type": "error",
                "errorCode": ErrorCode.ARTIFACT_ERROR,
                "errorMessage": error_msg,
            })
            yield error_msg, {"error": error_msg}
            return

        # Generate unique thread ID for this modification
        thread_id = f"artifact_mod_{self.conversation.conversation_id}_{uuid.uuid4().hex[:8]}"

        user_id = self.user.id if self.user else None
        message_id = message_obj.id if message_obj else None

        logger.info(
            f"Starting artifact modification: artifact={target_artifact_id}, "
            f"thread={thread_id}, current_sections={artifact.current_section}"
        )

        try:
            async for chunk, metadata in run_artifact_workflow(
                mode=ArtifactMode.MODIFY,
                conversation_id=self.conversation.conversation_id,
                llm_id=llm.id,
                llm_provider=llm.provider,
                thread_id=thread_id,
                user_id=user_id,
                message_id=message_id,
                send_callback=self.send_callback,
                user_message=message,
                # MODIFY-specific params
                artifact_id=artifact.id,
                title=artifact.title,
                artifact_type=artifact.artifact_type,
                language=artifact.language,
                original_outline=artifact.outline,
                original_content=artifact.content,
                original_sections=artifact.current_section,
                version=artifact.version,
                artifact_context=artifact_context,
            ):
                yield chunk, metadata

        except Exception as e:
            logger.exception(f"Error modifying artifact: {str(e)}")
            await self.send({
                "type": "error",
                "errorCode": ErrorCode.ARTIFACT_ERROR,
                "errorMessage": str(e),
            })
            yield f"Error: {str(e)}", {"error": str(e)}

    # ========== Database Helpers ==========

    @database_sync_to_async
    def _get_artifact(self, artifact_id: int) -> Artifact:
        """Get artifact from database."""
        return Artifact.active_objects.get(id=artifact_id)

    @database_sync_to_async
    def _get_latest_checkpoint(self, artifact: Artifact) -> Optional[ArtifactCheckpoint]:
        """Get the latest checkpoint for an artifact."""
        return artifact.checkpoints.order_by('-created_at').first()

    async def get_active_artifact(self) -> Optional[Artifact]:
        """Get active (paused or generating) artifact for conversation."""
        return await self._get_active_artifact_db()

    @database_sync_to_async
    def _get_active_artifact_db(self) -> Optional[Artifact]:
        """Get active artifact from database."""
        return Artifact.active_objects.filter(
            conversation=self.conversation,
            status__in=[ArtifactStatus.PAUSED, ArtifactStatus.GENERATING]
        ).first()

