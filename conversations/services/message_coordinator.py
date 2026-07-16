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

import asyncio
import json
import logging
from typing import Any, Callable, Dict, List, Optional

from channels.db import database_sync_to_async
from django.utils import timezone
from djangorestframework_camel_case.util import camelize

from conversations.api.serializers import ArtifactListSerializer
from conversations.constants import (DEFAULT_AI_SENDER_NAME,
                                     DEFAULT_CONVERSATION_TITLE,
                                     ArtifactStatus, ErrorCode, ErrorMessage,
                                     SenderType, ToolCallOrigin,
                                     ToolCallStatus)
from conversations.models import (LLM, Artifact, Conversation, Message,
                                  MessageToolCall, Snippet, WebSearchSource)
from conversations.services.image_generation_service import \
    ImageGenerationService
from conversations.services.message_helpers import (  # Database helpers; Learning progress helpers; Billing helpers; Finalization helpers; Regeneration helpers
    build_generated_image_data, build_transcription_data,
    fetch_preceding_user_message, finalize_message, get_ai_message_by_id,
    get_conversation_default_descriptor, handle_insufficient_balance,
    parse_model_id, prepare_regeneration_data, run_learning_progress_stream,
    should_generate_title)
from conversations.services.message_validation_service import \
    MessageValidationService
from conversations.services.tool_loop_service import (ToolLoopResult,
                                                      ToolLoopService)
from conversations.services.web_search_source_service import \
    WebSearchSourceService
from conversations.services.websocket_response_service import \
    WebSocketResponseService
