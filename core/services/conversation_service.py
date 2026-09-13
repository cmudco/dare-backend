import logging
from decimal import Decimal
from typing import Dict, Optional

from channels.db import database_sync_to_async
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Prefetch
from djangorestframework_camel_case.util import camelize

from conversations.api.serializers import MessageSerializer
from conversations.constants import SenderType
from conversations.models import LLM, Artifact, Conversation, Message
from core.services.background_model_service import BackgroundModelService
from core.services.billing_service import BillingService
from core.services.dtos import LLMDescriptor
from files.models import File, Tag
from users.models import User

logger = logging.getLogger(__name__)


class ConversationService:
    """Handles conversation metadata and message management."""

    async def fetch_chat_history_from_db(
        self, conversation: Conversation, limit: int = 50
    ):
        """Recent messages for the socket ``conversation_history`` event.

        The rows are the REST ``MessageSerializer`` output, camelized. The
        socket transport carries the same message contract as the messages
        API, so a field added to the serializer reaches both clients without a
        second, hand-maintained field list.
        """

        messages = await database_sync_to_async(
            lambda: list(
                Message.active_objects.filter(conversation=conversation)
                .select_related("llm")
                .prefetch_related(
                    "snippets",
                    "files",
                    "tags",
                    "web_search_sources",
                    "mcp_tool_calls",
                    # Only prefetch active artifacts to match what serializer expects
                    Prefetch("artifacts", queryset=Artifact.active_objects.all()),
                )
                .order_by("-created_at")[:limit]
            )
        )()

        serialized_messages = await database_sync_to_async(
            lambda: MessageSerializer(reversed(messages), many=True).data
        )()
        return camelize(serialized_messages)

    async def create_message(
        self,
        conversation: Conversation,
        sender_type: str,
        message_content: str,
        sender: str = None,
        file_ids: list = None,
        tag_ids: list = None,
        embedding_ids: list = None,
        descriptor: Optional[LLMDescriptor] = None,
    ) -> Message:
        """Create a new message with file attachments and tags.

        Persists the dispatch target via three discriminated fields (rules.md
        §11): real-LLM messages set ``llm``; LiteLLM-routed messages leave
        ``llm`` NULL and set ``litellm_key`` + ``litellm_model_name``. User
        messages typically pass no descriptor (all three NULL).
        """
        if descriptor is not None and descriptor.is_synthetic:
            llm_fk = None
            litellm_key = descriptor.litellm_key
            litellm_model_name = descriptor.litellm_model_name
        elif descriptor is not None:
            llm_fk = descriptor.llm
            litellm_key = None
            litellm_model_name = None
        else:
            llm_fk = None
            litellm_key = None
            litellm_model_name = None

        message = await database_sync_to_async(
            lambda: Message.active_objects.create(
                conversation=conversation,
                sender_type=sender_type,
                message=message_content,
                sender=sender,
                llm=llm_fk,
                litellm_key=litellm_key,
                litellm_model_name=litellm_model_name,
                cost=Decimal("0.000000") if sender_type == SenderType.PLAYER else None,
            )
        )()

        all_file_ids = list(set((file_ids or []) + (embedding_ids or [])))

        if all_file_ids:
            # For forked conversations, allow files from both current user and original owner
            def _get_accessible_files():
                allowed_user_ids = [conversation.user_id]
                if conversation.file_owner_id:
                    allowed_user_ids.append(conversation.file_owner_id)
                return list(
                    File.active_objects.filter(
                        pk__in=all_file_ids, user_id__in=allowed_user_ids
                    )
                )

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

    async def get_conversation(
        self, conversation_id: str, user: "User"
    ) -> Optional[Conversation]:
        """Retrieve a conversation by ID for the given user."""
        return await database_sync_to_async(
            lambda: Conversation.active_objects.filter(
                conversation_id=conversation_id, user=user
            ).first()
        )()

    async def get_conversation_by_id(
        self, conversation_id: str
    ) -> Optional[Conversation]:
        """Retrieve a conversation by ID (no user filter, for public bots)."""
        return await database_sync_to_async(
            lambda: Conversation.active_objects.filter(
                conversation_id=conversation_id
            ).first()
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
            lambda: Conversation.active_objects.filter(id=conversation.id).update(
                title=title
            )
        )()

    async def generate_title(
        self,
        user_message: str,
        ai_response: str = "",
        user: Optional[User] = None,
        public_bot_id: Optional[int] = None,
    ) -> str:
        """Generate a concise title with the user's resolved background model."""
        messages = [
            {
                "role": "system",
                "content": (
                    "Generate a short, descriptive conversation title of at "
                    "most 6 words. Reply with ONLY the title itself - no "
                    "quotes, no markdown, no explanation, no punctuation at "
                    "the end."
                ),
            },
            {
                "role": "user",
                "content": f"Title for: User: {user_message}\nAI: {ai_response}",
            },
        ]

        try:
            result = await BackgroundModelService().complete_text(
                user=user,
                messages=messages,
                description="Conversation title generation",
                max_tokens=80,
                public_bot_id=public_bot_id,
            )
            return self._sanitize_title(result.value)
        except Exception:
            logger.exception("Conversation title generation failed")
            return "New Chat"

    @staticmethod
    def _sanitize_title(raw: str) -> str:
        """Normalize a model-generated title for the 255-char DB column.

        Models (especially small ones) sometimes return markdown headers,
        surrounding quotes, or whole paragraphs; unsanitized output crashed
        the save with StringDataRightTruncation and left conversations
        untitled.
        """
        if not raw:
            return "New Chat"
        first_line = raw.strip().splitlines()[0]
        title = first_line.strip().lstrip("#*- ").strip().strip("\"'`").strip()
        # Provider failures surface as error text in the aggregated stream
        # rather than exceptions; never let that become a title.
        if title.lower().startswith("error"):
            return "New Chat"
        if len(title) > 120:
            title = title[:119].rstrip() + "…"
        return title or "New Chat"

    async def get_latest_user_message(
        self, conversation: Conversation
    ) -> Optional[Message]:
        """Retrieve the latest user message."""
        return await database_sync_to_async(
            lambda: Message.active_objects.filter(
                conversation=conversation, sender_type=SenderType.PLAYER
            )
            .order_by("-created_at")
            .first()
        )()

    async def edit_message(
        self, message_id: str, new_content: str, conversation: Conversation
    ) -> Message:
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

    def finalize_ai_message_with_billing(
        self, message_obj: Message, ai_response: str, token_usage: Dict
    ) -> Message:
        """Finalize AI message with billing (delegated to BillingService)."""
        billing_service = BillingService()
        return billing_service.finalize_ai_message(
            message_obj, ai_response, token_usage
        )
