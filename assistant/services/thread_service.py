"""Thread lifecycle and turn bookkeeping for the platform assistant."""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Max, OuterRef, QuerySet, Subquery
from django.db.models.functions import Substr
from django.utils import timezone
from django.utils.translation import gettext as _

from assistant.constants import (
    HISTORY_MESSAGES,
    THREAD_HISTORY_LIMIT,
    THREAD_PAYLOAD_MESSAGES,
    THREAD_TITLE_LENGTH,
    AssistantMessageStatus,
    AssistantRole,
)
from assistant.domain.prompt import HistoryTurn
from assistant.models import (
    AssistantKnowledgeSource,
    AssistantMessage,
    AssistantProposal,
    AssistantThread,
)
from conversations.models import LLM
from files.constants import FileStatus


class AssistantLimitReached(Exception):
    """The user has used today's assistant message budget."""


class AssistantUnavailable(Exception):
    """The assistant is not configured (no active model)."""


class AssistantThreadNotFound(Exception):
    """No thread with that id belongs to the user."""


@dataclass(frozen=True)
class KnowledgeScope:
    """The ingested documentation files and the account that owns them."""

    file_ids: Tuple[int, ...]
    owner_id: int


@dataclass(frozen=True)
class Turn:
    question: AssistantMessage
    reply: AssistantMessage
    history: Tuple[HistoryTurn, ...]
    llm: LLM
    knowledge: Optional[KnowledgeScope]


def open_thread(user) -> AssistantThread:
    """The user's open thread, created on first use."""
    thread = AssistantThread.active_objects.filter(
        user=user, closed_at__isnull=True
    ).first()
    if thread is not None:
        return thread
    try:
        with transaction.atomic():
            return AssistantThread.active_objects.create(user=user)
    except IntegrityError:
        # A concurrent request created it first.
        return AssistantThread.active_objects.get(user=user, closed_at__isnull=True)


@transaction.atomic
def start_new_thread(user) -> AssistantThread:
    """Close the open thread (kept for history) and open an empty one."""
    AssistantThread.active_objects.filter(user=user, closed_at__isnull=True).update(
        closed_at=timezone.now()
    )
    return AssistantThread.active_objects.create(user=user)


def thread_history(user) -> QuerySet:
    """The user's conversations that have messages, most recently active first,
    titled by their first question."""
    messages = AssistantMessage.active_objects.filter(thread=OuterRef("pk"))
    first_question = (
        messages.filter(role=AssistantRole.USER)
        .order_by("created_at", "id")
        .values("content")[:1]
    )
    return (
        AssistantThread.active_objects.filter(user=user)
        .annotate(
            last_message_at=Max("messages__created_at"),
            title=Substr(Subquery(first_question), 1, THREAD_TITLE_LENGTH),
        )
        .filter(last_message_at__isnull=False)
        .order_by("-last_message_at")[:THREAD_HISTORY_LIMIT]
    )


@transaction.atomic
def reopen_thread(user, thread_id: int) -> AssistantThread:
    """Make one of the user's past threads the open one; new messages go there."""
    # Locking every thread of the user serialises concurrent switches, which
    # would otherwise race on the one-open-thread constraint.
    threads = {
        thread.pk: thread
        for thread in AssistantThread.active_objects.select_for_update().filter(
            user=user
        )
    }
    thread = threads.get(thread_id)
    if thread is None:
        raise AssistantThreadNotFound()
    if thread.closed_at is None:
        return thread
    now = timezone.now()
    for other in threads.values():
        if other.closed_at is None:
            other.closed_at = now
            other.save(update_fields=["closed_at", "updated_at"])
    thread.closed_at = None
    thread.save(update_fields=["closed_at", "updated_at"])
    return thread


def recent_messages(thread: AssistantThread) -> List[AssistantMessage]:
    newest = (
        AssistantMessage.active_objects.filter(thread=thread)
        .prefetch_related("proposals")
        .order_by("-created_at", "-id")[:THREAD_PAYLOAD_MESSAGES]
    )
    return list(newest)[::-1]


