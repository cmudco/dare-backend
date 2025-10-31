import json
import logging
import uuid
import asyncio
import os
import requests
from typing import Optional, Dict, Any
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from channels.exceptions import DenyConnection
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from pydantic import ValidationError
from asgiref.sync import sync_to_async
from djangorestframework_camel_case.util import camelize
from django.core.files.base import ContentFile

from conversations.models import Conversation, Message, LLM
from files.models import File
from files.constants import FileStatus
from core.services.conversation_service import ConversationService
from core.services.llm_service import LLMService
from core.services.billing_service import BillingService
from core.services.learning_progress_service import LearningProgressService
from core.services.dtos import LLMQueryRequestBuilder
from .constants import SenderType
from conversations.api.serializers import MessageSerializer
from users.utils import detect_platform_from_scope, should_run_learning_progress
from users.constants import AuthSourceChoice

User = get_user_model()
logger = logging.getLogger(__name__)

class ChatConsumer(AsyncWebsocketConsumer):
    DEFAULT_TEMPERATURE = 0.7
    DEFAULT_MAX_TOKENS = 8000
    DEFAULT_MAX_CONTEXT_SNIPPETS = 4
    DEFAULT_DOCUMENT_SIMILARITY_THRESHOLD = 0.5
    DEFAULT_HISTORY_LIMIT = 20
    
    # Default tracking prompt from SocraticBooks migration
    DEFAULT_TRACKING_PROMPT = """You are an AI tutor designed to assess student learning progress. Based on the conversation history and learning goals provided, evaluate the student's understanding and provide constructive feedback.

Please analyze:
1. What concepts the student has grasped well
2. Areas where they need improvement
3. Specific misconceptions or gaps in understanding
4. Recommendations for next steps in their learning journey

Provide your assessment in a clear, encouraging format that helps track their progress toward the learning goals."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.conversation_service = ConversationService()
        self.llm_service = LLMService()
        self.billing_service = BillingService()
        self.learning_progress_service = LearningProgressService()
        self.user: Optional[User] = None
        self.conversation: Optional[Conversation] = None
        self.conversation_id: Optional[str] = None
        self.platform: Optional[str] = None

    async def connect(self):
        """Initialize WebSocket connection and validate conversation."""
        try:
            self.user = self.scope["user"]
            self.conversation_id = self.scope["url_route"]["kwargs"].get("conversation_id")
            self.conversation = await self.conversation_service.get_conversation(self.conversation_id, self.user)
            if not self.conversation:
                logger.warning(f"Invalid conversation_id: {self.conversation_id} for user: {self.user.id}")
                raise DenyConnection("Invalid conversation_id")
            # Detect platform from ASGI scope headers
            self.platform = detect_platform_from_scope(self.scope)
            await self.accept()
            await self.load_conversation_history()
            # Also send the latest learning progress assessment if available
            await self.send_latest_progress()
        except DenyConnection as e:
            logger.error(f"Connection denied: {str(e)}")
            await self.close(code=4000)
        except Exception as e:
            logger.exception(f"Error during connect: {str(e)}")
            await self.close(code=4001)

    async def receive(self, text_data: str = None, bytes_data: bytes = None):
        """Handle incoming WebSocket messages."""
        try:
            data = json.loads(text_data)
            action = data.get("action")
            if action == "edit_message":
                await self.handle_edit_message(data)
            elif action == "regenerate_response":
                await self.handle_regenerate_response(data)
            else:
                await self.handle_new_message(data)
        except json.JSONDecodeError:
            await self.send_error("invalid_json", "Invalid JSON format")
        except Exception as e:
            logger.exception(f"Error processing message: {str(e)}")
            await self.send_error("processing_error", "Failed to process message")

    async def handle_new_message(self, data: Dict[str, Any]):
        """Process new user message and stream AI response."""
        try:
            message_data = self._validate_message_data(data)
            llm = await self._get_llm(message_data.get("llm_id"))
            if not await self.billing_service.check_sufficient_credits(self.user, llm):
                await self.send_error("insufficient_credits", "Insufficient wallet balance")
                return

            message_obj = await self.conversation_service.create_message(
                self.conversation,
                message_data["sender_type"],
                message_data["message"],
                self.user.email
            )
            await self.send(await self._format_message(message_obj, is_sender=True))

            if await self.conversation_service.is_first_message(self.conversation):
                asyncio.create_task(self._generate_conversation_title(message_data["message"]))

            bot_message_obj = await self.conversation_service.create_message(
                self.conversation,
                SenderType.AI_ASSISTANT,
                "",
                "AI Assistant",
                message_data["file_ids"],
                message_data["tag_ids"],
                message_data["embedding_ids"],
                llm
            )
            await self.send(await self._format_message(bot_message_obj, streaming=True))

            await self._stream_ai_response(message_data, bot_message_obj, llm)
        except ValidationError as e:
            await self.send_error("validation_error", str(e))
        except Exception as e:
            logger.exception(f"Error in handle_new_message: {str(e)}")
            await self.send_error("ai_response_error", "Failed to generate AI response")

    async def handle_edit_message(self, data: Dict[str, Any]):
        """Edit the latest user message."""
        try:
            message_id = data.get("message_id")
            new_content = data.get("message", "").strip()
            if not message_id or not new_content:
                await self.send_error("missing_data", "Missing message_id or message content")
                return

            updated_message = await self.conversation_service.edit_message(
                message_id, new_content, self.conversation
            )
            await self.send(await self._format_message(updated_message, is_sender=True))
        except ValueError as e:
            await self.send_error("invalid_edit", str(e))
        except Exception as e:
            logger.exception(f"Error in handle_edit_message: {str(e)}")
            await self.send_error("edit_error", "Failed to edit message")

    async def handle_regenerate_response(self, data: Dict[str, Any]):
        """Regenerate an AI response for a given message."""
        try:
            message_id = data.get("message_id")
            if not message_id:
                await self.send_error("missing_data", "Missing message_id")
                return

            ai_message = await database_sync_to_async(
                lambda: Message.active_objects.select_related('llm').filter(
                    id=message_id, sender_type=SenderType.AI_ASSISTANT
                ).first()
            )()
            if not ai_message:
                await self.send_error("invalid_message", "AI message not found")
                return

            preceding_user_message = await self._get_preceding_user_message(ai_message)
            if not preceding_user_message:
                await self.send_error("no_user_message", "No preceding user message found")
                return

            llm = await self._get_llm(data.get("llm_id"), default=ai_message.llm)
            if not await self.billing_service.check_sufficient_credits(self.user, llm):
                await self.send_error("insufficient_credits", "Insufficient wallet balance")
                return

            message_data = self._validate_message_data(data, default_message=preceding_user_message.message)
            await self._stream_ai_response(message_data, ai_message, llm, regenerate=True)
        except Exception as e:
            logger.exception(f"Error in handle_regenerate_response: {str(e)}")
            await self.send_error("regenerate_error", "Failed to regenerate response")


    async def _stream_ai_response(self, message_data: Dict[str, Any], message_obj: Message, llm: LLM, regenerate: bool = False):
        """Stream AI response and handle billing."""
        try:
            bot_message_id = str(message_obj.id)
            ai_response_accumulator = ""
            token_usage = None
            generated_image_data = None

            # Build LLM query request using DTO builder
            # Builder automatically applies conversation-level defaults
            request = LLMQueryRequestBuilder.from_message_data(
                message=message_data["message"],
                conversation=self.conversation,
                user=self.user,
                message_data=message_data,
                llm=llm,
                message_obj=message_obj,
                platform=self.platform,
            )

            async for chunk, usage in self.llm_service.query(request):
                if usage:
                    token_usage = usage

                    # Handle generated image
                    if usage.get("image_bytes"):
                        generated_file = await self._save_generated_image(
                            image_bytes=usage["image_bytes"],
                            prompt=message_data["message"],
                            metadata=usage
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

                    can_continue, error_response = await self.billing_service.check_streaming_credit_usage(
                        self.user, llm, token_usage
                    )
                    if not can_continue:
                        await self._handle_insufficient_balance(
                            message_obj, ai_response_accumulator, token_usage, error_response
                        )
                        return

                if chunk and chunk.strip():
                    ai_response_accumulator += chunk
                    payload = {
                        "type": "ai_stream",
                        "id": bot_message_id,
                        "message": ai_response_accumulator,
                        "senderName": "AI Assistant",
                        "senderType": SenderType.AI_ASSISTANT,
                        "isSender": False,
                        "streaming": True,
                        "regenerate": regenerate,
                        "date": message_obj.created_at.isoformat(),
                    }
                    await self.send(json.dumps(camelize(payload)))

            if ai_response_accumulator.strip():
                await self._finalize_message(message_obj, ai_response_accumulator, token_usage, regenerate, generated_image_data)
                # Socratic-only sequential progress stream
                if not regenerate and should_run_learning_progress(self.platform, message_data.get("enable_progress")):
                    await self._run_learning_progress_stream(message_data, message_obj, llm)
        except Exception as e:
            logger.exception(f"Error streaming AI response: {str(e)}")
            await self.send_error("stream_error", "Failed to stream AI response")

    @database_sync_to_async
    def _save_generated_image(self, image_bytes: bytes, prompt: str, metadata: Dict) -> Optional[File]:
        """Save AI-generated image as a File object."""
        try:
            from datetime import datetime

            # Generate filename
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"dalle_generated_{timestamp}.png"

            # Create File object
            file_obj = File(
                user=self.user,
                name=filename,
                file_type="image/png",
                size=len(image_bytes),
                status=FileStatus.PROCESSED,
                is_media=True,
                media_type='generated_image',
                is_generated=True,
                generation_prompt=prompt,
                revised_prompt=metadata.get('revised_prompt', ''),
                generation_params={
                    'model': metadata.get('model', 'dall-e-3'),
                    'size': metadata.get('size', '1024x1024'),
                    'quality': metadata.get('quality', 'standard'),
                    'style': metadata.get('style', 'vivid'),
                }
            )

            # Save the image file
            file_obj.file.save(filename, ContentFile(image_bytes), save=False)
            file_obj.save()

            logger.info(f"Saved generated image as File ID: {file_obj.id}")
            return file_obj

        except Exception as e:
            logger.exception(f"Error saving generated image: {str(e)}")
            return None

    async def _finalize_message(self, message_obj: Message, ai_response: str, token_usage: Dict, regenerate: bool, generated_image_data: Dict = None):
        """Finalize AI message with billing and send final response."""
        try:
            if regenerate and not message_obj.original_message:
                message_obj.original_message = message_obj.message
                await database_sync_to_async(message_obj.save)(update_fields=['original_message'])

            updated_message = await sync_to_async(
                self.conversation_service.finalize_ai_message_with_billing
            )(message_obj, ai_response, token_usage)
            if regenerate:
                updated_message.is_regenerated = True
                await database_sync_to_async(updated_message.save)(update_fields=['is_regenerated'])

            # Update bot budget if this is a public bot conversation
            if self.conversation.bot_id and self.conversation.user is None:
                await self._update_bot_budget(updated_message.cost)

            await self.send(await self._format_message(updated_message, streaming=False, regenerate=regenerate, generated_image=generated_image_data))
        except (ValidationError, DjangoValidationError) as e:
            logger.error(f"Validation error finalizing message: {str(e)}")
            await self.send_error("insufficient_balance", "Insufficient wallet balance", details={"error": str(e)})
        except Exception as e:
            logger.exception(f"Error finalizing message: {str(e)}")
            await self.send_error("finalize_error", "Failed to finalize message")

    async def _handle_insufficient_balance(self, message_obj: Message, ai_response: str, token_usage: Dict, error_response: Dict):
        """Handle insufficient balance during streaming."""
        message_obj.message = f"{ai_response}\n\n[Response cut off - insufficient credits]"
        message_obj.input_tokens = token_usage.get('input_tokens', 0)
        message_obj.output_tokens = token_usage.get('output_tokens', 0)
        if message_obj.llm and (message_obj.input_tokens or message_obj.output_tokens):
            llm = message_obj.llm
            input_rate = llm.input_token_rate_per_million / 1000000
            output_rate = llm.output_token_rate_per_million / 1000000
            cost = (message_obj.input_tokens * input_rate) + (message_obj.output_tokens * output_rate)
            message_obj.cost = cost
        await database_sync_to_async(message_obj.save)()
        await self.send(json.dumps(camelize({
            "type": "ai_stream",
            "id": str(message_obj.id),
            "message": message_obj.message,
            "senderName": "AI Assistant",
            "senderType": SenderType.AI_ASSISTANT,
            "isSender": False,
            "streaming": False,
            "regenerate": False,
            "date": message_obj.created_at.isoformat(),
            "cost": str(message_obj.cost) if message_obj.cost is not None else None,
            "inputTokens": message_obj.input_tokens,
            "outputTokens": message_obj.output_tokens,
        })))
        await self.send(json.dumps(error_response))

    async def load_conversation_history(self):
        """Fetches and sends conversation history to the frontend."""
        history = await self.conversation_service.fetch_chat_history_from_db(self.conversation)
        await self.send(json.dumps(camelize({"type": "conversation_history", "conversationHistory": history})))

    async def _generate_conversation_title(self, user_message: str):
        """Generate and send conversation title for the first message."""
        
        await database_sync_to_async(self.conversation.refresh_from_db)()

        if self.conversation.title not in (None, "", "New Chat"):
            return

        title = await self.conversation_service.generate_title(user_message)
        await self.conversation_service.update_conversation_title(self.conversation, title)
        await self.send(json.dumps(camelize({"type": "conversation_title", "title": title})))

    async def _format_message(self, message_obj: Message, is_sender: bool = False, streaming: bool = False, regenerate: bool = False, generated_image: Dict = None):
        """Format message for WebSocket response."""
        @database_sync_to_async
        def serialize_message():
            message = Message.active_objects.prefetch_related('files', 'tags', 'snippets__file').get(id=message_obj.id)
            return MessageSerializer(message).data

        serialized_data = await serialize_message()
        llm_id = await database_sync_to_async(lambda: getattr(message_obj.llm, 'id', None))()
        cost = await database_sync_to_async(lambda: message_obj.cost)()
        input_tokens = await database_sync_to_async(lambda: message_obj.input_tokens)()
        output_tokens = await database_sync_to_async(lambda: message_obj.output_tokens)()
        learning_progress_data = await database_sync_to_async(lambda: message_obj.learning_progress_data)()
        response = {
            "type": "message",
            "id": str(message_obj.id),
            "message": message_obj.message,
            "senderType": message_obj.sender_type,
            "senderName": message_obj.sender or "AI Assistant",
            "isSender": is_sender,
            "streaming": streaming,
            "regenerate": regenerate,
            "date": message_obj.created_at.isoformat(),
            "llmId": llm_id,
            "files": serialized_data.get("files", []),
            "tags": serialized_data.get("tags", []),
            "snippets": serialized_data.get("snippets", []),
            "feedbackType": message_obj.feedback_type,
            "feedbackText": message_obj.feedback_text,
            "isEdited": message_obj.is_edited,
            "isRegenerated": message_obj.is_regenerated,
            "originalMessage": message_obj.original_message,
            "cost": str(cost) if cost is not None else None,
            "inputTokens": input_tokens,
            "outputTokens": output_tokens,
            "learningProgressData": learning_progress_data or {},
            "generatedImage": generated_image
        }
        return json.dumps(camelize(response))

    async def _get_llm(self, llm_id: Optional[str], default: LLM = None) -> LLM:
        """Fetch LLM by ID or return default/first available."""
        if llm_id:
            llm = await database_sync_to_async(lambda: LLM.objects.filter(id=llm_id).first())()
            if llm:
                return llm
        return default or await database_sync_to_async(lambda: LLM.objects.first())()

    def _validate_message_data(self, data: Dict[str, Any], default_message: str = None) -> Dict[str, Any]:
        """Validate and extract message data."""
        return {
            "message": (data.get("message", default_message or "").strip()),
            "sender_type": data.get("sender_type", SenderType.PLAYER),
            "file_ids": data.get("file_ids", []),
            "embedding_ids": data.get("embedding_ids", []),
            "media_ids": data.get("media_ids", []),  # NEW: Media files (images/videos)
            "tag_ids": data.get("tag_ids", []),
            "folder_ids": data.get("folder_ids", []),
            "referenced_conversation_ids": data.get("referenced_conversation_ids", []),
            "llm_id": data.get("llm_id"),
            "prompt_id": data.get("prompt_id"),
            "temperature": data.get("temperature", self.DEFAULT_TEMPERATURE),
            "max_tokens": data.get("max_tokens", self.DEFAULT_MAX_TOKENS),
            "max_context_snippets": data.get("max_context_snippets", self.DEFAULT_MAX_CONTEXT_SNIPPETS),
            "document_similarity_threshold": data.get("document_similarity_threshold", self.DEFAULT_DOCUMENT_SIMILARITY_THRESHOLD),
            "history_limit": data.get("history_limit", self.DEFAULT_HISTORY_LIMIT),
            "web_search_enabled": data.get("web_search_enabled"),
            "image_generation_enabled": data.get("image_generation_enabled"),
            "image_generation_settings": data.get("image_generation_settings"),
            # Vision support: base64 encoded images
            "images": data.get("images", []),  # List of {preview: str, name: str, type: str}
            # Socratic-only optional fields
            "enable_progress": data.get("enable_progress"),
            "tracking_prompt": data.get("tracking_prompt", ""),
            "learning_goals": data.get("learning_goals", ""),
            "progress_llm_id": data.get("progress_llm_id"),
            "bot_meta": data.get("bot_meta", {}),
            "is_advanced": data.get("is_advanced", data.get("isAdvanced")),
        }

    async def send_error(self, code: str, message: str, details: Dict = None):
        """Send standardized error response."""
        error_response = {"error": code, "message": message}
        if details:
            error_response["details"] = details
        await self.send(json.dumps(error_response))

    async def _get_preceding_user_message(self, ai_message: Message) -> Optional[Message]:
        """Retrieve the preceding user message."""
        preceding_messages = await database_sync_to_async(
            lambda: list(Message.active_objects.filter(
                conversation=self.conversation, created_at__lt=ai_message.created_at
            ).order_by('-created_at'))
        )()
        return next((msg for msg in preceding_messages if msg.sender_type == SenderType.PLAYER), None)

    async def _run_learning_progress_stream(self, message_data: Dict[str, Any], ai_message: Message, default_llm: LLM):
        """
        Stream a learning-progress assessment after the main message.
        Uses tracking prompt and learning goals from message_data with DEFAULT_TRACKING_PROMPT as fallback.
        Also saves the assessment to DB and updates message.learning_progress_data.
        """
        try:
            tracking_prompt = (message_data.get("tracking_prompt") or "").strip()
            learning_goals = (message_data.get("learning_goals") or "").strip()

            if not tracking_prompt:
                tracking_prompt = self.DEFAULT_TRACKING_PROMPT
            if not learning_goals:
                learning_goals = "No specific learning goals defined."

            progress_llm_id = message_data.get("progress_llm_id")
            progress_llm = await self._get_llm(progress_llm_id, default=default_llm)

            accumulated = ""
            last_usage = None

            # Stream via LearningProgressService
            async for chunk, usage in self.learning_progress_service.assess_learning_progress(
                conversation=self.conversation,
                learning_goals=learning_goals,
                tracking_prompt=tracking_prompt,
                last_message=ai_message,
                llm=progress_llm,
                max_tokens=2048,
                temperature=0.7,
                conversation_history_limit=80,
                bot_meta=message_data.get("bot_meta") or {},
            ):
                if usage:
                    last_usage = usage
                    can_continue, _ = await self.billing_service.check_streaming_credit_usage(self.user, progress_llm, usage)
                    if not can_continue:
                        await self.send(json.dumps({
                            "type": "progress_error",
                            "message": "Insufficient credits during progress assessment"
                        }))
                        return

                if chunk:
                    accumulated += chunk
                    await self.send(json.dumps({
                        "type": "progress_stream",
                        "conversationId": str(self.conversation.id),
                        "messageId": str(ai_message.id),
                        "chunk": chunk
                    }))

            # Save assessment and update message metadata if we have content
            if accumulated.strip():
                # Build usage with totals for FE
                def _build_usage(u: Dict):
                    if not isinstance(u, dict):
                        return u
                    inp = u.get("input_tokens") or u.get("prompt_tokens") or 0
                    out = u.get("output_tokens") or u.get("completion_tokens") or 0
                    tot = (inp or 0) + (out or 0)
                    u_with_totals = dict(u)
                    u_with_totals["total_tokens"] = tot
                    return u_with_totals

                platform_label = self.platform or AuthSourceChoice.DARE
                if platform_label == AuthSourceChoice.SOCRATIC_BOTS:
                    platform_label = "SocraticBots"
                else:
                    platform_label = "DARE"

                metadata = {
                    "llm_model": getattr(progress_llm, "identifier", None),
                    "usage": _build_usage(last_usage),
                    "platform": platform_label,
                    "tracking_prompt_used": tracking_prompt[:100] + "..." if len(tracking_prompt) > 100 else tracking_prompt,
                }
                assessment = await self.learning_progress_service._save_progress_assessment(
                    conversation=self.conversation,
                    content=accumulated,
                    learning_goals=learning_goals,
                    last_message=ai_message,
                    metadata=metadata,
                )

                # Update AI message with learning progress data
                def _update_msg():
                    ai_message.learning_progress_data = {
                        "progress_assessment_id": str(getattr(assessment, "id", "")),
                        "learning_goals": learning_goals,
                        "tracking_prompt": tracking_prompt,
                        "llm_id": getattr(progress_llm, "id", None),
                        "input_tokens": (last_usage or {}).get("input_tokens"),
                        "output_tokens": (last_usage or {}).get("output_tokens"),
                        "status": "completed",
                    }
                    ai_message.save(update_fields=["learning_progress_data"])
                    return ai_message

                ai_message = await database_sync_to_async(_update_msg)()

            # Completion notification
            meta = {
                "type": "progress_complete",
                "conversationId": str(self.conversation.id),
                "messageId": str(ai_message.id),
            }
            if last_usage:
                meta.update({
                    "inputTokens": last_usage.get("input_tokens"),
                    "outputTokens": last_usage.get("output_tokens"),
                })
            await self.send(json.dumps(meta))

        except Exception as e:
            logger.exception(f"Learning progress stream error: {e}")
            await self.send(json.dumps({
                "type": "progress_error",
                "message": "Failed to generate learning progress"
            }))

    async def send_latest_progress(self):
        """Fetch and send the latest learning progress assessment to the client."""
        try:
            latest = await self.learning_progress_service.get_latest_assessment(self.conversation)
            payload = {
                "type": "latest_progress",
                "conversationId": str(self.conversation.id),
                "assessment": latest  # None or dict
            }
            await self.send(json.dumps(camelize(payload)))
        except Exception as e:
            logger.exception(f"Error sending latest progress: {e}")
            # Non-fatal; do not close socket
            await self.send(json.dumps(camelize({
                "type": "latest_progress",
                "conversationId": str(self.conversation.id),
                "assessment": None
            })))

    async def _update_bot_budget(self, cost):
        """
        Update Socratic Bot budget for public bot conversations.
        Makes HTTP call to Socratic Books backend (runs in thread pool to avoid blocking).
        """
        if cost is None or cost == 0:
            return

        # Run in thread pool to avoid blocking the async event loop
        await sync_to_async(self._update_bot_budget_sync)(cost)

    def _update_bot_budget_sync(self, cost):
        """
        Synchronous budget update to Socratic Books backend.
        Called via sync_to_async to run in thread pool.
        """
        try:
            socratic_backend_url = os.getenv('SOCRATIC_BOTS_BACKEND_URL', 'http://localhost:8001')
            url = f"{socratic_backend_url}/api/bots/internal/update-budget/"

            data = {
                'bot_id': self.conversation.bot_id,
                'cost': float(cost)
            }

            response = requests.post(url, json=data, timeout=5)

            if response.status_code == 200:
                logger.info(f"Updated budget for bot {self.conversation.bot_id}: +${cost}")
            else:
                logger.error(f"Failed to update bot budget: HTTP {response.status_code}")

        except requests.Timeout:
            logger.error(f"Timeout updating bot budget for bot {self.conversation.bot_id}")
        except Exception as e:
            logger.exception(f"Error updating bot budget: {str(e)}")
            # Non-fatal - don't block the conversation


class PublicBotConsumer(ChatConsumer):
    """
    WebSocket consumer for public bot conversations (no user authentication required).
    Uses anonymous_session_id instead of user for validation.
    Inherits all chat functionality from ChatConsumer.
    """

    async def connect(self):
        """Initialize WebSocket connection for public bot (no auth required)."""
        try:
            # Get conversation_id from URL
            self.conversation_id = self.scope["url_route"]["kwargs"].get("conversation_id")

            # Get session_id from query string
            query_string = self.scope.get('query_string', b'').decode()
            params = dict(param.split('=') for param in query_string.split('&') if '=' in param)
            session_id = params.get('session_id')

            if not session_id:
                logger.warning(f"No session_id provided for public bot conversation {self.conversation_id}")
                raise DenyConnection("session_id is required")

            # Get conversation and validate it belongs to this session
            self.conversation = await self.conversation_service.get_conversation_by_id(self.conversation_id)

            if not self.conversation:
                logger.warning(f"Invalid conversation_id: {self.conversation_id}")
                raise DenyConnection("Invalid conversation_id")

            # Verify this conversation belongs to this anonymous session
            if self.conversation.anonymous_session_id != session_id:
                logger.warning(f"Session mismatch for conversation {self.conversation_id}")
                raise DenyConnection("Invalid session for this conversation")

            # Verify conversation has no user (is public)
            if self.conversation.user is not None:
                logger.warning(f"Conversation {self.conversation_id} is not a public conversation")
                raise DenyConnection("Not a public conversation")

            # Set user to None for public conversations
            self.user = None

            # Detect platform from ASGI scope headers
            self.platform = detect_platform_from_scope(self.scope)

            await self.accept()
            await self.load_conversation_history()

        except DenyConnection as e:
            logger.error(f"Public bot connection denied: {str(e)}")
            await self.close(code=4000)
        except Exception as e:
            logger.exception(f"Error during public bot connect: {str(e)}")
            await self.close(code=4001)

    async def handle_new_message(self, data: Dict[str, Any]):
        """Process new user message for public bot (skip billing checks)."""
        try:
            message_data = self._validate_message_data(data)
            llm = await self._get_llm(message_data.get("llm_id"))

            # Skip billing check for public bots - bot budget will be updated instead

            message_obj = await self.conversation_service.create_message(
                self.conversation,
                message_data["sender_type"],
                message_data["message"],
                "Anonymous User"
            )
            await self.send(await self._format_message(message_obj, is_sender=True))

            if await self.conversation_service.is_first_message(self.conversation):
                asyncio.create_task(self._generate_conversation_title(message_data["message"]))

            bot_message_obj = await self.conversation_service.create_message(
                self.conversation,
                SenderType.AI_ASSISTANT,
                "",
                "AI Assistant",
                message_data["file_ids"],
                message_data["tag_ids"],
                message_data["embedding_ids"],
                llm
            )
            await self.send(await self._format_message(bot_message_obj, streaming=True))

            await self._stream_ai_response(message_data, bot_message_obj, llm)
        except ValidationError as e:
            await self.send_error("validation_error", str(e))
        except Exception as e:
            logger.exception(f"Error in handle_new_message (public): {str(e)}")
            await self.send_error("ai_response_error", "Failed to generate AI response")

    async def handle_regenerate_response(self, data: Dict[str, Any]):
        """Regenerate an AI response for public bot (skip billing checks)."""
        try:
            message_id = data.get("message_id")
            if not message_id:
                await self.send_error("missing_data", "Missing message_id")
                return

            ai_message = await database_sync_to_async(
                lambda: Message.active_objects.select_related('llm').filter(
                    id=message_id, sender_type=SenderType.AI_ASSISTANT
                ).first()
            )()
            if not ai_message:
                await self.send_error("invalid_message", "AI message not found")
                return

            preceding_user_message = await self._get_preceding_user_message(ai_message)
            if not preceding_user_message:
                await self.send_error("no_user_message", "No preceding user message found")
                return

            llm = await self._get_llm(data.get("llm_id"), default=ai_message.llm)

            # Skip billing check for public bots

            message_data = self._validate_message_data(data, default_message=preceding_user_message.message)
            await self._stream_ai_response(message_data, ai_message, llm, regenerate=True)
        except Exception as e:
            logger.exception(f"Error in handle_regenerate_response (public): {str(e)}")
            await self.send_error("regenerate_error", "Failed to regenerate response")

    async def _stream_ai_response(self, message_data: Dict[str, Any], message_obj: Message, llm: LLM, regenerate: bool = False):
        """Stream AI response for public bot (skip billing checks during streaming)."""
        try:
            bot_message_id = str(message_obj.id)
            ai_response_accumulator = ""
            token_usage = None
            generated_image_data = None

            # Build LLM query request using DTO builder
            request = LLMQueryRequestBuilder.from_message_data(
                message=message_data["message"],
                conversation=self.conversation,
                user=None,  # No user for public bots
                message_data=message_data,
                llm=llm,
                message_obj=message_obj,
                platform=self.platform,
            )

            async for chunk, usage in self.llm_service.query(request):
                if usage:
                    token_usage = usage

                    # Handle generated image (if supported)
                    if usage.get("image_bytes"):
                        generated_file = await self._save_generated_image_public(
                            image_bytes=usage["image_bytes"],
                            prompt=message_data["message"],
                            metadata=usage
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

                    # Skip billing check during streaming for public bots

                if chunk and chunk.strip():
                    ai_response_accumulator += chunk
                    payload = {
                        "type": "ai_stream",
                        "id": bot_message_id,
                        "message": ai_response_accumulator,
                        "senderName": "AI Assistant",
                        "senderType": SenderType.AI_ASSISTANT,
                        "isSender": False,
                        "streaming": True,
                        "regenerate": regenerate,
                        "date": message_obj.created_at.isoformat(),
                    }
                    await self.send(json.dumps(camelize(payload)))

            if ai_response_accumulator.strip():
                await self._finalize_message_public(message_obj, ai_response_accumulator, token_usage, regenerate, generated_image_data)
                # Skip learning progress for public bots
        except Exception as e:
            logger.exception(f"Error streaming AI response (public): {str(e)}")
            await self.send_error("stream_error", "Failed to stream AI response")

    async def _finalize_message_public(self, message_obj: Message, ai_response: str, token_usage: Dict, regenerate: bool, generated_image_data: Dict = None):
        """Finalize AI message for public bot (skip billing, update bot budget)."""
        try:
            if regenerate and not message_obj.original_message:
                message_obj.original_message = message_obj.message
                await database_sync_to_async(message_obj.save)(update_fields=['original_message'])

            # Update message without billing
            message_obj.message = ai_response
            if token_usage:
                message_obj.input_tokens = token_usage.get('input_tokens', 0)
                message_obj.output_tokens = token_usage.get('output_tokens', 0)

                # Calculate cost
                if message_obj.llm and (message_obj.input_tokens or message_obj.output_tokens):
                    llm = message_obj.llm
                    input_rate = llm.input_token_rate_per_million / 1000000
                    output_rate = llm.output_token_rate_per_million / 1000000
                    cost = (message_obj.input_tokens * input_rate) + (message_obj.output_tokens * output_rate)
                    message_obj.cost = cost

            if regenerate:
                message_obj.is_regenerated = True

            await database_sync_to_async(message_obj.save)()

            # Update bot budget if this is a public bot conversation
            if self.conversation.bot_id and self.conversation.user is None and message_obj.cost:
                await self._update_bot_budget(message_obj.cost)

            await self.send(await self._format_message(message_obj, streaming=False, regenerate=regenerate, generated_image=generated_image_data))
        except Exception as e:
            logger.exception(f"Error finalizing message (public): {str(e)}")
            await self.send_error("finalize_error", "Failed to finalize message")

    @database_sync_to_async
    def _save_generated_image_public(self, image_bytes: bytes, prompt: str, metadata: Dict) -> Optional[File]:
        """Save AI-generated image for public bot (no user)."""
        try:
            from datetime import datetime

            # Generate filename
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"dalle_public_{timestamp}.png"

            # Create File object without user
            file_obj = File(
                user=None,  # No user for public bots
                name=filename,
                file_type="image/png",
                size=len(image_bytes),
                status=FileStatus.PROCESSED,
                is_media=True,
                media_type='generated_image',
                is_generated=True,
                generation_prompt=prompt,
                revised_prompt=metadata.get('revised_prompt', ''),
                generation_params={
                    'model': metadata.get('model', 'dall-e-3'),
                    'size': metadata.get('size', '1024x1024'),
                    'quality': metadata.get('quality', 'standard'),
                    'style': metadata.get('style', 'vivid'),
                }
            )

            # Save the image file
            file_obj.file.save(filename, ContentFile(image_bytes), save=False)
            file_obj.save()

            logger.info(f"Saved generated image as File ID: {file_obj.id} (public bot)")
            return file_obj

        except Exception as e:
            logger.exception(f"Error saving generated image (public): {str(e)}")
            return None