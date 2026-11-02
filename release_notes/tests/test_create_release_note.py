import json
import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import CommandError, call_command

from release_notes.models import ReleaseNote
from release_notes.tests.base import CleanReleaseNotesTestCase

VALID = {
    "version": "2026.10.27",
    "title": "Smoother What's New",
    "summary": "A friendlier way to hear about updates.",
    "items": [
        {"kind": "new", "title": "What's New dialog", "link_path": "/dashboard"},
        {"kind": "fixed", "title": "Rounder corners", "body": "Dialogs look softer."},
    ],
}


class CreateReleaseNoteCommandTests(CleanReleaseNotesTestCase):
    def run_command(self, payload, *flags):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "draft.json"
            path.write_text(json.dumps(payload))
            call_command("create_release_note", str(path), *flags, stdout=StringIO())

    def test_creates_an_unpublished_draft_with_ordered_items(self):
        self.run_command(VALID)

        note = ReleaseNote.objects.get(version="2026.10.27")
        self.assertIsNone(note.published_at)
        self.assertEqual(
            [(i.position, i.kind, i.link_path) for i in note.items.all()],
            [(1, "new", "/dashboard"), (2, "fixed", "")],
        )

    def test_invalid_item_rolls_back_the_whole_release(self):
        payload = {**VALID, "items": [{"title": "Bad link", "link_path": "//evil.com"}]}

        with self.assertRaisesMessage(CommandError, "item 1"):
            self.run_command(payload)
        self.assertFalse(ReleaseNote.objects.exists())

    def test_rejects_a_duplicate_version_on_the_same_platform(self):
        self.run_command(VALID)

        with self.assertRaises(CommandError):
            self.run_command(VALID)
        self.assertEqual(ReleaseNote.objects.count(), 1)

    def test_rejects_unknown_fields_and_empty_items(self):
        with self.assertRaisesMessage(CommandError, "Unknown release fields"):
            self.run_command({**VALID, "publish": True})
        with self.assertRaisesMessage(CommandError, "non-empty items"):
            self.run_command({**VALID, "items": []})

    def test_publish_flag_makes_it_live_immediately(self):
        self.run_command(VALID, "--publish")

        note = ReleaseNote.objects.get(version="2026.10.27")
        self.assertEqual(note.status, "live")
