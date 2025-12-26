"""
Artifact Coordinator Service

Coordinates artifact generation/modification for WebSocket consumers.
Extracted from MessageCoordinator for single responsibility.

Handles:
- Artifact creation flow
- Artifact modification flow (new version creation)
- Artifact continuation (resume paused)
- Artifact pause handling
- Message finalization for artifacts
"""

import logging
from typing import Optional, Dict, Any, Callable

from channels.db import database_sync_to_async

from conversations.models import Conversation, Message, LLM, Artifact
from conversations.constants import (
    ErrorCode,
    ErrorMessage,
    ArtifactStatus,
)
from core.services.billing_service import BillingService
from core.services.dtos.artifact_dto import build_artifact_context
from conversations.services.websocket_response_service import WebSocketResponseService
from conversations.services.artifact_service import ArtifactService
from conversations.services.artifact_intent_detector import ArtifactIntentDetector
from conversations.services.artifact_graph.db_helpers import get_artifact, get_latest_version_in_group

logger = logging.getLogger(__name__)


class ArtifactCoordinator:
    """
    Coordinates artifact generation/modification for WebSocket consumers.
    
    This class handles all artifact-related operations including:
    - Creating new artifacts
    - Modifying existing artifacts (creates new version)
    - Resuming paused artifacts
    - Pausing artifact generation
    - Finalizing artifact messages
    """

    def __init__(
        self,
        conversation: Conversation,
        user=None,  # Can be None for public bots
        send_callback: Optional[Callable] = None,
    ):
        """
        Initialize the artifact coordinator.

        Args:
            conversation: The conversation instance
            user: User object (None for public bots)
            send_callback: Async callback for sending WebSocket messages
        """
        self.conversation = conversation
        self.user = user
        self.send_callback = send_callback
        self.billing_service = BillingService()

    async def send(self, data: Dict[str, Any]):
        """Send data through WebSocket if callback is available."""
        if self.send_callback:
            try:
                await self.send_callback(data)
            except Exception as e:
                logger.debug(f"Failed to send WebSocket message: {type(e).__name__}")

    async def send_error(self, error_code: str, error_message: str, details: Optional[Dict] = None):
        """Send error response through WebSocket."""
        error_payload = WebSocketResponseService.format_error(error_code, error_message, details)
        await self.send(error_payload)

    async def stream_artifact_response(
        self,
        message_data: Dict[str, Any],
        message_obj: Message,
        llm: LLM,
        artifact_id: Optional[str] = None,
    ):
        """
        Stream artifact generation response.

        Uses an event-driven approach to allow pause requests to be processed
        during generation.

        Supports three modes:
        1. Create new artifact (default)
        2. Continue paused artifact (artifact_id provided)
        3. Modify existing artifact (artifact_action=modify or auto-detected)

        Args:
            message_data: Validated message data
            message_obj: AI message object
            llm: LLM instance to use
            artifact_id: Optional existing artifact ID for continuation
        """
        # Get artifact action parameters from message data
        artifact_action = message_data.get("artifact_action", "auto")
        active_artifact_id = message_data.get("active_artifact_id")
        target_artifact_id = message_data.get("target_artifact_id")

        # Resolve action using heuristics if "auto"
        resolved_action = artifact_action
        if artifact_action == "auto" and active_artifact_id:
            resolved_action = ArtifactIntentDetector.detect_intent(
                message=message_data.get("message", ""),
                has_active_artifact=True,
            )
            # Use active artifact as target if not explicitly set
            if resolved_action in ("modify", "rewrite"):
                target_artifact_id = target_artifact_id or active_artifact_id
            logger.info(f"Artifact intent detection: action={artifact_action} -> resolved={resolved_action}")

        # Route to appropriate flow
        if resolved_action == "rewrite" and target_artifact_id:
            # Section rewrite - regenerate specific section(s)
            target_artifact_id = await self._resolve_to_latest_version(target_artifact_id)
            logger.info(f"Resolved target artifact to latest version for rewrite: {target_artifact_id}")
            await self._run_artifact_section_rewrite(message_data, message_obj, llm, target_artifact_id)
        elif resolved_action == "modify" and target_artifact_id:
            # CRITICAL: Always resolve to the latest version in the artifact group
            # This fixes the bug where clicking an old version in UI caused branching
            target_artifact_id = await self._resolve_to_latest_version(target_artifact_id)
            logger.info(f"Resolved target artifact to latest version: {target_artifact_id}")
            await self._run_artifact_modification(message_data, message_obj, llm, target_artifact_id)
        else:
            # Existing create/continue flow
            await self._run_artifact_generation(message_data, message_obj, llm, artifact_id)

    async def _run_artifact_generation(
        self,
        message_data: Dict[str, Any],
        message_obj: Message,
        llm: LLM,
        artifact_id: Optional[str] = None,
    ):
        """
        Execute the artifact generation logic.

        The pause mechanism works by:
        1. User clicks pause -> handle_pause_artifact updates DB status to PAUSED
        2. The asyncio.sleep(0) in graph.py yields control to process pause request
        3. check_artifact_paused in nodes.py reads the PAUSED status from DB
        4. Generation stops at the next section boundary
        """
        try:
            # Create artifact service with WebSocket callback
            artifact_service = ArtifactService(
                conversation=self.conversation,
                user=self.user,
                send_callback=self._artifact_send_callback,
            )

            token_usage = None
            generated_artifact_id = artifact_id

            # Build artifact context from message_data for RAG, files, etc.
            artifact_context = build_artifact_context(
                file_ids=message_data.get("file_ids"),
                embedding_ids=message_data.get("embedding_ids"),
                tag_ids=message_data.get("tag_ids"),
                folder_ids=message_data.get("folder_ids"),
                media_ids=message_data.get("media_ids"),
                system_prompt=message_data.get("system_prompt"),
                max_context_snippets=message_data.get("max_context_snippets", 4),
            )
            artifact_context_dict = artifact_context.to_dict() if artifact_context.has_rag_context() or artifact_context.has_system_prompt() else None

            # Execute artifact generation
            # Note: Content is stored in the Artifact model, NOT in the message
            # The message just gets linked to the artifact via artifact_id
            async for chunk, usage in artifact_service.execute(
                message=message_data["message"],
                llm=llm,
                message_obj=message_obj,
                artifact_id=artifact_id,
                artifact_context=artifact_context_dict,
            ):
                if usage:
                    token_usage = usage

                    # Track the artifact_id from metadata
                    if usage.get("type") == "artifact_init":
                        generated_artifact_id = str(usage.get("artifact_id"))
                        logger.info(f"Artifact generation started for artifact_id={generated_artifact_id}")

                    # Check billing during streaming (authenticated users only)
                    if self.user:
                        can_continue, error_response = await self.billing_service.check_streaming_credit_usage(
                            self.user, llm, token_usage
                        )
                        if not can_continue:
                            # For artifacts, just pause instead of failing
                            if generated_artifact_id:
                                await self._pause_artifact_internal(generated_artifact_id)
                            return

            # Finalize message - for artifacts, message content stays empty
            # but we link it to the artifact
            await self._finalize_artifact_message(
                message_obj=message_obj,
                artifact_id=generated_artifact_id or artifact_id,
                token_usage=token_usage,
            )

        except Exception as e:
            logger.exception(f"Error streaming artifact response: {str(e)}")
            await self.send_error(ErrorCode.ARTIFACT_ERROR, ErrorMessage.ARTIFACT_ERROR)

    async def _run_artifact_modification(
        self,
        message_data: Dict[str, Any],
        message_obj: Message,
        llm: LLM,
        target_artifact_id: str,
    ):
        """
        Execute artifact modification logic (append new sections).

        Args:
            message_data: Validated message data
            message_obj: AI message object
            llm: LLM instance to use
            target_artifact_id: ID of the artifact to modify (parent)
        """
        try:
            # Create artifact service with WebSocket callback
            artifact_service = ArtifactService(
                conversation=self.conversation,
                user=self.user,
                send_callback=self._artifact_send_callback,
            )

            token_usage = None
            # Track the NEW artifact ID (not the parent!)
            # This will be set by artifact_modify_init event
            new_artifact_id = None

            logger.info(f"Starting artifact modification for artifact_id={target_artifact_id}")

            # Build artifact context from message_data for RAG, files, etc.
            artifact_context = build_artifact_context(
                file_ids=message_data.get("file_ids"),
                embedding_ids=message_data.get("embedding_ids"),
                tag_ids=message_data.get("tag_ids"),
                folder_ids=message_data.get("folder_ids"),
                media_ids=message_data.get("media_ids"),
                system_prompt=message_data.get("system_prompt"),
                max_context_snippets=message_data.get("max_context_snippets", 4),
            )
            artifact_context_dict = artifact_context.to_dict() if artifact_context.has_rag_context() or artifact_context.has_system_prompt() else None

            # Execute artifact modification
            async for chunk, usage in artifact_service.execute(
                message=message_data["message"],
                llm=llm,
                message_obj=message_obj,
                is_modification=True,
                target_artifact_id=target_artifact_id,
                artifact_context=artifact_context_dict,
            ):
                if usage:
                    token_usage = usage

                    # Track the NEW artifact ID from modification init
                    if usage.get("type") == "artifact_modify_init":
                        new_artifact_id = str(usage.get("artifact_id"))
                        logger.info(
                            f"Artifact modification started: NEW artifact_id={new_artifact_id}, "
                            f"parent={target_artifact_id}, version={usage.get('version')}"
                        )

                    # Check billing during streaming (authenticated users only)
                    if self.user:
                        can_continue, error_response = await self.billing_service.check_streaming_credit_usage(
                            self.user, llm, token_usage
                        )
                        if not can_continue:
                            # Pause the NEW artifact if out of credits
                            await self._pause_artifact_internal(new_artifact_id or target_artifact_id)
                            return

            # Finalize message with the NEW artifact ID (not parent!)
            # This is critical: we must link message to the NEW artifact, not the parent
            await self._finalize_artifact_message(
                message_obj=message_obj,
                artifact_id=new_artifact_id or target_artifact_id,
                token_usage=token_usage,
            )

        except Exception as e:
            logger.exception(f"Error modifying artifact: {str(e)}")
            await self.send_error(ErrorCode.ARTIFACT_ERROR, f"Error modifying artifact: {str(e)}")

    async def _run_artifact_section_rewrite(
        self,
        message_data: Dict[str, Any],
        message_obj: Message,
        llm: LLM,
        target_artifact_id: str,
    ):
        """
        Execute artifact section rewrite logic.
        
        Instead of appending new sections, this rewrites a specific section
        in place while keeping all other sections unchanged.

        Args:
            message_data: Validated message data
            message_obj: AI message object  
            llm: LLM instance to use
            target_artifact_id: ID of the artifact to rewrite section in
        """
        from core.prompts.artifact_prompts import (
            get_section_rewrite_prompt,
            parse_sections_from_content,
            reconstruct_content_with_new_section,
            extract_target_section_from_message,
        )
        from conversations.services.artifact_graph.ai_services import get_ai_service
        
        try:
            # Get the artifact
            artifact = await get_artifact(int(target_artifact_id))
            content = await database_sync_to_async(lambda: artifact.content)()
            title = await database_sync_to_async(lambda: artifact.title)()
            artifact_type = await database_sync_to_async(lambda: artifact.artifact_type)()
            
            # Parse content into sections
            sections = parse_sections_from_content(content)
            if not sections:
                logger.warning(f"No sections found in artifact {target_artifact_id}, falling back to modify")
                await self._run_artifact_modification(message_data, message_obj, llm, target_artifact_id)
                return
            
            # Determine which section to rewrite
            user_message = message_data.get("message", "")
            target_section_num = extract_target_section_from_message(user_message, len(sections))
            
            if not target_section_num or target_section_num > len(sections):
                logger.warning(f"Could not determine target section, falling back to modify")
                await self._run_artifact_modification(message_data, message_obj, llm, target_artifact_id)
                return
            
            target_section = sections[target_section_num - 1]
            
            # Build context summary of other sections
            other_sections_summary = "\n".join([
                f"- Section {s['number']}: {s['title']}"
                for s in sections if s['number'] != target_section_num
            ])
            
            # Build the rewrite prompt
            system_prompt = get_section_rewrite_prompt(
                title=title,
                artifact_type=artifact_type,
                total_sections=len(sections),
                target_section_number=target_section_num,
                target_section_title=target_section['title'],
                original_section_content=target_section['content'],
                other_sections_summary=other_sections_summary,
                user_message=user_message,
            )
            
            logger.info(
                f"Starting section rewrite: artifact={target_artifact_id}, "
                f"section={target_section_num} '{target_section['title']}'"
            )
            
            # Send rewrite_init notification to frontend
            await self.send({
                "type": "artifact_rewrite_init",
                "artifactId": target_artifact_id,
                "targetSection": target_section_num,
                "targetSectionTitle": target_section['title'],
            })
            
            # Call LLM to generate new section content
            ai_service = await get_ai_service(llm, self.user)
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ]
            
            new_section_content = ""
            async for chunk, usage in ai_service.stream_chat_completion(
                messages=messages,
                max_tokens=2000,
                temperature=0.7,
            ):
                if chunk:
                    new_section_content += chunk
                    # Stream progress to frontend
                    await self.send({
                        "type": "artifact_rewrite_stream",
                        "artifactId": target_artifact_id,
                        "chunk": chunk,
                        "targetSection": target_section_num,
                    })
            
            # Reconstruct full content with new section
            new_content = reconstruct_content_with_new_section(
                content, sections, target_section_num, new_section_content
            )
            
            # Create new version with rewritten content
            new_artifact = await database_sync_to_async(artifact.create_new_version)()
            new_artifact.content = new_content
            new_artifact.message = message_obj
            new_artifact.status = ArtifactStatus.COMPLETED
            await database_sync_to_async(new_artifact.save)()
            
            new_version = await database_sync_to_async(lambda: new_artifact.version)()
            new_artifact_id = await database_sync_to_async(lambda: new_artifact.id)()
            
            logger.info(
                f"Section rewrite complete: new artifact {new_artifact_id} v{new_version}, "
                f"rewrote section {target_section_num}"
            )
            
            # Get additional data for frontend
            outline = await database_sync_to_async(lambda: new_artifact.outline)()
            estimated_sections = await database_sync_to_async(lambda: new_artifact.estimated_sections)()
            current_section = await database_sync_to_async(lambda: new_artifact.current_section)()
            artifact_group_id = await database_sync_to_async(lambda: new_artifact.artifact_group_id)()
            
            # Send completion notification with full artifact data
            await self.send({
                "type": "artifact_rewrite_complete",
                "artifactId": str(new_artifact_id),
                "parentArtifactId": target_artifact_id,
                "artifactGroupId": artifact_group_id,
                "version": new_version,
                "targetSection": target_section_num,
                "content": new_content,
                "title": title,
                "outline": outline,
                "artifactType": artifact_type,
                "estimatedSections": estimated_sections,
                "currentSection": current_section,
            })
            
            # Finalize the message
            await self._finalize_artifact_message(
                message_obj=message_obj,
                artifact_id=str(new_artifact_id),
                token_usage=None,  # TODO: Track token usage for rewrite
            )

        except Exception as e:
            logger.exception(f"Error rewriting artifact section: {str(e)}")
            await self.send_error(ErrorCode.ARTIFACT_ERROR, f"Error rewriting section: {str(e)}")

    async def _finalize_artifact_message(
        self,
        message_obj: Message,
        artifact_id: Optional[str],
        token_usage: Optional[Dict],
    ):
        """
        Finalize an artifact-linked message.

        Unlike regular messages, artifact messages don't store the content
        directly - they just link to the artifact.

        Note: The artifact is already linked to the message in plan_node,
        so we just verify and update the message content here.

        Args:
            message_obj: The AI message object
            artifact_id: ID of the linked artifact
            token_usage: Token usage data
        """
        try:
            # Link message to artifact if we have an artifact_id
            if artifact_id:
                # Get the artifact fresh from database to ensure we have latest title
                def _get_artifact():
                    return Artifact.active_objects.get(id=int(artifact_id))

                artifact = await database_sync_to_async(_get_artifact)()

                # Force refresh from DB to get absolute latest data
                await database_sync_to_async(artifact.refresh_from_db)()

                # Verify artifact is linked to message (should already be done in plan_node)
                # Only update if not already linked (defensive check)
                if artifact.message_id != message_obj.id:
                    logger.warning(f"Artifact {artifact_id} not linked to message {message_obj.id}, linking now")
                    artifact.message = message_obj
                    await database_sync_to_async(artifact.save)(update_fields=['message', 'updated_at'])

                # Use the artifact title - should be set by plan_node
                artifact_title = artifact.title or "Untitled"
                message_obj.message = f"Generated artifact: {artifact_title}"

                logger.info(f"Finalizing artifact message: artifact_id={artifact_id}, title={artifact_title}")

            # Save the message
            await database_sync_to_async(message_obj.save)()

            # Refresh message from DB to ensure artifacts relation is up-to-date
            await database_sync_to_async(message_obj.refresh_from_db)()

            # Process billing
            if token_usage and self.user:
                llm = await database_sync_to_async(lambda: message_obj.llm)()
                await self.billing_service.process_message_cost(
                    user=self.user,
                    llm=llm,
                    message_obj=message_obj,
                    token_usage=token_usage,
                )

            # Send final message to frontend
            final_payload = await WebSocketResponseService.format_message(
                message=message_obj,
                message_type="message",
                is_sender=False,
                streaming=False,
                regenerate=False,
            )

            # Debug log to track artifactId in final payload
            logger.info(
                f"Finalize artifact message: message_id={message_obj.id}, "
                f"artifact_id param={artifact_id}, "
                f"payload artifactId={final_payload.get('artifactId')}"
            )

            await self.send(final_payload)

        except Exception as e:
            logger.exception(f"Error finalizing artifact message: {str(e)}")

    async def _artifact_send_callback(self, data: Dict[str, Any]):
        """
        Callback for ArtifactService to send WebSocket messages.

        Args:
            data: Data dictionary to send (already formatted)
        """
        await self.send(data)

    async def _resolve_to_latest_version(self, artifact_id: str) -> str:
        """
        Resolve an artifact ID to the latest version in its artifact group.
        
        This ensures that modifications always build on the latest version,
        even if the user is viewing an older version in the sidecar.
        
        Args:
            artifact_id: The artifact ID from the frontend (may be any version)
            
        Returns:
            The ID of the latest version in the artifact's group,
            or the original ID if no group exists
        """
        try:
            # Get the artifact to find its group
            artifact = await get_artifact(int(artifact_id))
            artifact_group_id = await database_sync_to_async(lambda: artifact.artifact_group_id)()
            
            if artifact_group_id:
                # Get the latest version in the group
                latest = await get_latest_version_in_group(artifact_group_id)
                if latest:
                    latest_id = await database_sync_to_async(lambda: latest.id)()
                    if str(latest_id) != artifact_id:
                        logger.info(
                            f"Resolved artifact {artifact_id} to latest version {latest_id} "
                            f"in group {artifact_group_id}"
                        )
                    return str(latest_id)
            
            # No group or no latest version found, use original
            return artifact_id
            
        except Exception as e:
            logger.warning(f"Failed to resolve latest version for artifact {artifact_id}: {e}")
            return artifact_id


    async def handle_continue_artifact(
        self,
        message_data: Dict[str, Any],
        llm: LLM,
    ) -> Optional[Message]:
        """
        Handle continuation of a paused artifact.

        Args:
            message_data: Validated message data with artifact_id
            llm: LLM instance to use

        Returns:
            The AI message object if successful, None otherwise
        """
        try:
            artifact_id = message_data.get("artifact_id")
            if not artifact_id:
                await self.send_error(ErrorCode.MISSING_DATA, ErrorMessage.MISSING_ARTIFACT_ID)
                return None

            # Get the artifact and its linked message
            artifact = await database_sync_to_async(
                Artifact.active_objects.get
            )(id=int(artifact_id))

            # Use the existing message linked to the artifact (don't create a new one)
            ai_message = await database_sync_to_async(lambda: artifact.message)()

            if not ai_message:
                # If no message is linked, something is wrong
                await self.send_error(ErrorCode.ARTIFACT_ERROR, "Artifact has no linked message")
                return None

            # Check billing if user exists
            if self.user:
                has_credits = await self.billing_service.check_sufficient_credits(
                    self.user, llm
                )
                if not has_credits:
                    await self.send_error(ErrorCode.INSUFFICIENT_CREDITS, ErrorMessage.INSUFFICIENT_CREDITS)
                    return None

            # Update artifact status to generating
            artifact.status = ArtifactStatus.GENERATING
            await database_sync_to_async(artifact.save)(update_fields=['status', 'updated_at'])

            # Send artifact resume notification to frontend
            await self.send({
                "type": "artifact_resume",
                "artifactId": str(artifact_id),
                "messageId": str(ai_message.id),
                "currentSection": artifact.current_section,
                "estimatedSections": artifact.estimated_sections,
            })

            # Continue artifact generation (reuse existing message)
            await self.stream_artifact_response(
                message_data=message_data,
                message_obj=ai_message,
                llm=llm,
                artifact_id=artifact_id,
            )

            return ai_message

        except Exception as e:
            logger.exception(f"Error continuing artifact: {str(e)}")
            await self.send_error(ErrorCode.ARTIFACT_ERROR, ErrorMessage.ARTIFACT_ERROR)
            return None

    async def handle_pause_artifact(self, artifact_id: str):
        """
        Handle request to pause an artifact generation.

        Updates the artifact status in the database. The generation loop will
        check this status via check_artifact_paused() and stop at the next
        section boundary or during streaming.

        Args:
            artifact_id: ID of the artifact to pause
        """
        try:
            logger.info(f"ArtifactCoordinator: Starting pause for artifact_id={artifact_id}")

            # Update artifact status in database
            # The generation loop will check this status via check_artifact_paused()
            # and stop at the next chunk check interval or section boundary
            await self._pause_artifact_internal(artifact_id)

        except Exception as e:
            # Only log, don't try to send error (client may have disconnected)
            logger.warning(f"Error pausing artifact {artifact_id}: {type(e).__name__}: {str(e)}")

    async def _pause_artifact_internal(self, artifact_id: str):
        """
        Internal method to update artifact status to paused and notify frontend.

        Args:
            artifact_id: ID of the artifact to pause
        """
        artifact = await database_sync_to_async(
            Artifact.active_objects.get
        )(id=int(artifact_id))

        logger.info(f"ArtifactCoordinator: Found artifact {artifact_id}, current status={artifact.status}")

        # Only update if not already paused or completed
        if artifact.status not in [ArtifactStatus.PAUSED, ArtifactStatus.COMPLETED]:
            artifact.status = ArtifactStatus.PAUSED
            await database_sync_to_async(artifact.save)(update_fields=['status', 'updated_at'])
            logger.info(f"ArtifactCoordinator: Updated artifact {artifact_id} status to PAUSED in database")

        # Try to send pause confirmation to frontend (may fail if disconnected)
        await self.send({
            "type": "artifact_pause",
            "artifactId": artifact_id,
            "currentSection": artifact.current_section,
            "sectionsRemaining": artifact.estimated_sections - artifact.current_section,
        })

        logger.info(f"Artifact {artifact_id} paused at section {artifact.current_section}")