from core.services.billing_service import BillingService
from core.services.conversation_service import ConversationService
from core.services.dtos import LLMDescriptor, LLMQueryRequestBuilder
from core.services.file_upload_service import FileUploadService
from core.services.learning_progress_service import LearningProgressService
from core.services.llm_service import LLMService
from core.services.sb_client import SocraticBooksClient
from dare_tools.services.retrieval_tool_executor import RetrievalScope
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
        self.tool_loop_service = ToolLoopService(self.llm_service, self.billing_service)

        # Track active artifact generation tasks for cancellation
        self._artifact_tasks: Dict[str, asyncio.Task] = {}

    async def send(self, data: Dict[str, Any]):
        """Send data through WebSocket if callback is available."""
        if self.send_callback:
            try:
                await self.send_callback(json.dumps(camelize(data)))
            except Exception as e:
                # Log but don't raise - client may have disconnected
                logger.debug(
                    f"Failed to send WebSocket message (client may have disconnected): {type(e).__name__}"
                )

    async def send_error(
        self, error_code: str, error_message: str, details: Optional[Dict] = None
    ):
        """Send error response through WebSocket."""
        error_payload = WebSocketResponseService.format_error(
            error_code, error_message, details
        )
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

        saved_files = await database_sync_to_async(
            FileUploadService.save_base64_images
        )(images=images, user=self.user, is_public=(self.user is None))
        file_ids = [f.id for f in saved_files]
        if file_ids:
            logger.info(f"Saved {len(file_ids)} attached images for message")
        return file_ids

    async def _handle_generated_image(
        self, usage: Dict, message_data: Dict, message_obj: "Message"
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

        generated_file = await database_sync_to_async(
            ImageGenerationService.save_generated_image
        )(
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
        return build_generated_image_data(
            generated_file, message_data["message"], usage
        )

    async def _save_web_search_sources(
        self, message_obj: "Message", token_usage: Optional[Dict], regenerate: bool
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

    async def _save_provider_tool_calls(
        self, message_obj: "Message", token_usage: Optional[Dict], regenerate: bool
    ) -> None:
        """
        Save provider-native server tool calls, such as Anthropic web fetch or
        Gemini URL Context.

        These are not executed by DARE or MCP; the provider executes them and
        returns the result in the same model response. Persisting them in
        MessageToolCall keeps the frontend display consistent with existing
        DARE/MCP tool call history.
        """
        if not token_usage or not token_usage.get("provider_tool_calls"):
            return

        @database_sync_to_async
        def save_tool_calls():
            if regenerate:
                MessageToolCall.objects.filter(
                    message=message_obj,
                    origin=ToolCallOrigin.PROVIDER,
                ).delete()

            for tool_call in token_usage["provider_tool_calls"]:
                arguments = tool_call.get("arguments") or "{}"
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError:
                        arguments = {"raw": arguments}

                status = tool_call.get("status") or ToolCallStatus.COMPLETED
                result = tool_call.get("result")
                error = None
                if isinstance(result, dict) and result.get("error_code"):
                    error = result["error_code"]
                    status = ToolCallStatus.FAILED

                MessageToolCall.objects.create(
                    message=message_obj,
                    tool_call_id=tool_call.get("id", ""),
                    server_slug=tool_call.get("provider", "anthropic"),
                    origin=ToolCallOrigin.PROVIDER,
                    tool_name=tool_call.get("name", "provider_tool"),
                    arguments=arguments,
                    status=status,
                    result=json.dumps(result or {}),
                    error=error,
                    executed_at=timezone.now(),
                )

        await save_tool_calls()

    async def _save_memory_context(
        self,
        message_obj: "Message",
        token_usage: Optional[Dict],
    ) -> None:
        """
        Save memory context items to the message if present in token usage.

        Args:
            message_obj: Message to save memory context on
            token_usage: Usage dict possibly containing memory_context
        """
        if not token_usage or not token_usage.get("memory_context"):
            return

        message_obj.memory_context_data = token_usage["memory_context"]
        await database_sync_to_async(message_obj.save)(
            update_fields=["memory_context_data"]
        )

    async def _mark_as_regenerated(self, message: "Message") -> None:
        """Mark a message as regenerated if applicable."""
        message.is_regenerated = True
        await database_sync_to_async(message.save)(update_fields=["is_regenerated"])

    async def handle_new_message(
        self,
        message_data: Dict[str, Any],
        sender_name: str = None,
        model_id: Optional[str] = None,
    ) -> Optional[Message]:
        """
        Handle creation of a new user message and generate AI response.

        Args:
            message_data: Validated message data dictionary
            sender_name: Name of the sender (user email or "Anonymous User")
            model_id: Optional opaque dispatch id override (string). The FE
                hands back the same id the picker endpoint emitted; the BE
                inverts the encoding in ``parse_model_id``.

        Returns:
            The AI message object if successful, None otherwise
        """
        try:
            descriptor = await self._get_descriptor(
                model_id or message_data.get("model_id")
            )
            if descriptor is None:
                await self.send_error(
                    ErrorCode.VALIDATION_ERROR, "Selected AI model not found"
                )
                return None
            dispatch_handle = descriptor.to_dispatch_handle()

            # Check billing if user exists. Synthetic dispatches debit zero
            # from the DARE wallet (the stub LLM carries zero rates), so the
            # estimate is $0 and the check trivially passes — kept for
            # uniformity with real-LLM messages.
            if self.user:
                has_credits = await self.billing_service.check_sufficient_credits(
                    self.user, dispatch_handle
                )
                if not has_credits:
                    await self.send_error(
                        ErrorCode.INSUFFICIENT_CREDITS,
                        ErrorMessage.INSUFFICIENT_CREDITS,
                    )
                    return None
            elif self.conversation.bot_id:
                cap_error = await database_sync_to_async(self._public_bot_cap_error)(
                    self.conversation.bot_id
                )
                if cap_error:
                    await self.send_error(
                        cap_error["code"],
                        cap_error["message"],
                        cap_error.get("details"),
                    )
                    return None

            # Save attached images and combine with existing file_ids
            attached_image_ids = await self._save_attached_images(
                message_data.get("images", [])
            )
            all_file_ids = list(
                set((message_data.get("file_ids") or []) + attached_image_ids)
            )

            # Create user message
            user_message = await self.conversation_service.create_message(
                conversation=self.conversation,
                sender_type=SenderType.PLAYER,
                message_content=message_data["message"],
                sender=sender_name,
                file_ids=all_file_ids,
                tag_ids=message_data.get("tag_ids"),
                embedding_ids=message_data.get("embedding_ids"),
                descriptor=descriptor,
            )

            # Send user message to client
            user_message_payload = await WebSocketResponseService.format_message(
                message=user_message, is_sender=True
            )
            await self.send(user_message_payload)

            # Create empty AI message
            ai_message = await self.conversation_service.create_message(
                conversation=self.conversation,
                sender_type=SenderType.AI_ASSISTANT,
                message_content="",
                sender=DEFAULT_AI_SENDER_NAME,
                descriptor=descriptor,
            )

            # Send empty AI message with streaming=True to show placeholder on frontend
            placeholder_payload = await WebSocketResponseService.format_message(
                message=ai_message,
                message_type="message",
                is_sender=False,
                streaming=True,
                regenerate=False,
            )
            await self.send(placeholder_payload)

            # Generate conversation title if first message (User + AI = 2 messages)
            if await should_generate_title(self.conversation):
                asyncio.create_task(self._generate_conversation_title())

            # Stream AI response
            await self.stream_ai_response(
                message_data=message_data,
                message_obj=ai_message,
                llm=dispatch_handle,
                regenerate=False,
            )

            return ai_message

        except Exception as e:
            logger.exception(f"Error handling new message: {str(e)}")
            await self.send_error(
                ErrorCode.PROCESSING_ERROR, ErrorMessage.PROCESSING_ERROR
            )
            return None

    async def handle_regenerate_response(
        self,
        message_data: Dict[str, Any],
        model_id: Optional[str] = None,
    ) -> Optional[Message]:
        """
        Handle regeneration of an existing AI message.

        Args:
            message_data: Validated message data dictionary (must include message_id)
            model_id: Optional opaque dispatch id override (see
                ``handle_new_message`` for the encoding).

        Returns:
            The regenerated AI message object if successful, None otherwise
        """
        try:
            # Get message_id from message_data
            message_id = message_data.get("message_id")
            if not message_id:
                await self.send_error(
                    ErrorCode.MISSING_DATA, ErrorMessage.MISSING_MESSAGE_ID
                )
                return None

            # Get the existing AI message to regenerate
            ai_message = await get_ai_message_by_id(message_id, self.conversation.id)

            if not ai_message:
                await self.send_error(
                    ErrorCode.INVALID_MESSAGE, ErrorMessage.INVALID_MESSAGE
                )
                return None

            # Get the preceding user message
            preceding_user_message = await self._get_preceding_user_message()
            if not preceding_user_message:
                await self.send_error(
                    ErrorCode.NO_USER_MESSAGE, ErrorMessage.NO_USER_MESSAGE
                )
                return None

            # Get descriptor: explicit override → existing message's recorded
            # model (real or LiteLLM-routed). For LITELLM messages the previous
            # dispatch is reconstructed from `litellm_key` + `litellm_model_name`.
            descriptor = await self._get_descriptor(
                model_id or message_data.get("model_id"),
                default=LLMDescriptor.from_message(ai_message),
            )
            if descriptor is None:
                await self.send_error(
                    ErrorCode.VALIDATION_ERROR, "Selected AI model not found"
                )
                return None
            dispatch_handle = descriptor.to_dispatch_handle()

            # Handle special model regeneration (image generator, audio transcriber)
            dispatch_handle, regeneration_message_data = (
                await self._prepare_regeneration_data(
                    ai_message=ai_message,
                    llm=dispatch_handle,
                    message_data=message_data,
                    preceding_user_message=preceding_user_message,
                )
            )
            if dispatch_handle is None:
                return None  # Error already sent

            # Check billing if user exists (synthetic descriptors estimate $0).
            if self.user:
                has_credits = await self.billing_service.check_sufficient_credits(
                    self.user, dispatch_handle
                )
                if not has_credits:
                    await self.send_error(
                        ErrorCode.INSUFFICIENT_CREDITS,
                        ErrorMessage.INSUFFICIENT_CREDITS,
                    )
                    return None

            await self._clear_regeneration_run_state(ai_message)

            # Send streaming placeholder to show loading animation
            await self._send_regeneration_placeholder(ai_message)

            # Stream AI response into the EXISTING message (don't create new one)
            await self.stream_ai_response(
                message_data=regeneration_message_data,
                message_obj=ai_message,
                llm=dispatch_handle,
                regenerate=True,
            )

            return ai_message

        except Exception as e:
            logger.exception(f"Error regenerating response: {str(e)}")
            await self.send_error(
                ErrorCode.REGENERATE_ERROR, ErrorMessage.REGENERATE_ERROR
            )
            return None

    async def _prepare_regeneration_data(
        self,
        ai_message: Message,
        llm: LLM,
        message_data: Dict[str, Any],
        preceding_user_message: Message,
    ) -> tuple[Optional[LLM], Dict[str, Any]]:
        """Prepare message data for regeneration based on original message type."""
        return await prepare_regeneration_data(
            ai_message=ai_message,
            llm=llm,
            message_data=message_data,
            preceding_user_message=preceding_user_message,
            send_error_callback=self.send_error,
        )

    async def _send_regeneration_placeholder(self, ai_message: Message) -> None:
        """Send streaming placeholder to show loading animation on frontend."""
        ai_message.message = ""
        placeholder_payload = await WebSocketResponseService.format_message(
            message=ai_message,
            message_type="message",
            is_sender=False,
            streaming=True,
            regenerate=True,
        )
        await self.send(placeholder_payload)

    @database_sync_to_async
    def _clear_regeneration_run_state(self, ai_message: Message) -> None:
        """Remove prior-run state before regenerating an assistant message.

        Artifacts are detached, not deleted: they remain available in the
        conversation artifact library while the regenerated message gets a
        clean set of cards and model-visible artifact associations.
        """
        Snippet.active_objects.filter(message=ai_message).delete()
        WebSearchSource.active_objects.filter(message=ai_message).delete()
        MessageToolCall.objects.filter(message=ai_message).delete()
        Artifact.active_objects.filter(message=ai_message).update(message=None)
        # Capture the pre-regeneration text NOW: the placeholder sender blanks
        # ai_message.message in memory right after this, so waiting until
        # finalization (the old behavior) captured an empty string.
        if not ai_message.original_message and ai_message.message:
            ai_message.original_message = ai_message.message
        ai_message.retrieval_trace = None
        ai_message.memory_context_data = []
        ai_message.usage_details = None
        ai_message.save(
            update_fields=[
                "original_message",
                "retrieval_trace",
                "memory_context_data",
                "usage_details",
            ]
        )

    async def stream_ai_response(
        self,
        message_data: Dict[str, Any],
        message_obj: Message,
        llm: LLM,
        regenerate: bool = False,
    ):
        """
        Stream AI response with billing checks.

        Standard chat runs through the bounded tool loop (ToolLoopService):
        the model streams, executes tools, sees their results as native
        tool turns, and keeps going until it answers in text or the round
        cap forces a final answer. Image generation and audio transcription
        remain single-pass flows.

        Args:
            message_data: Validated message data
            message_obj: Empty AI message object to populate
            llm: LLM instance to use
            regenerate: Whether this is a regeneration request
        """
        try:
            logger.info(
                "[journey] mid=%s turn start: conv=%s regenerate=%s rag=%s "
                "dare_tools=%s mcp_servers=%s artifacts=%s web_search=%s",
                message_obj.id,
                self.conversation.id,
                regenerate,
                message_data.get("rag_mode"),
                message_data.get("dare_tool_slugs") or [],
                message_data.get("mcp_server_ids") or [],
                message_data.get("artifacts_enabled", False),
                message_data.get("web_search_enabled", False),
            )
            # Build LLM query request using DTO builder
            # Note: mcp_server_ids are automatically extracted in the builder
            request = LLMQueryRequestBuilder.from_message_data(
                message=message_data["message"],
                conversation=self.conversation,
                user=self.user,
                message_data=message_data,
                llm=llm,
                message_obj=message_obj,
                platform=self.platform,
            )

            # Image generation / audio transcription bypass the tool loop —
            # single-pass flows with their own usage payloads.
            if (
                request.requires_audio_transcription()
                or request.requires_image_generation()
            ):
                await self._stream_media_response(
                    request, message_data, message_obj, llm, regenerate
                )
                return

            retrieval_scope = RetrievalScope(
                embedding_ids=tuple(request.context.embedding_ids or ()),
                tag_ids=tuple(request.context.tag_ids or ()),
                folder_ids=tuple(request.context.folder_ids or ()),
                library_ids=tuple(request.context.library_ids or ()),
                user_id=self.user.id if self.user else None,
                file_owner_id=request.context.file_owner_id,
                max_context_snippets=request.context.max_context_snippets,
                similarity_threshold=request.context.document_similarity_threshold,
            )

            result = await self.tool_loop_service.run(
                request=request,
                message_obj=message_obj,
                llm=llm,
                user=self.user,
                conversation=self.conversation,
                send_callback=self.send,
                retrieval_scope=retrieval_scope,
                regenerate=regenerate,
            )

            if result.interrupted:
                await self._handle_insufficient_balance(
                    message_obj,
                    result.text,
                    result.token_usage or {},
                    result.error_response,
                )
                return

            token_usage = result.token_usage
            if result.timed_out:
                # A stalled provider stream ends the turn, but everything
                # that already happened is real: keep the streamed text,
                # bill the accumulated usage, and be honest about the cut.
                await self._finalize_timed_out_turn(message_obj, result, regenerate)
                return

            if result.text.strip():
                await self._save_web_search_sources(
                    message_obj, token_usage, regenerate
                )
                await self._save_provider_tool_calls(
                    message_obj, token_usage, regenerate
                )
                await self._save_memory_context(message_obj, token_usage)
                await self._save_usage_breakdown(message_obj, result.usage_breakdown)
                logger.info(
                    "[journey] mid=%s finalizing: text=%d chars, tokens=%s/%s",
                    message_obj.id,
                    len(result.text),
                    (token_usage or {}).get("input_tokens"),
                    (token_usage or {}).get("output_tokens"),
                )
                await self._finalize_message(
                    message_obj=message_obj,
                    ai_response=result.text,
                    token_usage=token_usage,
                    regenerate=regenerate,
                )

                # Run learning progress assessment (Socratic only, sequential after AI response)
                if not regenerate and should_run_learning_progress(
                    self.platform, message_data.get("enable_progress")
                ):
                    await self._run_learning_progress_stream(
                        message_data, message_obj, llm
                    )
            elif result.tool_calls_made:
                # Edge case: tools ran but the model produced no text.
                logger.warning(
                    "[MessageCoordinator] Tools ran but no response was generated; "
                    "finalizing with fallback message."
                )
                await self._finalize_message(
                    message_obj=message_obj,
                    ai_response=(
                        "The tool execution completed but I was unable to "
                        "generate a response. Please try again."
                    ),
                    token_usage=token_usage,
                    regenerate=regenerate,
                )
            else:
                # Some provider streams can close cleanly without yielding a
                # token or an exception. Treat that as a completed failure,
                # otherwise the original empty placeholder remains forever.
                logger.warning(
                    "[MessageCoordinator] Provider stream ended without text "
                    "or tool calls; finalizing with retry guidance."
                )
                await self._finalize_message(
                    message_obj=message_obj,
                    ai_response=(
                        "I couldn’t generate a response for that request. "
                        "Your request is saved—please retry it."
                    ),
                    token_usage=token_usage,
                    regenerate=regenerate,
                )

        except Exception as e:
            logger.exception(f"Error streaming AI response: {str(e)}")
            # Never leave the placeholder as an empty, apparently streaming
            # message. Persist a visible recovery state so refresh and socket
            # reconnects agree about how the turn ended.
            await self._finalize_message(
                message_obj=message_obj,
                ai_response=(
                    "I couldn’t complete that response because the model stopped "
                    "responding. Your request is saved—please retry it."
                ),
                token_usage=None,
                regenerate=regenerate,
            )
            await self.send_error(ErrorCode.STREAM_ERROR, ErrorMessage.STREAM_ERROR)

    async def _stream_media_response(
        self,
        request,
        message_data: Dict[str, Any],
        message_obj: Message,
        llm: LLM,
        regenerate: bool,
    ):
        """Single-pass streaming for image generation / audio transcription."""
        try:
            bot_message_id = message_obj.id
            ai_response_accumulator = ""
            token_usage = None
            generated_image_data = None
            generated_transcription_data = None

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
                        can_continue, error_response = (
                            await self.billing_service.check_streaming_credit_usage(
                                self.user, llm, token_usage
                            )
                        )
                        if not can_continue:
                            await self._handle_insufficient_balance(
                                message_obj,
                                ai_response_accumulator,
                                token_usage,
                                error_response,
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
                            "streaming": True,
                            "regenerate": regenerate,
                            "createdAt": message_obj.created_at.isoformat(),
                        },
                    )
                    await self.send(payload)

            # Ensure we always finalize the message
            if ai_response_accumulator.strip():
                # Save memory context if present (before finalization)
                await self._save_memory_context(message_obj, token_usage)

                await self._finalize_message(
                    message_obj=message_obj,
                    ai_response=ai_response_accumulator,
                    token_usage=token_usage,
                    regenerate=regenerate,
                    generated_image_data=generated_image_data,
                    generated_transcription_data=generated_transcription_data,
                )

        except Exception as e:
            logger.exception(f"Error streaming media response: {str(e)}")
            await self.send_error(ErrorCode.STREAM_ERROR, ErrorMessage.STREAM_ERROR)

    async def _finalize_timed_out_turn(
        self, message_obj: Message, result: ToolLoopResult, regenerate: bool
    ) -> None:
        """Finalize a turn whose provider stream went idle mid-way.

        The partial text stays (the user already read it), the accumulated
        usage is billed, and completed tool work keeps its persisted rows —
        so the notice must not tell the user to retry when tools already
        produced artifacts.
        """
        token_usage = result.token_usage
        if result.text.strip():
            notice = "The model stopped responding, so this answer was cut short."
            if result.tool_calls_made:
                notice += " The work completed by tools so far has been kept."
            ai_response = f"{result.text}\n\n*{notice}*"
            await self._save_web_search_sources(message_obj, token_usage, regenerate)
            await self._save_provider_tool_calls(message_obj, token_usage, regenerate)
            await self._save_memory_context(message_obj, token_usage)
            await self._save_usage_breakdown(message_obj, result.usage_breakdown)
        elif result.tool_calls_made:
            ai_response = (
                "The model stopped responding before it could summarize, but "
                "the tool work it completed has been kept. You can ask me to "
                "continue from here."
            )
        else:
            ai_response = (
                "I couldn’t complete that response because the model stopped "
                "responding. Your request is saved—please retry it."
            )
        await self._finalize_message(
            message_obj=message_obj,
            ai_response=ai_response,
            token_usage=token_usage,
            regenerate=regenerate,
        )
        await self.send_error(ErrorCode.STREAM_ERROR, ErrorMessage.STREAM_ERROR)

    @database_sync_to_async
    def _save_usage_breakdown(
        self, message_obj: Message, usage_breakdown: List[Dict]
    ) -> None:
        """Persist the per-round token breakdown for multi-round turns."""
        if not usage_breakdown or len(usage_breakdown) < 2:
            return
        try:
            message_obj.usage_details = usage_breakdown
            message_obj.save(update_fields=["usage_details"])
        except Exception as exc:
            logger.warning("Failed to save usage breakdown: %s", exc)

    async def _finalize_message(
        self,
        message_obj: Message,
        ai_response: str,
        token_usage: Optional[Dict],
        regenerate: bool = False,
        generated_image_data: Optional[Dict] = None,
        generated_transcription_data: Optional[Dict] = None,
    ):
        """Finalize AI message with billing or budget update."""
        return await finalize_message(
            message_obj=message_obj,
            ai_response=ai_response,
            token_usage=token_usage,
            regenerate=regenerate,
            generated_image_data=generated_image_data,
            generated_transcription_data=generated_transcription_data,
            user=self.user,
            conversation=self.conversation,
            billing_service=self.billing_service,
            send_callback=self.send,
            send_error_callback=self.send_error,
            mark_as_regenerated_callback=self._mark_as_regenerated,
        )

    @staticmethod
    def _public_bot_cap_error(bot_id: int) -> Optional[Dict[str, Any]]:
        config = SocraticBooksClient.get_bot_billing_config(bot_id)
        if config is None:
            return {
                "code": "bot_config_unavailable",
                "message": "Unable to verify this public bot's deployment budget.",
            }
        if not config.is_active or not config.is_publicly_deployed:
            return {
                "code": "bot_unavailable",
                "message": "This bot is not currently available for public use.",
            }
        if config.budget is None or config.budget <= 0:
            return {
                "code": "bot_cap_reached",
                "message": "This bot is missing a public deployment budget cap.",
            }
        if config.budget_used >= config.budget:
            return {
                "code": "bot_cap_reached",
                "message": "This bot has reached its public deployment budget cap.",
                "details": {
                    "budget": str(config.budget),
                    "budgetUsed": str(config.budget_used),
                },
            }
        return None

    async def _handle_insufficient_balance(
        self,
        message_obj: Message,
        ai_response: str,
        token_usage: Dict,
        error_response: Dict,
    ):
        """Handle mid-stream insufficient balance."""
        return await handle_insufficient_balance(
            message_obj=message_obj,
            ai_response=ai_response,
            token_usage=token_usage,
            error_response=error_response,
            billing_service=self.billing_service,
            send_callback=self.send,
            send_error_callback=self.send_error,
        )

    async def _fetch_progress_llm(self, llm_id) -> Optional[LLM]:
        """Resolve the Socratic progress-assessment LLM by numeric id.

        Progress LLMs are configured per-bot in SocraticBooks and are always
        DB-backed (never LiteLLM-routed), so we stringify the bare id to
        feed the opaque-id parser.
        """
        if llm_id is None:
            return None
        descriptor = await self._get_descriptor(str(llm_id))
        return descriptor.llm if descriptor and not descriptor.is_synthetic else None

    async def _run_learning_progress_stream(
        self,
        message_data: Dict[str, Any],
        message_obj: Message,
        llm: LLM,
    ):
        """Stream learning progress assessment (Socratic only)."""
        return await run_learning_progress_stream(
            conversation=self.conversation,
            message_data=message_data,
            message_obj=message_obj,
            llm=llm,
            platform=self.platform,
            learning_progress_service=self.learning_progress_service,
            billing_service=self.billing_service,
            user=self.user,
            send_callback=self.send,
            get_llm_callback=self._fetch_progress_llm,
        )

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

            # Generate title — pass `user` so the title call honors the
            # user's active wallet (DARE/BYO/LITELLM) just like the main chat.
            title = await self.conversation_service.generate_title(
                user_message.message,
                user=self.user,
            )

            # Update conversation
            await self.conversation_service.update_conversation_title(
                self.conversation, title
            )

            # Send title to client
            payload = {"type": "conversation_title", "title": title}
            await self.send(payload)

        except Exception as e:
            logger.exception(f"Error generating conversation title: {str(e)}")
            # Non-fatal error

    async def _get_descriptor(
        self,
        model_id: Optional[str],
        default: Optional[LLMDescriptor] = None,
    ) -> Optional[LLMDescriptor]:
        """Resolve an opaque ``model_id`` from the FE to a runtime descriptor.

        Args:
            model_id: Opaque identifier from the FE — either ``"<int>"`` (a
                DB-backed LLM PK) or ``"litellm:<key_pk>:<model>"``. Decoded
                in ``parse_model_id``.
            default: Descriptor to return when ``model_id`` is empty.

        Returns:
            ``LLMDescriptor`` for the chosen model, or ``None`` when neither
            the id nor the conversation default resolves.
        """
        if model_id:
            return await parse_model_id(model_id, user=self.user)
        if default is not None:
            return default
        return await get_conversation_default_descriptor(
            self.conversation, user=self.user
        )

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

            artifacts = (
                Artifact.active_objects.filter(conversation=self.conversation)
                .select_related("conversation", "artifact_group", "parent_artifact")
                .order_by("-created_at")
            )
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
                "assessment": latest,  # None or dict
            }
            await self.send(payload)

        except Exception as e:
            logger.exception(f"Error sending latest progress: {str(e)}")
            # Non-fatal; send None assessment
            payload = {
                "type": "latest_progress",
                "conversationId": str(self.conversation.id),
                "assessment": None,
            }
            await self.send(payload)
