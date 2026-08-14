"""Poke the memory system from the shell.

Two modes:

    # Run the ranking by hand without spending a turn — winners and near-misses
    python manage.py memory_probe --user someone@example.com --recall "where do I live"

    # Ingest one synthetic turn synchronously (real writer LLM call)
    python manage.py memory_probe --user someone@example.com \
        --ingest "I'm vegetarian and I hate long answers"

The ingest mode builds a throwaway conversation + message pair so the ledger
has real sources to point at; it exercises the exact ``ingest_turn`` the RQ
job calls.
"""

import uuid

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from conversations.constants import SenderType
from conversations.models import Conversation, Message
from memory.services.ingest import ingest_turn
from memory.services.retrieval import retrieve, summarize_recall


class Command(BaseCommand):
    help = "Probe memory recall, or ingest one synthetic turn synchronously."

    def add_arguments(self, parser):
        parser.add_argument("--user", required=True, help="User email")
        parser.add_argument("--recall", help="Query to run through the retriever")
        parser.add_argument("--ingest", help="A user message to ingest as one turn")
        parser.add_argument(
            "--assistant",
            default="Understood.",
            help="The assistant reply for --ingest (default: 'Understood.')",
        )

    def handle(self, *args, **options):
        user = get_user_model().objects.filter(email=options["user"]).first()
        if user is None:
            raise CommandError(f"No user with email {options['user']}")

        if options.get("recall"):
            self._recall(user, options["recall"])
        elif options.get("ingest"):
            self._ingest(user, options["ingest"], options["assistant"])
        else:
            raise CommandError("Pass --recall or --ingest")

    def _recall(self, user, query):
        summary = summarize_recall(retrieve(user, query), considered=12)
        for line in summary["trace"]:
            self.stdout.write(self.style.NOTICE(line))
        for item in summary["items"]:
            marker = "✓" if item["chosen"] else " "
            self.stdout.write(
                f"{marker} {item['score']:.3f}  [{item['key']}] {item['text']}"
                f"  parts={item['parts']} via={item['via']}"
            )
        self.stdout.write(
            f"{summary['shortlisted']} shortlisted, {summary['ms']}ms, "
            f"embedding={'yes' if summary['used_embedding'] else 'no'}"
        )

    def _ingest(self, user, text, assistant_text):
        conversation = Conversation.active_objects.create(
            user=user,
            conversation_id=f"memory-probe-{uuid.uuid4().hex[:12]}",
            title="memory probe",
        )
        user_message = Message.active_objects.create(
            conversation=conversation,
            sender_type=SenderType.PLAYER,
            sender="probe",
            message=text,
        )
        ai_message = Message.active_objects.create(
            conversation=conversation,
            sender_type=SenderType.AI_ASSISTANT,
            sender="assistant",
            message=assistant_text,
        )

        report = ingest_turn(user, conversation, user_message, ai_message)

        if report.skipped:
            self.stdout.write(self.style.WARNING(f"Skipped: {report.skipped}"))
            return

        self.stdout.write(
            self.style.SUCCESS(
                f"{report.decisions} decisions → {len(report.created)} created, "
                f"{report.retired} retired, {report.reinforced} reinforced, "
                f"profile_changed={report.profile_changed}"
            )
        )
        for entry in report.entries:
            flag = "applied" if entry.applied else "REFUSED"
            self.stdout.write(f"  [{entry.action} ← {entry.proposed_action}] {flag}")
            self.stdout.write(f"      reason: {entry.reason}")
            if entry.note:
                self.stdout.write(f"      note:   {entry.note}")
            if entry.detail:
                self.stdout.write(f"      detail: {entry.detail}")
