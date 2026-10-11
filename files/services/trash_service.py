"""Recently deleted files: soft-deleted rows their owner can restore or purge.

Deleting a file moves it here; ``updated_at`` records when. Purging goes
through ``File.delete`` so storage and vector cleanup run exactly as they do
for any other permanent delete.
"""

from typing import Iterable

from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from files.models import File


def deleted_files(user) -> QuerySet:
    return File._base_manager.filter(user=user, is_deleted=True).order_by(
        "-updated_at", "-id"
    )


def move_to_trash(user, file_ids: Iterable[int]) -> int:
    return File.active_objects.filter(user=user, id__in=file_ids).update(
        is_deleted=True, updated_at=timezone.now()
    )


def restore_files(user, file_ids: Iterable[int]) -> int:
    return (
        deleted_files(user)
        .filter(id__in=file_ids)
        .update(is_deleted=False, updated_at=timezone.now())
    )


@transaction.atomic
def purge_files(user, file_ids: Iterable[int]) -> int:
    files = list(deleted_files(user).filter(id__in=file_ids))
    for file in files:
        file.delete()
    return len(files)
