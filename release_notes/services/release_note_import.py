import json

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from release_notes.models import ReleaseNote, ReleaseNoteItem

NOTE_FIELDS = {"source", "version", "title", "summary"}
ITEM_FIELDS = {"kind", "title", "body", "link_path"}


class ReleaseNoteImportError(Exception):
    """The pasted or loaded release note JSON is not a valid release."""


def parse_release_note_json(raw: str) -> dict:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ReleaseNoteImportError(f"Not valid JSON: {error}") from error
    if not isinstance(payload, dict) or not payload.get("items"):
        raise ReleaseNoteImportError(
            "The JSON must be an object with a non-empty items list."
        )
    return payload


@transaction.atomic
def import_release_note(payload: dict, *, publish: bool = False) -> ReleaseNote:
    """Create a release and its items in one transaction; a draft unless publish."""
    unknown = set(payload) - NOTE_FIELDS - {"items"}
    if unknown:
        raise ReleaseNoteImportError(f"Unknown release fields: {sorted(unknown)}")

    note = ReleaseNote(
        **{key: value for key, value in payload.items() if key in NOTE_FIELDS},
        published_at=timezone.now() if publish else None,
    )
    _validate(note, "release")
    note.save()

    for position, item_data in enumerate(payload["items"], start=1):
        unknown = set(item_data) - ITEM_FIELDS
        if unknown:
            raise ReleaseNoteImportError(
                f"Item {position}: unknown fields {sorted(unknown)}"
            )
        item = ReleaseNoteItem(release_note=note, position=position, **item_data)
        _validate(item, f"item {position}")
        item.save()
    return note


def _validate(instance, label: str) -> None:
    try:
        instance.full_clean()
    except ValidationError as error:
        problems = "; ".join(
            f"{field}: {' '.join(messages)}"
            for field, messages in error.message_dict.items()
        )
        raise ReleaseNoteImportError(f"Invalid {label} — {problems}") from error
