"""
Create a release note from a JSON file; a draft unless --publish is given.

Usage:
    python manage.py create_release_note draft.json
    cat draft.json | python manage.py create_release_note - --publish

JSON shape (field names match the models):
    {
      "source": "DARE",
      "version": "2026.10.27",
      "title": "Projects, background models, and more",
      "summary": "Optional one-paragraph intro.",
      "items": [
        {"kind": "new", "title": "...", "body": "Markdown", "link_path": "/settings"}
      ]
    }
"""

import sys
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError, CommandParser

from release_notes.services.release_note_import import (
    ReleaseNoteImportError,
    import_release_note,
    parse_release_note_json,
)


class Command(BaseCommand):
    help = "Create a release note from JSON (a draft unless --publish)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("path", help="Path to the JSON file, or - for stdin.")
        parser.add_argument(
            "--publish", action="store_true", help="Publish it immediately."
        )

    def handle(self, *args, **options) -> None:
        path = options["path"]
        try:
            raw = sys.stdin.read() if path == "-" else Path(path).read_text()
        except OSError as error:
            raise CommandError(f"Could not read {path}: {error}") from error

        try:
            note = import_release_note(
                parse_release_note_json(raw), publish=options["publish"]
            )
        except ReleaseNoteImportError as error:
            raise CommandError(str(error)) from error

        state = "Published" if note.published_at else "Created draft"
        self.stdout.write(
            self.style.SUCCESS(
                f"{state} release note #{note.pk} ({note.version}, "
                f"{note.items.count()} items): "
                f"/admin/release_notes/releasenote/{note.pk}/change/"
            )
        )
