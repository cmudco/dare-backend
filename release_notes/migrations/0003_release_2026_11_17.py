"""
Release notes: Oct 27 - Nov 17, 2026 (knowledge graphs, GPT Image, new models).

Release migrations publish at the moment `migrate` runs, so each environment
announces them when its code lands there. RELEASES is in dialog order: the
first entry is shown first. Re-running is safe: each release is matched by
(source, version) and its items are replaced.
"""

from datetime import timedelta

from django.db import migrations
from django.utils import timezone

SOURCE = "DARE"

RELEASES = [
    {
        "version": "2026.11.17-graphs",
        "title": "See how your documents connect",
        "summary": "Knowledge graphs map the people, places and links across your files.",
        "items": [
            {
                "kind": "new",
                "title": "Knowledge graphs",
                "body": (
                    "Build a graph from a set of files or folders. DARE finds the "
                    "people, places and organizations in them and how they relate, "
                    "with the quotes that back each link."
                ),
                "link_path": "/knowledge-graphs",
            },
            {
                "kind": "new",
                "title": "Graph mode in chat",
                "body": (
                    "Pick a graph from the **Graphs** tab in the chat source picker. "
                    "Every question then follows the connections in the graph, not "
                    "just matching passages."
                ),
                "link_path": "/conversation",
            },
            {
                "kind": "new",
                "title": "Edit a graph",
                "body": (
                    "Rename a graph or add and remove documents. Only new documents "
                    "are read, and you see the cost before you approve it."
                ),
            },
            {
                "kind": "new",
                "title": "See how an answer was found",
                "body": (
                    "A reply's activity panel shows what search found, what the graph "
                    "added, and a small picture of the route between them."
                ),
            },
        ],
    },
    {
        "version": "2026.11.17-images",
        "title": "Image generation, now with editing",
        "summary": "New image models, and you can change images you attach.",
        "items": [
            {
                "kind": "new",
                "title": "Edit your images",
                "body": (
                    "Turn on **Image Generation**, attach one or more images, and "
                    "describe the change. Combine photos, restyle them, or fix details."
                ),
                "link_path": "/conversation",
            },
            {
                "kind": "new",
                "title": "GPT Image models",
                "body": (
                    "Images are now made with GPT Image 2.6 Sunburst and Flare, which "
                    "replace the retired DALL-E models."
                ),
            },
            {
                "kind": "improved",
                "title": "Clearer image settings",
                "body": (
                    "Choose square, portrait or landscape and Low, Medium or High "
                    "quality. The exact cost shows under each image."
                ),
            },
            {
                "kind": "fixed",
                "title": "Follow-ups remember images",
                "body": "Follow-up questions now include images you attached earlier in the chat.",
            },
        ],
    },
    {
        "version": "2026.11.17-chat",
        "title": "New models and smoother chat",
        "summary": "Four new models and fewer reasons to wait.",
        "items": [
            {
                "kind": "new",
                "title": "Four new models",
                "body": (
                    "Claude Fable 5.2, GPT-6.1 Terra, Gemini 4 Carbon and Gemini 4 "
                    "Hydrogen are in the model picker, with their costs shown."
                ),
                "link_path": "/conversation",
            },
            {
                "kind": "new",
                "title": "Queue your next message",
                "body": (
                    "Keep typing while a reply runs. Messages wait above the box and "
                    "send in order, and you can send one now, edit it or remove it."
                ),
            },
            {
                "kind": "improved",
                "title": "Search survey answers",
                "body": (
                    "Free-text answers in survey spreadsheets can now be searched by "
                    "meaning, such as what people say about flooding."
                ),
            },
            {
                "kind": "fixed",
                "title": "Smaller fixes",
                "body": (
                    "Chart links in replies download the chart, the model picker no "
                    "longer empties after image or audio mode, and sign-in accepts "
                    "your email in any capitalization."
                ),
            },
        ],
    },
]


def publish(apps, schema_editor):
    ReleaseNote = apps.get_model("release_notes", "ReleaseNote")
    ReleaseNoteItem = apps.get_model("release_notes", "ReleaseNoteItem")
    now = timezone.now()
    # Newest is shown first, so earlier entries get the later timestamps.
    for index, release in enumerate(RELEASES):
        note, _ = ReleaseNote.objects.update_or_create(
            source=SOURCE,
            version=release["version"],
            is_deleted=False,
            defaults={
                "title": release["title"],
                "summary": release.get("summary", ""),
                "published_at": now - timedelta(seconds=index),
            },
        )
        note.items.all().delete()
        ReleaseNoteItem.objects.bulk_create(
            ReleaseNoteItem(release_note=note, position=position, **item)
            for position, item in enumerate(release["items"], start=1)
        )


def unpublish(apps, schema_editor):
    ReleaseNote = apps.get_model("release_notes", "ReleaseNote")
    ReleaseNote.objects.filter(
        source=SOURCE, version__in=[release["version"] for release in RELEASES]
    ).delete()


class Migration(migrations.Migration):
    dependencies = [("release_notes", "0002_release_2026_10_27")]

    operations = [migrations.RunPython(publish, unpublish)]
