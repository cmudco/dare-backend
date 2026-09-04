"""
Queue a re-embed for processed documents so they gain map rows.

Files uploaded before the document map existed have vectors but no chunk or
reference rows, so their citations carry no page or section and the Map tab
shows headings only. This queues ``refresh_file_embeddings`` for them, which
deletes the file's existing vectors and map rows before regenerating them.
Files whose OCR finished or partly finished are rebuilt from the stored
transcriptions without re-running vision. A scanned PDF that never went
through OCR approval has no transcription to rebuild from: it still loses its
old vectors, then pauses for OCR approval like a first-time upload, so it is
briefly unsearchable until that approval completes.

    python manage.py reprocess_documents --user-id 3
    python manage.py reprocess_documents --file-id 42
    python manage.py reprocess_documents --all --limit 200
"""

from django.core.management.base import BaseCommand, CommandError

from files.constants import FileStatus
from files.models import File
from files.tasks import refresh_file_embeddings


class Command(BaseCommand):
    help = (
        "Queue refresh_file_embeddings for processed, non-media files. Files whose OCR "
        "finished or partly finished are rebuilt from the stored transcriptions without "
        "re-running vision. A scanned PDF that never went through OCR approval loses its "
        "old vectors and then pauses for approval, same as a first-time upload."
    )

    def add_arguments(self, parser):
        parser.add_argument("--user-id", type=int)
        parser.add_argument("--file-id", type=int)
        parser.add_argument("--all", action="store_true")
        parser.add_argument("--limit", type=int, default=100)

    def handle(self, *args, **options):
        user_id, file_id, everything = (
            options["user_id"],
            options["file_id"],
            options["all"],
        )
        if not (user_id or file_id or everything):
            raise CommandError("Pass --user-id, --file-id or --all.")

        queryset = File.active_objects.filter(
            status=FileStatus.PROCESSED, is_media=False
        ).select_related("user")
        if user_id:
            queryset = queryset.filter(user_id=user_id)
        if file_id:
            queryset = queryset.filter(id=file_id)

        queued = 0
        for file in queryset.order_by("id")[: options["limit"]]:
            refresh_file_embeddings.delay(
                file.id, file.user_id, file.user.chunk_size, file.user.overlap_size
            )
            queued += 1
        self.stdout.write(f"Queued {queued} file(s) for reprocessing.")
