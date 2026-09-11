"""
Mark files whose ingestion job died as interrupted instead of leaving them
in Processing forever. Safe to run at any time; a file with a live job is
left alone.

    python manage.py reconcile_ingestion
    python manage.py reconcile_ingestion --file-id 42
"""

from django.core.management.base import BaseCommand

from core.services.ingestion_reconciliation import reconcile_interrupted_ingestions


class Command(BaseCommand):
    help = "Mark Processing files whose background job is no longer running as interrupted."

    def add_arguments(self, parser):
        parser.add_argument("--file-id", type=int, action="append", dest="file_ids")

    def handle(self, *args, **options):
        summary = reconcile_interrupted_ingestions(file_ids=options.get("file_ids"))
        self.stdout.write(
            f"checked={summary.checked} interrupted={summary.interrupted} "
            f"retained_previous_index={summary.retained} "
            f"abandoned_attempts={summary.abandoned_attempts}"
        )
