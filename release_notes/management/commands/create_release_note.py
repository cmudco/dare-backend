"""
Create a draft release note from a JSON file, for review and publishing in admin.

The command never publishes: going live stays a human decision in Django admin.

Usage:
    python manage.py create_release_note draft.json
    cat draft.json | python manage.py create_release_note -

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

import json
import sys
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction

from release_notes.models import ReleaseNote, ReleaseNoteItem

NOTE_FIELDS = {"source", "version", "title", "summary"}
ITEM_FIELDS = {"kind", "title", "body", "link_path"}


class Command(BaseCommand):
    help = "Create a draft release note from JSON; publish it in Django admin."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("path", help="Path to the JSON file, or - for stdin.")

    def handle(self, *args, **options) -> None:
        payload = self._load(options["path"])
        note = self._create(payload)
        self.stdout.write(
            self.style.SUCCESS(
                f"Created draft release note #{note.pk} ({note.version}, "
                f"{note.items.count()} items). Review and publish at "
                f"/admin/release_notes/releasenote/{note.pk}/change/"
            )
        )

    def _load(self, path: str) -> dict:
        try:
            raw = sys.stdin.read() if path == "-" else Path(path).read_text()
            payload = json.loads(raw)
        except (OSError, json.JSONDecodeError) as error:
            raise CommandError(f"Could not read release note JSON: {error}")
        if not isinstance(payload, dict) or not payload.get("items"):
            raise CommandError(
                "The JSON must be an object with a non-empty items list."
            )
        return payload

    @transaction.atomic
    def _create(self, payload: dict) -> ReleaseNote:
        unknown = set(payload) - NOTE_FIELDS - {"items"}
        if unknown:
            raise CommandError(f"Unknown release note fields: {sorted(unknown)}")
        note = ReleaseNote(**{k: v for k, v in payload.items() if k in NOTE_FIELDS})
        self._validate(note, "release note")
        note.save()

        for position, item_data in enumerate(payload["items"], start=1):
            unknown = set(item_data) - ITEM_FIELDS
            if unknown:
                raise CommandError(f"Item {position}: unknown fields {sorted(unknown)}")
            item = ReleaseNoteItem(release_note=note, position=position, **item_data)
            self._validate(item, f"item {position}")
            item.save()
        return note

    def _validate(self, instance, label: str) -> None:
        try:
            instance.full_clean()
        except ValidationError as error:
            raise CommandError(f"Invalid {label}: {error.message_dict}")
