"""
Release note: What's New dialog.

Release migrations publish at the moment `migrate` runs, so each environment
announces the release when its code lands there. Re-running is safe: the
release is matched by (source, version) and its items are replaced.
"""

from django.db import migrations
from django.utils import timezone

RELEASE = {
    "source": "DARE",
    "version": "2026.10.27",
    "title": "Never miss what's new",
    "summary": "DARE now tells you about new features the moment you open it.",
    "items": [
        {
            "kind": "new",
            "title": "What's new, in one place",
            "body": "After each update, a short tour shows what changed and takes you straight to it.",
            "link_path": "/dashboard",
        },
        {
            "kind": "improved",
            "title": "Release history",
            "body": "Catch up on past updates any time from **What's new** in your account menu.",
        },
    ],
}


def publish(apps, schema_editor):
    ReleaseNote = apps.get_model("release_notes", "ReleaseNote")
    ReleaseNoteItem = apps.get_model("release_notes", "ReleaseNoteItem")
    note, _ = ReleaseNote.objects.update_or_create(
        source=RELEASE["source"],
        version=RELEASE["version"],
        is_deleted=False,
        defaults={
            "title": RELEASE["title"],
            "summary": RELEASE["summary"],
            "published_at": timezone.now(),
        },
    )
    note.items.all().delete()
    ReleaseNoteItem.objects.bulk_create(
        ReleaseNoteItem(release_note=note, position=position, **item)
        for position, item in enumerate(RELEASE["items"], start=1)
    )


def unpublish(apps, schema_editor):
    ReleaseNote = apps.get_model("release_notes", "ReleaseNote")
    ReleaseNote.objects.filter(
        source=RELEASE["source"], version=RELEASE["version"]
    ).delete()


class Migration(migrations.Migration):
    dependencies = [("release_notes", "0001_initial")]

    operations = [migrations.RunPython(publish, unpublish)]
