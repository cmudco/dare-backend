"""
Launch release notes: the week of Oct 20-26, 2026, plus What's New itself.

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
        "version": "2026.10.27-data",
        "title": "Real analysis on your own data",
        "summary": "Ask questions about your spreadsheets and get exact answers.",
        "items": [
            {
                "kind": "new",
                "title": "Code sandbox in chat",
                "body": (
                    "Turn on **Code sandbox** in chat settings, attach a CSV or Excel "
                    "file, and ask for numbers or a chart. The AI runs Python on your "
                    "whole file, on OpenAI and Claude models."
                ),
                "link_path": "/conversation",
            },
            {
                "kind": "new",
                "title": "Spreadsheet preview",
                "body": "Excel and CSV files in Sources now open as a table, with a tab for each sheet.",
                "link_path": "/files",
            },
            {
                "kind": "improved",
                "title": "Large spreadsheets finish processing",
                "body": "Big CSV and Excel uploads now reach ready instead of stalling.",
            },
            {
                "kind": "fixed",
                "title": "Safer file previews",
                "body": "HTML and SVG files open as plain text, so they never run in your browser.",
            },
        ],
    },
    {
        "version": "2026.10.27-chat",
        "title": "Chat that keeps up with you",
        "summary": "Fewer interruptions and clearer answers.",
        "items": [
            {
                "kind": "improved",
                "title": "Replies keep running when you switch",
                "body": (
                    "Move to another chat mid-reply and come back: it's still going, "
                    "and **Stop** still works."
                ),
            },
            {
                "kind": "improved",
                "title": "Follow-ups remember web searches",
                "body": "Follow-up questions build on what the AI already found online.",
            },
            {
                "kind": "improved",
                "title": "Long documents finish",
                "body": "Long Word documents written by Claude no longer time out.",
            },
            {
                "kind": "improved",
                "title": "Faster chat loading",
                "body": "Chats and model cards open noticeably quicker.",
            },
            {
                "kind": "fixed",
                "title": "Clearer endings",
                "body": (
                    "A reply that runs out of room now says so instead of coming back "
                    "empty, and chats with charts open scrolled to the bottom."
                ),
            },
        ],
    },
    {
        "version": "2026.10.27-settings",
        "title": "More control over models and spending",
        "summary": "See which model works behind the scenes and what it costs you.",
        "items": [
            {
                "kind": "new",
                "title": "Background models settings",
                "body": (
                    "See which model writes your chat titles, summaries, memory, and "
                    "search queries, and who pays for it. Pick your own if you prefer."
                ),
                "link_path": "/settings/background-models",
            },
            {
                "kind": "new",
                "title": "Gemini 4 Argon",
                "body": "Google's newest model is in the model picker, with its cost shown.",
            },
            {
                "kind": "improved",
                "title": "Live wallet balance",
                "body": "Your balance updates after every reply, and transactions now show the time.",
            },
            {
                "kind": "fixed",
                "title": "Darker dark mode",
                "body": "Scrollbars and dropdowns now stay dark in dark mode.",
            },
        ],
    },
    {
        "version": "2026.10.27-whats-new",
        "title": "Never miss what's new",
        "summary": "From now on, DARE tells you about updates the moment you open it.",
        "items": [
            {
                "kind": "new",
                "title": "What's new, in one place",
                "body": "After each update, a short tour like this one shows what changed and takes you to it.",
            },
            {
                "kind": "new",
                "title": "Release history",
                "body": "Catch up on past updates any time from **What's new** in your account menu.",
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
    dependencies = [("release_notes", "0001_initial")]

    operations = [migrations.RunPython(publish, unpublish)]
