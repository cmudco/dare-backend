"""
Message Coordinator Service

Orchestrates the complete message lifecycle for WebSocket conversations:
- Message creation and validation
- AI response streaming with billing
- Image generation
- Learning progress assessment
- Message finalization

This coordinator encapsulates the core business logic that was previously
duplicated across ChatConsumer and PublicBotConsumer.
"""

import logging
import json
import asyncio
from typing import Optional, Dict, Any, Callable
from decimal import Decimal
from channels.db import database_sync_to_async
from django.core.exceptions import ValidationError as DjangoValidationError
from djangorestframework_camel_case.util import camelize

from conversations.models import Conversation, Message, LLM, Artifact
from conversations.constants import (
    SenderType,
    DEFAULT_AI_SENDER_NAME,
    DEFAULT_CONVERSATION_TITLE,
    ErrorCode,
    ErrorMessage,
    ArtifactStatus,
)
from core.services.conversation_service import ConversationService
from core.services.llm_service import LLMService
from core.services.billing_service import BillingService
from core.services.learning_progress_service import LearningProgressService
from core.services.file_upload_service import FileUploadService
from core.services.dtos import LLMQueryRequestBuilder
from conversations.services.websocket_response_service import WebSocketResponseService
from conversations.services.message_validation_service import MessageValidationService
from conversations.services.image_generation_service import ImageGenerationService
from conversations.services.bot_budget_service import BotBudgetService
from conversations.services.artifact_service import ArtifactService
from users.utils import should_run_learning_progress

logger = logging.getLogger(__name__)


