"""
Backfill document models for files uploaded before the parsing pipeline.

Those files have no ``extracted_text`` and no ``document_model``, so a full-file
reference falls back to flat text and the structure view has nothing to show.
This re-parses them with Docling and, for anything that turns out to be a
scan, moves the status to NEEDS_OCR so the gap is visible instead of silent.

Run it in a worker, not a web process: Docling loads a layout model.

    python manage.py backfill_document_models --limit 50
    python manage.py backfill_document_models --file-id 42 --force
"""

import logging

from django.core.management.base import BaseCommand
from django.db.models import Q

from core.services.document_parsing_service import DocumentParsingService
from files.constants import FileStatus
from files.models import File

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Parse existing files into extracted_text and document_model"

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Maximum number of files to process",
        )
        parser.add_argument(
            "--file-id",
            type=int,
            default=None,
            help="Process a single file by id",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Re-parse files that already have a document model",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be parsed without writing anything",
        )

    def handle(self, *args, **options):
        files = self._select_files(options)
        total = files.count()
        self.stdout.write(f"{total} file(s) to parse")

        if options["dry_run"]:
            for file in files:
                self.stdout.write(f"  would parse #{file.id} {file.name or file.file}")
            return

        service = DocumentParsingService()
        parsed_count = 0
        needs_ocr_count = 0
        failed_count = 0

        for file in files.iterator():
            try:
                parsed = service.parse_and_persist(file)
            except Exception as error:
                failed_count += 1
                self.stderr.write(f"  #{file.id} failed: {error}")
                continue

            parsed_count += 1
            if parsed.needs_ocr:
                needs_ocr_count += 1
                file.status = FileStatus.NEEDS_OCR
                file.error_message = (
                    f"All {parsed.structure.pages} pages are scanned images with "
                    f"no readable text."
                )
                file.save(update_fields=["status", "error_message", "updated_at"])

            self.stdout.write(
                f"  #{file.id} {file.name or file.file}: "
                f"{parsed.structure.content_chars} chars, "
                f"{parsed.structure.pages} pages, "
                f"{parsed.structure.tables} tables, "
                f"{parsed.structure.pictures} pictures"
                f"{' [needs OCR]' if parsed.needs_ocr else ''}"
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Parsed {parsed_count}, flagged {needs_ocr_count} as needing OCR, "
                f"{failed_count} failed"
            )
        )

    @staticmethod
    def _select_files(options):
        files = File.active_objects.filter(is_media=False).order_by("id")

        if options["file_id"]:
            return files.filter(id=options["file_id"])

        if not options["force"]:
            files = files.filter(Q(document_model__isnull=True))

        if options["limit"]:
            files = files[: options["limit"]]

        return files
