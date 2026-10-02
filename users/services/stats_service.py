"""Usage counters shown on the dashboard."""

from typing import Dict

from django.db.models import Sum

from billing.constants import TransactionTypeChoice
from billing.models import Transaction
from conversations.constants import SenderType
from conversations.models import Conversation, Message
from files.models import File
from prompts.models import Prompt


def get_user_stats(user) -> Dict[str, int]:
    """Counts of the user's prompts, files, chats and lifetime tokens."""
    # Every debited call, including proxy-routed ones. Those carry no
    # ``llm`` row, and filtering them out here understated the token
    # counts for anyone on a LiteLLM key. Tokens are a count, not money —
    # unlike cost, they are the same quantity whoever paid for the call.
    token_stats = Transaction.objects.filter(
        user=user,
        type=TransactionTypeChoice.DEBIT,
    ).aggregate(
        total_input_tokens=Sum("input_tokens"),
        total_output_tokens=Sum("output_tokens"),
    )
    input_tokens = token_stats["total_input_tokens"] or 0
    output_tokens = token_stats["total_output_tokens"] or 0
    return {
        "prompt_count": Prompt.active_objects.filter(user=user).count(),
        "file_count": File.active_objects.filter(user=user).count(),
        "conversation_count": Conversation.active_objects.filter(user=user).count(),
        "message_count": Message.active_objects.filter(conversation__user=user).count(),
        "ai_message_count": Message.active_objects.filter(
            conversation__user=user, sender_type=SenderType.AI_ASSISTANT
        ).count(),
        "tagged_files_count": File.active_objects.filter(
            user=user, tags__isnull=False
        ).count(),
        "total_input_tokens": input_tokens,
        "total_output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }
