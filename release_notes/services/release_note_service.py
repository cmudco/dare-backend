from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from release_notes.constants import UNSEEN_RELEASE_NOTES_LIMIT
from release_notes.models import ReleaseNote, ReleaseNoteSeenState


def live_release_notes(source: str) -> QuerySet[ReleaseNote]:
    return ReleaseNote.active_objects.filter(
        source=source, published_at__lte=timezone.now()
    ).prefetch_related("items")


def unseen_release_notes(user, source: str) -> list[ReleaseNote]:
    # A user who joined after a release never sees it announced; only newer ones.
    state = ReleaseNoteSeenState.objects.filter(user=user, source=source).first()
    seen_up_to = state.last_seen_published_at if state else user.date_joined
    watermark = max(seen_up_to, user.date_joined)
    return list(
        live_release_notes(source).filter(published_at__gt=watermark)[
            :UNSEEN_RELEASE_NOTES_LIMIT
        ]
    )


@transaction.atomic
def mark_seen(user, release_note: ReleaseNote) -> None:
    """Advance the watermark to this release; never moves it backwards."""
    state, created = ReleaseNoteSeenState.objects.get_or_create(
        user=user,
        source=release_note.source,
        defaults={"last_seen_published_at": release_note.published_at},
    )
    if not created:
        ReleaseNoteSeenState.objects.filter(
            pk=state.pk, last_seen_published_at__lt=release_note.published_at
        ).update(last_seen_published_at=release_note.published_at)
