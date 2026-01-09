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
from typing import Optional, Dict, Any, Callable, List
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
from conversations.services.web_search_source_service import WebSearchSourceService
# Simplified artifact services (replaced legacy LangGraph system)
from conversations.services.artifact_intent_service import ArtifactIntentService
from conversations.services.simple_artifact_coordinator import SimpleArtifactCoordinator
from users.utils import should_run_learning_progress
from conversations.services.message_helpers import (
    build_transcription_data,
    build_usage_with_totals,
    build_generated_image_data,
    # Database helpers
    get_ai_message_by_id,
    get_message_media_file_ids,
    fetch_llm_by_id,
    get_conversation_default_llm,
    fetch_preceding_user_message,
    should_generate_title,
    update_message_learning_progress,
)

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

        # Simplified artifact services
        self.intent_service = ArtifactIntentService()
        self.simple_artifact_coordinator = SimpleArtifactCoordinator(
            conversation=conversation,
            user=user,
            send_callback=send_callback,
        )

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

    async def _save_attached_images(self, images: List[Dict]) -> List[int]:
        """
        Save base64 images as File objects and return their IDs.
        
        Args:
            images: List of base64 image dicts from frontend
            
        Returns:
            List of saved File IDs
        """
        if not images:
            return []
        
        saved_files = await database_sync_to_async(FileUploadService.save_base64_images)(
            images=images,
            user=self.user,
            is_public=(self.user is None)
        )
        file_ids = [f.id for f in saved_files]
        if file_ids:
            logger.info(f"Saved {len(file_ids)} attached images for message")
        return file_ids

    async def _handle_generated_image(
        self,
        usage: Dict,
        message_data: Dict,
        message_obj: 'Message'
    ) -> Optional[Dict]:
        """
        Handle generated image from DALL-E: save file and build response data.
        
        Args:
            usage: Usage dict containing image_bytes and metadata
            message_data: Original message data with prompt
            message_obj: Message to attach the image to
            
        Returns:
            Dict with image data for frontend, or None if no image
        """
        if not usage.get("image_bytes"):
            return None
        
        generated_file = await database_sync_to_async(ImageGenerationService.save_generated_image)(
            image_bytes=usage["image_bytes"],
            prompt=message_data["message"],
            metadata=ImageGenerationService.extract_image_metadata(usage),
            user=self.user,
            is_public=(self.user is None),
        )
        
        if not generated_file:
            return None
        
        await database_sync_to_async(message_obj.files.add)(generated_file)
        
        # Build and return the image data dict using helper function
        return build_generated_image_data(generated_file, message_data["message"], usage)


    async def _save_web_search_sources(
        self,
        message_obj: 'Message',
        token_usage: Optional[Dict],
        regenerate: bool
    ) -> None:
        """
        Save web search sources if present in token usage.
        
        Args:
            message_obj: Message to attach sources to
            token_usage: Usage dict possibly containing web_search_sources
            regenerate: Whether this is a regeneration (clears old sources first)
        """
        if not token_usage or not token_usage.get("web_search_sources"):
            return
        
        if regenerate:
            await WebSearchSourceService.delete_sources_for_message(message_obj)
        
        await WebSearchSourceService.save_sources(
            message=message_obj,
            sources=token_usage["web_search_sources"],
        )

    async def _mark_as_regenerated(self, message: 'Message') -> None:
        """Mark a message as regenerated if applicable."""
        message.is_regenerated = True
        await database_sync_to_async(message.save)(update_fields=['is_regenerated'])

    async def _update_public_bot_budget(
        self,
        cost: Decimal,
        message_obj: 'Message'
    ) -> None:
        """
        Update bot budget for public bot conversations.
        
        Args:
            cost: Cost to deduct from bot budget
            message_obj: Message for metadata
        """
        if cost <= Decimal('0') or not self.conversation.bot_id:
            return
        
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


    async def _handle_artifact_intent(
        self,
        message_data: Dict[str, Any],
        message_obj: Message,
        llm: LLM,
    ) -> bool:
        """
        Handle artifact intent detection and routing.
        
        Args:
            message_data: Validated message data
            message_obj: AI message object
            llm: LLM instance
            
        Returns:
            True if artifact was handled (caller should return), False to continue normal flow
        """
        active_artifact_id = message_data.get("active_artifact_id")
        
        try:
            # Get active artifact summary for context
            active_artifact = None
            if active_artifact_id:
                active_artifact = await self.intent_service.get_active_artifact_summary(
                    active_artifact_id,
                    conversation_id=self.conversation.conversation_id,
                )
            
            # Detect intent using LLM
            intent = await self.intent_service.detect_intent(
                message=message_data["message"],
                active_artifact=active_artifact,
                llm=llm,
                user=self.user,
            )
            
            logger.info(f"Artifact intent detected: {intent}")
            
            if intent == "chat":
                return False  # Continue to normal message streaming
            
            if intent == "diagram":
                logger.info("Intent is 'diagram', using tool-based diagram generation")
                await self.simple_artifact_coordinator.stream_diagram_response(
                    message_data=message_data, message_obj=message_obj, llm=llm
                )
                return True
            
            if intent == "chart":
                logger.info("Intent is 'chart', using tool-based chart generation")
                await self.simple_artifact_coordinator.stream_chart_response(
                    message_data=message_data, message_obj=message_obj, llm=llm
                )
                return True
            
            # Create or edit artifact
            await self.simple_artifact_coordinator.stream_artifact_response(
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

            # Save attached images and combine with existing file_ids
            attached_image_ids = await self._save_attached_images(message_data.get("images", []))
            all_file_ids = list(set((message_data.get("file_ids") or []) + attached_image_ids))

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

            # Generate conversation title if first message (User + AI = 2 messages)
            if await should_generate_title(self.conversation):
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
            ai_message = await get_ai_message_by_id(message_id)

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

            # Handle special model regeneration (image generator, audio transcriber)
            llm, regeneration_message_data = await self._prepare_regeneration_data(
                ai_message=ai_message,
                llm=llm,
                message_data=message_data,
                preceding_user_message=preceding_user_message,
            )
            if llm is None:
                return None  # Error already sent

            # Check billing if user exists
            if self.user:
                has_credits = await self.billing_service.check_sufficient_credits(
                    self.user, llm
                )
                if not has_credits:
                    await self.send_error(ErrorCode.INSUFFICIENT_CREDITS, ErrorMessage.INSUFFICIENT_CREDITS)
                    return None

            # Send streaming placeholder to show loading animation
            await self._send_regeneration_placeholder(ai_message)

            # Stream AI response into the EXISTING message (don't create new one)
            await self.stream_ai_response(
                message_data=regeneration_message_data,
                message_obj=ai_message,
                llm=llm,
                regenerate=True,
            )

            return ai_message

        except Exception as e:
            logger.exception(f"Error regenerating response: {str(e)}")
            await self.send_error(ErrorCode.REGENERATE_ERROR, ErrorMessage.REGENERATE_ERROR)
            return None

    async def _prepare_regeneration_data(
        self,
        ai_message: Message,
        llm: LLM,
        message_data: Dict[str, Any],
        preceding_user_message: Message,
    ) -> tuple[Optional[LLM], Dict[str, Any]]:
        """
        Prepare message data for regeneration based on original message type.

        Handles special cases:
        - Image generation: Switch to chat model (can't regenerate images)
        - Audio transcription: Re-run transcription with original media files

        Returns:
            Tuple of (llm, regeneration_message_data) or (None, {}) on error
        """
        original_llm = ai_message.llm
        regeneration_message_data = message_data.copy()
        regeneration_message_data["message"] = preceding_user_message.message

        # Image generator: switch to chat model
        if original_llm and original_llm.is_image_generator and llm == original_llm:
            default_llm = await database_sync_to_async(LLM.get_default_chat_model)()
            if not default_llm:
                await self.send_error(
                    ErrorCode.VALIDATION_ERROR,
                    "Cannot regenerate image: No chat model available."
                )
                return None, {}
            llm = default_llm

        elif original_llm and original_llm.is_audio_transcriber and llm == original_llm:
            media_file_ids = message_data.get("media_ids") or await get_message_media_file_ids(
                preceding_user_message
            )

            if not media_file_ids:
                await self.send_error(
                    ErrorCode.VALIDATION_ERROR,
                    "Cannot regenerate transcription: No audio/video files found."
                )
                return None, {}

            regeneration_message_data["audio_transcription_enabled"] = True
            regeneration_message_data["media_ids"] = media_file_ids

        # Regular chat: disable special modes
        else:
            regeneration_message_data["image_generation_enabled"] = False
            regeneration_message_data["audio_transcription_enabled"] = False

        return llm, regeneration_message_data

    async def _send_regeneration_placeholder(self, ai_message: Message) -> None:
        """Send streaming placeholder to show loading animation on frontend."""
        ai_message.message = ""
        placeholder_payload = await WebSocketResponseService.format_message(
            message=ai_message,
            message_type="message",
            is_sender=False,
            streaming=True,
            regenerate=True
        )
        await self.send(placeholder_payload)

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
        active_artifact_id = message_data.get("active_artifact_id")

        if artifacts_enabled and not regenerate:
            if await self._handle_artifact_intent(message_data, message_obj, llm):
                return

        try:
            bot_message_id = message_obj.id  # Keep as integer for consistency
            ai_response_accumulator = ""
            token_usage = None
            generated_image_data = None
            generated_transcription_data = None

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
                        generated_image_data = await self._handle_generated_image(
                            usage, message_data, message_obj
                        )

                    # Handle audio transcription (final result)
                    if usage.get("transcription_result"):
                        generated_transcription_data = build_transcription_data(usage)

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

            if ai_response_accumulator.strip():
                # Save web search sources if present (before finalization)
                await self._save_web_search_sources(message_obj, token_usage, regenerate)

                await self._finalize_message(
                    message_obj=message_obj,
                    ai_response=ai_response_accumulator,
                    token_usage=token_usage,
                    regenerate=regenerate,
                    generated_image_data=generated_image_data,
                    generated_transcription_data=generated_transcription_data,
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
        generated_transcription_data: Optional[Dict] = None,
    ):
        """
        Finalize AI message with billing or budget update.

        Args:
            message_obj: Message object to finalize
            ai_response: Complete AI response text
            token_usage: Token usage dictionary
            regenerate: Whether this is a regeneration
            generated_image_data: Optional image generation data
            generated_transcription_data: Optional audio transcription data
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
                    await self._mark_as_regenerated(finalized_message)
            else:
                # Public bot - no billing, just calculate cost
                finalized_message, cost = await database_sync_to_async(
                    self.billing_service.finalize_ai_message_no_billing
                )(message_obj, ai_response, token_usage or {})

                # Mark as regenerated if applicable
                if regenerate:
                    await self._mark_as_regenerated(finalized_message)

                # Update bot budget if applicable
                await self._update_public_bot_budget(cost, message_obj)

            # Send final message to client
            final_payload = await WebSocketResponseService.format_message(
                message=finalized_message,
                message_type="message",
                is_sender=False,
                streaming=False,
                regenerate=regenerate,
                generated_image=generated_image_data,
                generated_transcription=generated_transcription_data
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

            # All Socratic bot data comes from bot_meta (single source of truth)
            bot_meta = message_data.get("bot_meta", {})
            learning_goals = bot_meta.get("learning_goals", "")
            tracking_prompt = bot_meta.get("tracking_prompt", "")

            # Stream progress assessment
            async for chunk, usage in self.learning_progress_service.assess_learning_progress(
                conversation=self.conversation,
                last_message=message_obj,
                learning_goals=learning_goals,
                tracking_prompt=tracking_prompt,
                llm=progress_llm,
                max_tokens=2048,
                temperature=0.7,
                conversation_history_limit=80,
                bot_meta=bot_meta,
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

            if progress_accumulator.strip():
                # Build usage metadata for frontend
                metadata = {
                    "llm_model": getattr(progress_llm, "identifier", None),
                    "usage": build_usage_with_totals(last_usage),
                    "platform": self.platform or "DARE",
                    "tracking_prompt_used": tracking_prompt[:100] if tracking_prompt else "",
                }

                # Save assessment to database
                assessment = await self.learning_progress_service._save_progress_assessment(
                    conversation=self.conversation,
                    content=progress_accumulator,
                    learning_goals=learning_goals,
                    last_message=message_obj,
                    metadata=metadata,
                )

                # Update message with learning progress data
                await update_message_learning_progress(
                    message_obj, assessment, learning_goals, tracking_prompt, progress_llm, last_usage
                )

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
            return await fetch_llm_by_id(llm_id)
        elif default:
            return default
        else:
            return await get_conversation_default_llm(self.conversation)

    async def _get_preceding_user_message(self) -> Optional[Message]:
        """Get the most recent user message in the conversation."""
        return await fetch_preceding_user_message(self.conversation)

    async def send_conversation_history(self):
        """Send conversation history and artifacts to client."""
        try:
            # Use the existing fetch_chat_history_from_db method
            # which returns already formatted and camelized history
            history = await self.conversation_service.fetch_chat_history_from_db(
                self.conversation
            )

            # Fetch all artifacts for this conversation
            artifacts = await self._fetch_conversation_artifacts()

            # Send as conversation_history message with artifacts
            payload = {
                "type": "conversation_history",
                "conversationHistory": history,
                "artifacts": artifacts,  # Include artifacts for preloading
            }
            await self.send(payload)

        except Exception as e:
            logger.exception(f"Error sending conversation history: {str(e)}")

    async def _fetch_conversation_artifacts(self):
        """Fetch all artifacts for the current conversation."""
        def _get_artifacts():
            from conversations.api.serializers import ArtifactListSerializer
            artifacts = Artifact.active_objects.filter(
                conversation=self.conversation
            ).select_related('conversation', 'artifact_group', 'parent_artifact').order_by('-created_at')
            serializer = ArtifactListSerializer(artifacts, many=True)
            return serializer.data

        artifacts_data = await database_sync_to_async(_get_artifacts)()
        # Camelize the artifact data to match frontend expectations
        return camelize(artifacts_data)

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
