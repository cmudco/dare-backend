import json
from decimal import Decimal
from typing import Dict, Optional
from django.db import models
from django.core.exceptions import ValidationError
from channels.db import database_sync_to_async
from django.db.models import Prefetch
from conversations.models import LLM, Message, Conversation, Artifact
from core.services.openai_service import OpenAIService
from core.services.api_key_service import get_provider_api_key
from conversations.constants import SenderType
from conversations.api.serializers import MessageSerializer
from djangorestframework_camel_case.util import camelize
from files.models import File, Tag
from core.services.billing_service import BillingService
from users.models import User

class ConversationService:
    """Handles conversation metadata and message management."""

    async def fetch_chat_history_from_db(self, conversation: Conversation, limit: int = 50):
        """Fetches recent chat history for AI context."""

        messages = await database_sync_to_async(
            lambda: list(
                Message.active_objects.filter(conversation=conversation)
                .select_related('llm')
                .prefetch_related(
                    'snippets', 'files', 'tags', 'web_search_sources', 'mcp_tool_calls',
                    # Only prefetch active artifacts to match what serializer expects
                    Prefetch('artifacts', queryset=Artifact.active_objects.all())
                )
                .order_by('-created_at')[:limit]
            )
        )()

        serialized_messages = await database_sync_to_async(
            lambda: MessageSerializer(reversed(messages), many=True).data
        )()

        user_email = await self.get_user_email(conversation)

        history = [
            {
                "id": msg["id"],
                "message": msg["message"],
                "sender": msg["sender_name"],
                "sender_type": msg["sender_type"],
                "created_at": msg["created_at"],
                "llm": msg["llm"],
                "files": msg.get("files", []),
                "tags": msg.get("tags", []),
                "snippets": msg.get("snippets", []),
                "webSearchSources": msg.get("web_search_sources", []),
                "feedbackType": msg.get("feedback_type", None),
                "feedbackText": msg.get("feedback_text", None),
                "isEdited": msg.get("is_edited", False),
                "isRegenerated": msg.get("is_regenerated", False),
                "originalMessage": msg.get("original_message", None),
                "cost": msg.get("cost", None),
                "inputTokens": msg.get("input_tokens", None),
                "outputTokens": msg.get("output_tokens", None),
                "energyWh": msg.get("energy_wh", None),
                "carbonG": msg.get("carbon_g", None),
                "waterMl": msg.get("water_ml", None),
                "energyStats": msg.get("energy_stats", None),
                "artifactId": msg.get("artifactId", None),
                "toolCalls": [
                    self._build_tool_call_payload(tc)
                    for tc in msg.get("mcp_tool_calls", [])
                ],
            }
            for msg in serialized_messages
        ]
        return camelize(history)

    def _build_tool_call_payload(self, tc: dict) -> dict:
        """
        Build a properly typed tool call payload for FE.
        
        Separates DARE results from MCP results into different fields
        for clean, zero-confusion typing on FE side.
        """
        server_slug = tc["server_slug"]
        parsed_result = self._parse_tool_result(tc.get("result"))
        
        payload = {
            "id": tc["tool_call_id"],
            "toolName": tc["tool_name"],
            "serverSlug": server_slug,
            "status": tc["status"],
            "error": tc.get("error"),
        }
        
        # Route to correct field based on server
        if server_slug == "dare":
            payload["dareResult"] = parsed_result
        else:
            payload["mcpResult"] = parsed_result
            
        return payload

    def _parse_tool_result(self, result: str):
        """
        Parse tool result JSON string and camelize for FE.

        The result is stored as a JSON string in the database, but FE
        expects a properly camelCased object - no parsing needed on FE side.
        """
        if not result:
            return None
        try:
            parsed = json.loads(result)
            return camelize(parsed)
        except (json.JSONDecodeError, TypeError):
            # If parsing fails, return as-is (shouldn't happen normally)
            return result

    async def get_user_email(self, conversation: Conversation) -> str:
        """Fetch user email associated with the conversation."""
        return await database_sync_to_async(lambda: getattr(conversation.user, 'email', ''))()

    async def create_message(
        self, conversation: Conversation, sender_type: str, message_content: str,
        sender: str = None, file_ids: list = None, tag_ids: list = None, 
        embedding_ids: list = None, llm: LLM = None
    ) -> Message:
        """Create a new message with file attachments and tags."""
        message = await database_sync_to_async(
            lambda: Message.active_objects.create(
                conversation=conversation,
                sender_type=sender_type,
                message=message_content,
                sender=sender,
                llm=llm,
                cost=Decimal('0.000000') if sender_type == SenderType.PLAYER else None
            )
        )()

        all_file_ids = list(set((file_ids or []) + (embedding_ids or [])))

        if all_file_ids:
            # For forked conversations, allow files from both current user and original owner
            def _get_accessible_files():
                allowed_user_ids = [conversation.user_id]
                if conversation.file_owner_id:
                    allowed_user_ids.append(conversation.file_owner_id)
                return list(File.active_objects.filter(
                    pk__in=all_file_ids,
                    user_id__in=allowed_user_ids
                ))
            files = await database_sync_to_async(_get_accessible_files)()
            if files:
                await database_sync_to_async(lambda: message.files.add(*files))()

        if tag_ids:
            tags = await database_sync_to_async(
                lambda: list(Tag.objects.filter(pk__in=tag_ids, user=conversation.user))
            )()
            if tags:
                await database_sync_to_async(lambda: message.tags.add(*tags))()

        return message

    async def get_conversation(self, conversation_id: str, user: 'User') -> Optional[Conversation]:
        """Retrieve a conversation by ID for the given user."""
        return await database_sync_to_async(
            lambda: Conversation.active_objects.filter(conversation_id=conversation_id, user=user).first()
        )()

    async def get_conversation_by_id(self, conversation_id: str) -> Optional[Conversation]:
        """Retrieve a conversation by ID (no user filter, for public bots)."""
        return await database_sync_to_async(
            lambda: Conversation.active_objects.filter(conversation_id=conversation_id).first()
        )()

    async def is_first_message(self, conversation: Conversation) -> bool:
        """Check if this is the first message in the conversation."""
        count = await database_sync_to_async(
            lambda: Message.active_objects.filter(conversation=conversation).count()
        )()
        return count <= 2

    async def update_conversation_title(self, conversation: Conversation, title: str):
        """Update the conversation title."""
        await database_sync_to_async(
            lambda: Conversation.active_objects.filter(id=conversation.id).update(title=title)
        )()

    async def generate_title(self, user_message: str, ai_response: str = "") -> str:
        """Generate a concise conversation title."""
        messages = [
            {
                "role": "system",
                "content": "Generate a short, descriptive conversation title (max 6 words)."
            },
            {
                "role": "user",
                "content": f"Title for: User: {user_message}\nAI: {ai_response}"
            }
        ]

        llm = await self.get_gpt_35_turbo_model()
        api_key = await get_provider_api_key(llm.provider)
        ai_service = OpenAIService(llm=llm, api_key=api_key)
        try:
            return await ai_service.get_chat_completion(messages)
        except Exception as e:
            return "New Chat"

    async def get_gpt_35_turbo_model(self) -> LLM:
        """Fetch the gpt-3.5-turbo LLM."""
        llm = await database_sync_to_async(
            lambda: LLM.objects.filter(identifier="gpt-3.5-turbo", provider="openai").first()
        )()
        return llm or await database_sync_to_async(lambda: LLM.objects.filter(provider="openai").first())()

    async def get_latest_user_message(self, conversation: Conversation) -> Optional[Message]:
        """Retrieve the latest user message."""
        return await database_sync_to_async(
            lambda: Message.active_objects.filter(
                conversation=conversation, sender_type=SenderType.PLAYER
            ).order_by('-created_at').first()
        )()

    async def edit_message(self, message_id: str, new_content: str, conversation: Conversation) -> Message:
        """Edit the latest user message."""
        message = await database_sync_to_async(
            lambda: Message.active_objects.get(id=message_id)
        )()
        latest_user_message = await self.get_latest_user_message(conversation)
        if not latest_user_message or str(latest_user_message.id) != message_id:
            raise ValueError("Can only edit the latest user message")

        if not message.is_edited:
            message.original_message = message.message
            message.is_edited = True
        message.message = new_content
        await database_sync_to_async(message.save)()
        return message

    def finalize_ai_message_with_billing(self, message_obj: Message, ai_response: str, token_usage: Dict) -> Message:
        """Finalize AI message with billing (delegated to BillingService)."""
        billing_service = BillingService()
        return billing_service.finalize_ai_message(message_obj, ai_response, token_usage)