class MessageCoordinator:
    """Coordinates message handling logic for WebSocket consumers."""

    def __init__(
        self,
        conversation: Conversation,
        user=None,  # Can be None for public bots
        platform: str = "DARE",
        send_callback: Optional[Callable] = None,
    ):
        """
        Initialize the message coordinator.

        Args:
            conversation: The conversation instance
            user: User object (None for public bots)
            platform: Platform name ("DARE" or "SocraticBots")
            send_callback: Async callback for sending WebSocket messages
        """
        self.conversation = conversation
        self.user = user
        self.platform = platform
        self.send_callback = send_callback

        # Initialize services
        self.conversation_service = ConversationService()
        self.llm_service = LLMService()
        self.billing_service = BillingService()
        self.learning_progress_service = LearningProgressService()

        # Track active artifact generation tasks for cancellation
        self._artifact_tasks: Dict[str, asyncio.Task] = {}

    async def send(self, data: Dict[str, Any]):
        """Send data through WebSocket if callback is available."""
        if self.send_callback:
            try:
                await self.send_callback(json.dumps(camelize(data)))
            except Exception as e:
                # Log but don't raise - client may have disconnected
                logger.debug(f"Failed to send WebSocket message (client may have disconnected): {type(e).__name__}")

    async def send_error(self, error_code: str, error_message: str, details: Optional[Dict] = None):
        """Send error response through WebSocket."""
        error_payload = WebSocketResponseService.format_error(error_code, error_message, details)
        await self.send(error_payload)

    async def handle_new_message(
        self,
        message_data: Dict[str, Any],
        sender_name: str = None,
        llm_id: Optional[str] = None,
    ) -> Optional[Message]:
        """
        Handle creation of a new user message and generate AI response.

        Args:
            message_data: Validated message data dictionary
            sender_name: Name of the sender (user email or "Anonymous User")
            llm_id: Optional LLM ID override

        Returns:
            The AI message object if successful, None otherwise
        """
        try:
            # Get LLM
            llm = await self._get_llm(llm_id or message_data.get("llm_id"))
            if not llm:
                await self.send_error(ErrorCode.VALIDATION_ERROR, "Selected AI model not found")
                return None

            # Check billing if user exists
            if self.user:
                has_credits = await self.billing_service.check_sufficient_credits(
                    self.user, llm
                )
                if not has_credits:
                    await self.send_error(ErrorCode.INSUFFICIENT_CREDITS, ErrorMessage.INSUFFICIENT_CREDITS)
                    return None

            # Save attached images (base64) as File objects
            attached_image_ids = []
            images = message_data.get("images", [])
            if images:
                saved_files = await database_sync_to_async(
                    FileUploadService.save_base64_images
                )(
                    images=images,
                    user=self.user,
                    is_public=(self.user is None)
                )
                attached_image_ids = [f.id for f in saved_files]
                logger.info(f"Saved {len(attached_image_ids)} attached images for message")

            # Combine file_ids with attached image IDs
            all_file_ids = list(set(
                (message_data.get("file_ids") or []) + attached_image_ids
            ))

            # Create user message
            user_message = await self.conversation_service.create_message(
                conversation=self.conversation,
                sender_type=SenderType.PLAYER,
                message_content=message_data["message"],
                sender=sender_name,
                file_ids=all_file_ids,
                tag_ids=message_data.get("tag_ids"),
                embedding_ids=message_data.get("embedding_ids"),
                llm=llm,
            )

            # Send user message to client
            user_message_payload = await WebSocketResponseService.format_message(
                message=user_message,
                is_sender=True
            )
            await self.send(user_message_payload)

            # Create empty AI message
            ai_message = await self.conversation_service.create_message(
                conversation=self.conversation,
                sender_type=SenderType.AI_ASSISTANT,
                message_content="",
                sender=DEFAULT_AI_SENDER_NAME,
                llm=llm,
            )

            # Send empty AI message with streaming=True to show placeholder on frontend
            placeholder_payload = await WebSocketResponseService.format_message(
                message=ai_message,
                message_type="message",
                is_sender=False,
                streaming=True,
                regenerate=False
            )
            await self.send(placeholder_payload)

            # Generate conversation title if first message
            if await database_sync_to_async(
                lambda: self.conversation.messages.count()
            )() == 2:  # User + AI message
                asyncio.create_task(self._generate_conversation_title())

            # Stream AI response
            await self.stream_ai_response(
                message_data=message_data,
                message_obj=ai_message,
                llm=llm,
                regenerate=False,
            )

            return ai_message

        except Exception as e:
            logger.exception(f"Error handling new message: {str(e)}")
            await self.send_error(ErrorCode.PROCESSING_ERROR, ErrorMessage.PROCESSING_ERROR)
            return None

    async def handle_regenerate_response(
        self,
        message_data: Dict[str, Any],
        llm_id: Optional[str] = None,
    ) -> Optional[Message]:
        """
        Handle regeneration of an existing AI message.

        Args:
            message_data: Validated message data dictionary (must include message_id)
            llm_id: Optional LLM ID override

        Returns:
            The regenerated AI message object if successful, None otherwise
        """
        try:
            # Get message_id from message_data
            message_id = message_data.get("message_id")
            if not message_id:
                await self.send_error(ErrorCode.MISSING_DATA, ErrorMessage.MISSING_MESSAGE_ID)
                return None

            # Get the existing AI message to regenerate
            ai_message = await database_sync_to_async(
                lambda: Message.active_objects.select_related('llm').filter(
                    id=message_id, sender_type=SenderType.AI_ASSISTANT
                ).first()
            )()

            if not ai_message:
                await self.send_error(ErrorCode.INVALID_MESSAGE, ErrorMessage.INVALID_MESSAGE)
                return None

            # Get the preceding user message
            preceding_user_message = await self._get_preceding_user_message()
            if not preceding_user_message:
                await self.send_error(ErrorCode.NO_USER_MESSAGE, ErrorMessage.NO_USER_MESSAGE)
                return None

            # Get LLM (use provided or fallback to existing message's LLM)
            llm = await self._get_llm(
                llm_id or message_data.get("llm_id"),
                default=ai_message.llm
            )
            if not llm:
                await self.send_error(ErrorCode.VALIDATION_ERROR, "Selected AI model not found")
                return None

            # Check billing if user exists
            if self.user:
                has_credits = await self.billing_service.check_sufficient_credits(
                    self.user, llm
                )
                if not has_credits:
                    await self.send_error(ErrorCode.INSUFFICIENT_CREDITS, ErrorMessage.INSUFFICIENT_CREDITS)
                    return None

            # Use preceding user message content for regeneration
            regeneration_message_data = message_data.copy()
            regeneration_message_data["message"] = preceding_user_message.message

            # Stream AI response into the EXISTING message (don't create new one)
            await self.stream_ai_response(
                message_data=regeneration_message_data,
                message_obj=ai_message,  # Reuse existing message
                llm=llm,
                regenerate=True,
            )

            return ai_message

        except Exception as e:
            logger.exception(f"Error regenerating response: {str(e)}")
            await self.send_error(ErrorCode.REGENERATE_ERROR, ErrorMessage.REGENERATE_ERROR)
            return None

    async def stream_ai_response(
        self,
        message_data: Dict[str, Any],
        message_obj: Message,
        llm: LLM,
        regenerate: bool = False,
    ):
        """
        Stream AI response with billing checks.

        Supports both standard LLM streaming and artifact generation mode.
        When artifacts_enabled is True, delegates to ArtifactService.

        Args:
            message_data: Validated message data
            message_obj: Empty AI message object to populate
            llm: LLM instance to use
            regenerate: Whether this is a regeneration request
        """
        # Check if artifacts mode is enabled
        artifacts_enabled = message_data.get("artifacts_enabled", False)
        artifact_id = message_data.get("artifact_id")

        if artifacts_enabled and not regenerate:
            # Delegate to artifact service for long-form content generation
            await self._stream_artifact_response(
                message_data=message_data,
                message_obj=message_obj,
                llm=llm,
                artifact_id=artifact_id,
            )
            return

        try:
            bot_message_id = str(message_obj.id)
            ai_response_accumulator = ""
            token_usage = None
            generated_image_data = None

            # Build LLM query request using DTO builder
            request = LLMQueryRequestBuilder.from_message_data(
                message=message_data["message"],
                conversation=self.conversation,
                user=self.user,
                message_data=message_data,
                llm=llm,
                message_obj=message_obj,
                platform=self.platform,
            )

            # Stream from LLM service
            async for chunk, usage in self.llm_service.query(request):
                if usage:
                    token_usage = usage

                    # Handle generated image
                    if usage.get("image_bytes"):
                        generated_file = await database_sync_to_async(
                            ImageGenerationService.save_generated_image
                        )(
                            image_bytes=usage["image_bytes"],
                            prompt=message_data["message"],
                            metadata=ImageGenerationService.extract_image_metadata(usage),
                            user=self.user,  # Can be None for public bots
                            is_public=(self.user is None),
                        )

                        if generated_file:
                            await database_sync_to_async(message_obj.files.add)(generated_file)
                            generated_image_data = {
                                "fileId": generated_file.id,
                                "filename": generated_file.name,
                                "fileUrl": generated_file.file.url,
                                "prompt": message_data["message"],
                                "revisedPrompt": usage.get("revised_prompt", ""),
                                "cost": str(usage.get("cost", "0.040")),
                                "model": usage.get("model", "dall-e-3"),
                                "size": usage.get("size", "1024x1024"),
                                "quality": usage.get("quality", "standard"),
                                "style": usage.get("style", "vivid"),
                            }

                    # Check billing during streaming (only for authenticated users)
                    if self.user:
                        can_continue, error_response = await self.billing_service.check_streaming_credit_usage(
                            self.user, llm, token_usage
                        )
                        if not can_continue:
                            await self._handle_insufficient_balance(
                                message_obj, ai_response_accumulator, token_usage, error_response
                            )
                            return

                # Send chunk to client
                if chunk and chunk.strip():
                    ai_response_accumulator += chunk
                    payload = WebSocketResponseService.format_streaming_chunk(
                        message_id=bot_message_id,
                        chunk=ai_response_accumulator,
                        is_complete=False,
                        metadata={
                            "senderName": DEFAULT_AI_SENDER_NAME,
                            "senderType": SenderType.AI_ASSISTANT,
                            "isSender": False,
                            "streaming": True,
                            "regenerate": regenerate,
                            "date": message_obj.created_at.isoformat(),
                        }
                    )
                    await self.send(payload)

            # Finalize message
            if ai_response_accumulator.strip():
                await self._finalize_message(
                    message_obj=message_obj,
                    ai_response=ai_response_accumulator,
                    token_usage=token_usage,
                    regenerate=regenerate,
                    generated_image_data=generated_image_data,
                )

                # Run learning progress assessment (Socratic only, sequential after AI response)
                if not regenerate and should_run_learning_progress(self.platform, message_data.get("enable_progress")):
                    await self._run_learning_progress_stream(message_data, message_obj, llm)

        except Exception as e:
            logger.exception(f"Error streaming AI response: {str(e)}")
            await self.send_error(ErrorCode.STREAM_ERROR, ErrorMessage.STREAM_ERROR)

    async def _finalize_message(
        self,
        message_obj: Message,
        ai_response: str,
        token_usage: Optional[Dict],
        regenerate: bool = False,
        generated_image_data: Optional[Dict] = None,
    ):
        """
        Finalize AI message with billing or budget update.

        Args:
            message_obj: Message object to finalize
            ai_response: Complete AI response text
            token_usage: Token usage dictionary
            regenerate: Whether this is a regeneration
            generated_image_data: Optional image generation data
        """
        try:
            # Save original message content on first regeneration
            if regenerate and not message_obj.original_message:
                message_obj.original_message = message_obj.message
                await database_sync_to_async(message_obj.save)(update_fields=['original_message'])

            # Finalize with appropriate billing strategy
            if self.user:
                # Authenticated user - use wallet billing (platform auto-detected from conversation)
                finalized_message = await database_sync_to_async(
                    self.billing_service.finalize_ai_message
                )(message_obj, ai_response, token_usage or {})

                # Mark as regenerated if applicable
                if regenerate:
                    finalized_message.is_regenerated = True
                    await database_sync_to_async(finalized_message.save)(update_fields=['is_regenerated'])
            else:
                # Public bot - no billing, just calculate cost
                finalized_message, cost = await database_sync_to_async(
                    self.billing_service.finalize_ai_message_no_billing
                )(message_obj, ai_response, token_usage or {})

                # Mark as regenerated if applicable
                if regenerate:
                    finalized_message.is_regenerated = True
                    await database_sync_to_async(finalized_message.save)(update_fields=['is_regenerated'])

                # Update bot budget if applicable
                if cost > Decimal('0') and self.conversation.bot_id:
                    await BotBudgetService.update_bot_budget(
                        bot_id=self.conversation.bot_id,
                        cost=cost,
                        metadata={
                            'conversation_id': str(self.conversation.conversation_id),
                            'message_id': str(message_obj.id),
                            'input_tokens': message_obj.input_tokens,
                            'output_tokens': message_obj.output_tokens,
                        }
                    )

            # Send final message to client
            final_payload = await WebSocketResponseService.format_message(
                message=finalized_message,
                message_type="message",
                is_sender=False,
                streaming=False,
                regenerate=regenerate,
                generated_image=generated_image_data
            )

            await self.send(final_payload)

        except DjangoValidationError as e:
            logger.error(f"Validation error finalizing message: {str(e)}")
            await self.send_error(ErrorCode.VALIDATION_ERROR, str(e))
        except Exception as e:
            logger.exception(f"Error finalizing message: {str(e)}")
            await self.send_error(ErrorCode.FINALIZE_ERROR, ErrorMessage.FINALIZE_ERROR)

    async def _handle_insufficient_balance(
        self,
        message_obj: Message,
        ai_response: str,
        token_usage: Dict,
        error_response: Dict,
    ):
        """
        Handle mid-stream insufficient balance.

        Args:
            message_obj: Message object being streamed
            ai_response: Accumulated response so far
            token_usage: Current token usage
            error_response: Error details from billing service
        """
        try:
            # Finalize partial message (platform auto-detected from conversation)
            await database_sync_to_async(
                self.billing_service.finalize_ai_message
            )(message_obj, ai_response, token_usage)

            # Send partial message to client
            partial_payload = await WebSocketResponseService.format_message(
                message=message_obj,
                message_type="message",
                is_sender=False,
                streaming=False,
                regenerate=False
            )
            await self.send(partial_payload)

            # Send error
            await self.send_error(
                error_response.get("error", "insufficient_balance"),
                error_response.get("message", "Insufficient balance to continue"),
                error_response
            )

        except Exception as e:
            logger.exception(f"Error handling insufficient balance: {str(e)}")

    async def _run_learning_progress_stream(
        self,
        message_data: Dict[str, Any],
        message_obj: Message,
        llm: LLM,
    ):
        """
        Stream learning progress assessment (Socratic only).

        Args:
            message_data: Original message data with Socratic config
            message_obj: The AI message to assess
            llm: LLM instance used for the response
        """
        try:
            progress_llm_id = message_data.get("progress_llm_id") or llm.id
            progress_llm = await self._get_llm(progress_llm_id)

            if not progress_llm:
                logger.warning("Progress LLM not found, skipping assessment")
                return

            progress_accumulator = ""
            last_usage = None

            # Stream progress assessment
            async for chunk, usage in self.learning_progress_service.assess_learning_progress(
                conversation=self.conversation,
                last_message=message_obj,
                learning_goals=message_data.get("learning_goals", ""),
                tracking_prompt=message_data.get("tracking_prompt", ""),
                llm=progress_llm,
                max_tokens=2048,
                temperature=0.7,
                conversation_history_limit=80,
                bot_meta=message_data.get("bot_meta", {}),
            ):
                # Track usage for billing (authenticated users only)
                if usage:
                    last_usage = usage
                    # Skip billing check for public bots (user is None)
                    if self.user:
                        can_continue, _ = await self.billing_service.check_streaming_credit_usage(
                            self.user, progress_llm, usage
                        )
                        if not can_continue:
                            error_payload = WebSocketResponseService.format_progress_error(
                                "Insufficient credits during progress assessment"
                            )
                            await self.send(error_payload)
                            return

                if chunk and chunk.strip():
                    progress_accumulator += chunk
                    progress_payload = WebSocketResponseService.format_progress_chunk(
                        conversation_id=str(self.conversation.id),
                        message_id=str(message_obj.id),
                        chunk=chunk,
                    )
                    await self.send(progress_payload)

            # Save assessment and send completion
            if progress_accumulator.strip():
                # Build usage metadata for frontend
                def _build_usage(u: dict):
                    if not isinstance(u, dict):
                        return u
                    inp = u.get("input_tokens") or u.get("prompt_tokens") or 0
                    out = u.get("output_tokens") or u.get("completion_tokens") or 0
                    tot = (inp or 0) + (out or 0)
                    u_with_totals = dict(u)
                    u_with_totals["total_tokens"] = tot
                    return u_with_totals

                metadata = {
                    "llm_model": getattr(progress_llm, "identifier", None),
                    "usage": _build_usage(last_usage) if last_usage else None,
                    "platform": self.platform or "DARE",
                    "tracking_prompt_used": message_data.get("tracking_prompt", "")[:100],
                }

                # Save assessment to database (already decorated with @database_sync_to_async)
                assessment = await self.learning_progress_service._save_progress_assessment(
                    conversation=self.conversation,
                    content=progress_accumulator,
                    learning_goals=message_data.get("learning_goals", ""),
                    last_message=message_obj,
                    metadata=metadata,
                )

                # Update message with learning progress data
                def _update_msg():
                    message_obj.learning_progress_data = {
                        "progress_assessment_id": str(getattr(assessment, "id", "")),
                        "learning_goals": message_data.get("learning_goals", ""),
                        "tracking_prompt": message_data.get("tracking_prompt", ""),
                        "llm_id": getattr(progress_llm, "id", None),
                        "input_tokens": (last_usage or {}).get("input_tokens"),
                        "output_tokens": (last_usage or {}).get("output_tokens"),
                        "status": "completed",
                    }
                    message_obj.save(update_fields=["learning_progress_data"])
                    return message_obj

                await database_sync_to_async(_update_msg)()

                # Send completion notification
                completion_payload = WebSocketResponseService.format_progress_complete(
                    conversation_id=str(self.conversation.id),
                    message_id=str(message_obj.id),
                    input_tokens=last_usage.get("input_tokens") if last_usage else None,
                    output_tokens=last_usage.get("output_tokens") if last_usage else None,
                )
                await self.send(completion_payload)

        except Exception as e:
            logger.exception(f"Error running learning progress stream: {str(e)}")
            # Non-fatal - don't interrupt the conversation

    async def _generate_conversation_title(self):
        """Generate conversation title asynchronously (fire and forget)."""
        try:
            # Refresh conversation from DB
            await database_sync_to_async(self.conversation.refresh_from_db)()

            # Skip if title already set
            if self.conversation.title not in (None, "", DEFAULT_CONVERSATION_TITLE):
                return

            # Get latest user message for title generation
            user_message = await self._get_preceding_user_message()
            if not user_message:
                return

            # Generate title
            title = await self.conversation_service.generate_title(user_message.message)

            # Update conversation
            await self.conversation_service.update_conversation_title(self.conversation, title)

            # Send title to client
            payload = {
                "type": "conversation_title",
                "title": title
            }
            await self.send(payload)

        except Exception as e:
            logger.exception(f"Error generating conversation title: {str(e)}")
            # Non-fatal error

    async def _get_llm(self, llm_id: Optional[str], default: Optional[LLM] = None) -> Optional[LLM]:
        """
        Get LLM by ID with fallback.

        Args:
            llm_id: LLM ID to fetch
            default: Default LLM if ID not provided

        Returns:
            LLM instance or None
        """
        if llm_id:
            return await database_sync_to_async(
                lambda: LLM.objects.filter(id=llm_id).first()
            )()
        elif default:
            return default
        else:
            # Fallback to conversation's selected model or first available
            return await database_sync_to_async(
                lambda: self.conversation.selected_model or LLM.objects.first()
            )()

    async def _get_preceding_user_message(self) -> Optional[Message]:
        """Get the most recent user message in the conversation."""
        return await database_sync_to_async(
            lambda: self.conversation.messages.filter(
                sender_type=SenderType.PLAYER
            ).order_by('-created_at').first()
        )()

    async def send_conversation_history(self):
        """Send conversation history to client."""
        try:
            # Use the existing fetch_chat_history_from_db method
            # which returns already formatted and camelized history
            history = await self.conversation_service.fetch_chat_history_from_db(
                self.conversation
            )

            # Send as conversation_history message
            payload = {
                "type": "conversation_history",
                "conversationHistory": history
            }
            await self.send(payload)

        except Exception as e:
            logger.exception(f"Error sending conversation history: {str(e)}")

    async def send_latest_learning_progress(self):
        """Send latest learning progress assessment to client (Socratic only)."""
        try:
            latest = await self.learning_progress_service.get_latest_assessment(
                self.conversation
            )
            payload = {
                "type": "latest_progress",
                "conversationId": str(self.conversation.id),
                "assessment": latest  # None or dict
            }
            await self.send(payload)

        except Exception as e:
            logger.exception(f"Error sending latest progress: {str(e)}")
            # Non-fatal; send None assessment
            payload = {
                "type": "latest_progress",
                "conversationId": str(self.conversation.id),
                "assessment": None
            }
            await self.send(payload)

    # ========== Artifact Methods ==========

    async def _stream_artifact_response(
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
            from conversations.services.artifact_intent_detector import ArtifactIntentDetector
            resolved_action = ArtifactIntentDetector.detect_intent(
                message=message_data.get("message", ""),
                has_active_artifact=True,
            )
            # Use active artifact as target if not explicitly set
            if resolved_action == "modify":
                target_artifact_id = target_artifact_id or active_artifact_id
            logger.info(f"Artifact intent detection: action={artifact_action} -> resolved={resolved_action}")

        # Route to appropriate flow
        if resolved_action == "modify" and target_artifact_id:
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

            # Execute artifact generation
            # Note: Content is stored in the Artifact model, NOT in the message
            # The message just gets linked to the artifact via artifact_id
            async for chunk, usage in artifact_service.execute(
                message=message_data["message"],
                llm=llm,
                message_obj=message_obj,
                artifact_id=artifact_id,
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

            # Execute artifact modification
            async for chunk, usage in artifact_service.execute(
                message=message_data["message"],
                llm=llm,
                message_obj=message_obj,
                is_modification=True,
                target_artifact_id=target_artifact_id,
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

    async def handle_continue_artifact(
        self,
        message_data: Dict[str, Any],
        llm_id: Optional[str] = None,
    ) -> Optional[Message]:
        """
        Handle continuation of a paused artifact.

        Args:
            message_data: Validated message data with artifact_id
            llm_id: Optional LLM ID override

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

            # Get LLM
            llm = await self._get_llm(llm_id or message_data.get("llm_id"))
            if not llm:
                await self.send_error(ErrorCode.VALIDATION_ERROR, "Selected AI model not found")
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
            await self._stream_artifact_response(
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
            logger.info(f"MessageCoordinator: Starting pause for artifact_id={artifact_id}")

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

        logger.info(f"MessageCoordinator: Found artifact {artifact_id}, current status={artifact.status}")

        # Only update if not already paused or completed
        if artifact.status not in [ArtifactStatus.PAUSED, ArtifactStatus.COMPLETED]:
            artifact.status = ArtifactStatus.PAUSED
            await database_sync_to_async(artifact.save)(update_fields=['status', 'updated_at'])
            logger.info(f"MessageCoordinator: Updated artifact {artifact_id} status to PAUSED in database")

        # Try to send pause confirmation to frontend (may fail if disconnected)
        await self.send({
            "type": "artifact_pause",
            "artifactId": artifact_id,
            "currentSection": artifact.current_section,
            "sectionsRemaining": artifact.estimated_sections - artifact.current_section,
        })

        logger.info(f"Artifact {artifact_id} paused at section {artifact.current_section}")
