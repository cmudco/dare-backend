from decimal import Decimal
from django.db import models, transaction as db_transaction
from channels.db import database_sync_to_async
from billing.constants import TransactionTypeChoice
from billing.models import Transaction
from conversations.models import LLM, Message, Conversation
from core.services.openai_service import OpenAIService
from conversations.constants import SenderType
from asgiref.sync import sync_to_async
from conversations.api.serializers import MessageSerializer
from djangorestframework_camel_case.util import camelize

class ConversationService:
    """Handles conversation metadata like title generation and message management."""

    async def fetch_chat_history_from_db(self, conversation):
        """Fetches recent chat history for AI context, including snippets."""
        messages = await database_sync_to_async(
            lambda: list(
                Message.active_objects.filter(conversation=conversation)
                .select_related('llm')
                .prefetch_related('snippets')
                .order_by('-created_at')
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
                "date": msg["created_at"],
                "isSender": msg["sender_name"] == user_email,
                "llmId": msg["llm"],
                "snippets": msg.get("snippets", []),
                "is_liked": msg.get("is_liked", False),
                "is_disliked": msg.get("is_disliked", False),
                "isEdited": msg.get("is_edited", False),
                "isRegenerated": msg.get("is_regenerated", False),
                "originalMessage": msg.get("original_message", None),
            }
            for msg in serialized_messages
        ]

        return camelize(history)

    async def get_user_email(self, conversation):
        """Safely fetch the email of the user associated with the conversation."""
        return await database_sync_to_async(
            lambda: getattr(conversation.user, 'email', '')
        )()

    async def update_message(self, message_id, new_content):
        """Update an existing AI-generated message with the final response."""
        await database_sync_to_async(
            lambda: Message.active_objects.filter(id=message_id).update(message=new_content)
        )()

    async def create_message(self, conversation, sender_type, message_content, sender=None, file_ids=None, user=None, llm=None):
        """Create a new message with specified sender information and file attachments."""
        message = await database_sync_to_async(
            lambda: Message.active_objects.create(
                conversation=conversation,
                sender_type=sender_type,
                message=message_content,
                sender=sender,
                llm=llm
            )
        )()

        if file_ids and user:
            files = await database_sync_to_async(
                lambda: list(user.files.filter(pk__in=file_ids))
            )()
            if files:
                await database_sync_to_async(
                    lambda: message.files.add(*files)
                )()

        return message

    async def get_conversation(self, conversation_id, user):
        """Retrieve an existing chat conversation, return None if not found."""
        return await database_sync_to_async(
            lambda: Conversation.active_objects.filter(conversation_id=conversation_id, user=user).first()
        )()

    async def is_first_message(self, conversation):
        """Check if this is the first message exchange in the conversation."""
        count = await database_sync_to_async(
            lambda: Message.active_objects.filter(conversation=conversation).count()
        )()
        return count <= 2

    async def update_conversation_title(self, conversation, title):
        """Update the conversation title."""
        await database_sync_to_async(
            lambda: Conversation.active_objects.filter(id=conversation.id).update(title=title)
        )()

    async def get_gpt_35_turbo_model(self):
        """Fetch the LLM object for gpt-3.5-turbo from the database."""
        llm = await database_sync_to_async(
            lambda: LLM.objects.filter(identifier="gpt-3.5-turbo", provider="openai").first()
        )()
        if not llm:
            llm = await database_sync_to_async(
                lambda: LLM.objects.filter(provider="openai").first()
            )()
        return llm

    async def generate_title(self, user_message, ai_response=""):
        """Generate a short, descriptive conversation title (max 6 words)."""
        messages = [
            {
                "role": "system",
                "content": "You are an assistant that generates short, descriptive conversation titles. "
                           "Keep the title concise, meaningful, and strictly 6 words or fewer."
            },
            {
                "role": "user",
                "content": f"Generate a short title (max 6 words) based on this conversation:\n"
                           f"User: {user_message}\nAI: {ai_response}\n\n"
                           f"Response must be at most 6 words long."
            }
        ]

        llm = await self.get_gpt_35_turbo_model()
        ai_service = OpenAIService(llm=llm)

        try:
            return await ai_service.get_chat_completion(messages)
        except Exception:
            return "New Chat"

    async def get_latest_user_message(self, conversation):
        """Retrieve the latest user message for the given conversation."""
        return await database_sync_to_async(
            lambda: Message.active_objects.filter(
                conversation=conversation,
                sender_type=SenderType.PLAYER
            ).order_by('-created_at').first()
        )()

    async def get_latest_ai_message(self, conversation):
        """Retrieve the latest AI message for the given conversation."""
        return await database_sync_to_async(
            lambda: Message.active_objects.filter(
                conversation=conversation,
                sender_type=SenderType.AI_ASSISTANT
            ).order_by('-created_at').first()
        )()

    async def edit_message(self, message_id, new_content, conversation):
        """Edit the latest user message with new content."""
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

    async def regenerate_message(self, message_id, new_content, conversation):
        """Regenerate the latest AI message with new content."""
        message = await database_sync_to_async(
            lambda: Message.active_objects.get(id=message_id)
        )()
        latest_ai_message = await self.get_latest_ai_message(conversation)
        if not latest_ai_message or str(latest_ai_message.id) != message_id:
            raise ValueError("Can only regenerate the latest AI message")

        if not message.is_regenerated:
            message.original_message = message.message
            message.is_regenerated = True
        message.message = new_content
        await database_sync_to_async(message.save)()
        return message

    def finalize_ai_message_with_billing(self, message_obj, ai_response, token_usage):
        try:
            with db_transaction.atomic():
                message_obj.message = ai_response
                if token_usage:
                    message_obj.input_tokens = token_usage.get("input_tokens", 0)
                    message_obj.output_tokens = token_usage.get("output_tokens", 0)

                llm = message_obj.llm
                if llm and message_obj.input_tokens is not None and message_obj.output_tokens is not None:
                    input_rate_per_token = llm.input_token_rate_per_million / Decimal('1000000')
                    output_rate_per_token = llm.output_token_rate_per_million / Decimal('1000000')

                    input_cost = Decimal(message_obj.input_tokens) * input_rate_per_token
                    output_cost = Decimal(message_obj.output_tokens) * output_rate_per_token
                    total_cost = input_cost + output_cost

                    amount = total_cost

                    if amount > Decimal('0.00'):
                        billing_transaction = Transaction(
                            user=message_obj.conversation.user,
                            message=f"LLM usage for message {message_obj.id}: {message_obj.input_tokens} input tokens, {message_obj.output_tokens} output tokens",
                            amount=amount,
                            type=TransactionTypeChoice.DEBIT
                        )
                        billing_transaction.save()

                message_obj.save()
        except Exception as e:
                        raise

    async def check_user_has_sufficient_credits(self, user, llm, estimated_input_tokens=500, estimated_output_tokens=1000):
        """
        Check if a user has sufficient wallet balance for an estimated conversation.
        This is a pre-check to avoid generating content that can't be billed.

        Args:
            user: The user making the request
            llm: The LLM model to be used
            estimated_input_tokens: Estimated number of input tokens (default: 500)
            estimated_output_tokens: Estimated number of output tokens (default: 1000)

        Returns:
            tuple: (bool, dict) - (has_sufficient_funds, error_details)
        """
        try:
            wallet = await database_sync_to_async(lambda: user.wallet)()

            if not wallet:
                return False, {
                    "error": "wallet_not_found",
                    "message": "User wallet not found"
                }

            balance = await database_sync_to_async(lambda: wallet.balance)()

            input_rate_per_token = llm.input_token_rate_per_million / Decimal('1000000')
            output_rate_per_token = llm.output_token_rate_per_million / Decimal('1000000')

            estimated_input_cost = Decimal(estimated_input_tokens) * input_rate_per_token
            estimated_output_cost = Decimal(estimated_output_tokens) * output_rate_per_token
            estimated_total_cost = estimated_input_cost + estimated_output_cost

            estimated_amount = estimated_total_cost

            if balance < estimated_amount.quantize(Decimal('0.01')):
                                return False, {
                    "error": "insufficient_credits",
                    "message": "You've run out of credits. Please add more to continue.",
                    "current_balance": str(balance),
                    "required_amount": str(estimated_amount)
                }

            return True, {}

        except Exception as e:
                        return False, {
                "error": "credit_check_error",
                "message": "An error occurred while checking your credits"
            }