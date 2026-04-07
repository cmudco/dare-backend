"""
Backfill rolling summaries for existing conversations.

Enqueues a summary-refresh job for every conversation that already has
5 or more completed AI assistant messages but no up-to-date summary yet.
Run this once after the ConversationSummary model is deployed so that
older conversations appear in the Conversation Summaries tab.

Usage:
    python manage.py backfill_conversation_summaries
    python manage.py backfill_conversation_summaries --dry-run
    python manage.py backfill_conversation_summaries --batch-size 200
"""

import logging

from django.core.management.base import BaseCommand, CommandParser
from django.db.models import Count, Q

from conversations.constants import SenderType
from conversations.models import Conversation, ConversationSummary
from conversations.tasks import (
    MESSAGES_PER_SUMMARY,
    refresh_conversation_summary_for_conversation,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Enqueue rolling-summary jobs for conversations missing a current summary."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="Report how many conversations would be enqueued without actually enqueuing.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=100,
            help="Number of conversation PKs to fetch per DB query (default: 100).",
        )

    def handle(self, *args, **options) -> None:
        dry_run: bool = options["dry_run"]
        batch_size: int = options["batch_size"]

        candidate_pks = self._find_candidate_pks()
        total = len(candidate_pks)

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"[dry-run] Would enqueue {total} conversation(s) for summary backfill."
                )
            )
            return

        enqueued = 0
        for i in range(0, total, batch_size):
            batch = candidate_pks[i : i + batch_size]
            for pk in batch:
                try:
                    refresh_conversation_summary_for_conversation.delay(pk)
                    enqueued += 1
                except Exception:
                    logger.exception(
                        "Failed to enqueue summary job for conversation pk=%s", pk
                    )

            self.stdout.write(f"Enqueued {min(i + batch_size, total)}/{total} jobs...")

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. {enqueued}/{total} conversation summary job(s) enqueued."
            )
        )

    def _find_candidate_pks(self) -> list[int]:
        """
        Return PKs of conversations that:
        - belong to a user (not anonymous)
        - have >= MESSAGES_PER_SUMMARY completed AI assistant messages
        - either have no summary at all, or have a summary that is behind the
          current message count (i.e. needs a refresh)
        """
        conversations_with_enough_messages = (
            Conversation.active_objects.filter(user__isnull=False)
            .annotate(
                ai_message_count=Count(
                    "messages",
                    filter=Q(messages__sender_type=SenderType.AI_ASSISTANT, messages__is_active=True, messages__is_deleted=False),
                )
            )
            .filter(ai_message_count__gte=MESSAGES_PER_SUMMARY)
            .values_list("pk", "ai_message_count")
        )

        existing_summaries: dict[int, int] = {
            row["conversation_id"]: row["summarized_message_count"]
            for row in ConversationSummary.active_objects.values(
                "conversation_id", "summarized_message_count"
            )
        }

        candidate_pks: list[int] = []
        for pk, ai_count in conversations_with_enough_messages:
            floored = ai_count - (ai_count % MESSAGES_PER_SUMMARY)
            already_summarized = existing_summaries.get(pk, 0)
            if already_summarized < floored:
                candidate_pks.append(pk)

        return candidate_pks