def messages_used_today(user) -> int:
    return AssistantMessage.active_objects.filter(
        thread__user=user,
        role=AssistantRole.USER,
        created_at__date=timezone.localdate(),
    ).count()


def usage(user) -> Dict[str, int]:
    return {
        "used_today": messages_used_today(user),
        "daily_limit": settings.ASSISTANT_DAILY_MESSAGE_LIMIT,
    }


def knowledge_scope() -> Optional[KnowledgeScope]:
    """Searchable documentation, or None when none is ingested yet."""
    rows = AssistantKnowledgeSource.active_objects.filter(
        file__is_active=True,
        file__is_deleted=False,
        file__status=FileStatus.PROCESSED,
    ).values_list("file_id", "file__user_id")
    if not rows:
        return None
    owners = {owner_id for _file_id, owner_id in rows}
    if len(owners) > 1:
        # AssistantKnowledgeSource.clean() prevents this through the admin.
        raise AssistantUnavailable(
            "Assistant knowledge files belong to more than one account."
        )
    return KnowledgeScope(
        file_ids=tuple(sorted(file_id for file_id, _owner_id in rows)),
        owner_id=owners.pop(),
    )


def assistant_llm() -> LLM:
    llm = LLM.objects.filter(
        identifier=settings.ASSISTANT_MODEL_IDENTIFIER, is_active=True
    ).first()
    if llm is None:
        raise AssistantUnavailable(
            f"Assistant model {settings.ASSISTANT_MODEL_IDENTIFIER!r} is not active."
        )
    return llm


def begin_turn(user, question: str, page_path: str) -> Turn:
    """Record the question and an empty streaming reply, within budget."""
    llm = assistant_llm()
    knowledge = knowledge_scope()
    with transaction.atomic():
        thread = open_thread(user)
        # Serialise a user's concurrent sends so the budget check holds.
        AssistantThread.objects.select_for_update().get(pk=thread.pk)
        if messages_used_today(user) >= settings.ASSISTANT_DAILY_MESSAGE_LIMIT:
            raise AssistantLimitReached(
                _("You have used today's assistant messages. Try again tomorrow.")
            )
        history = _history(thread)
        asked = AssistantMessage.active_objects.create(
            thread=thread,
            role=AssistantRole.USER,
            content=question,
            page_path=page_path,
        )
        reply = AssistantMessage.active_objects.create(
            thread=thread,
            role=AssistantRole.ASSISTANT,
            status=AssistantMessageStatus.STREAMING,
            llm=llm,
        )
    return Turn(
        question=asked, reply=reply, history=history, llm=llm, knowledge=knowledge
    )


def _history(thread: AssistantThread) -> Tuple[HistoryTurn, ...]:
    """Prior answered exchanges; failed or empty replies are left out."""
    newest = (
        AssistantMessage.active_objects.filter(thread=thread)
        .exclude(content="")
        .exclude(status=AssistantMessageStatus.FAILED)
        .order_by("-created_at", "-id")[:HISTORY_MESSAGES]
    )
    return tuple(
        HistoryTurn(role=message.role, content=message.content)
        for message in list(newest)[::-1]
    )


def finish_turn(
    reply: AssistantMessage,
    *,
    text: str,
    status: str,
    token_usage: Optional[Dict[str, Any]],
    tool_calls: List[Dict[str, Any]],
) -> AssistantMessage:
    usage_totals = token_usage or {}
    proposal_ids = [call["proposal_id"] for call in tool_calls if "proposal_id" in call]
    if proposal_ids:
        AssistantProposal.active_objects.filter(
            pk__in=proposal_ids, user_id=reply.thread.user_id
        ).update(message=reply)
    reply.content = text
    reply.status = status
    reply.tool_calls = tool_calls
    reply.input_tokens = usage_totals.get("input_tokens") or 0
    reply.output_tokens = usage_totals.get("output_tokens") or 0
    reply.save(
        update_fields=[
            "content",
            "status",
            "tool_calls",
            "input_tokens",
            "output_tokens",
            "updated_at",
        ]
    )
    return reply
