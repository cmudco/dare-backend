"""Ownership checks for document work that may outlive its File row."""

from contextlib import contextmanager
from typing import Iterable, Iterator

from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from files.models import File


class IngestionCancelled(Exception):
    """The file was removed or this attempt no longer owns it."""


def owned_file(file: File) -> QuerySet[File]:
    return File.active_objects.filter(
        pk=file.pk, user_id=file.user_id, ingestion_token=file.ingestion_token
    )


def ensure_ingestion_owner(file: File) -> None:
    if not owned_file(file).exists():
        raise IngestionCancelled("File removed or ingestion attempt replaced")


def persist_ingestion_file(file: File, update_fields: Iterable[str]) -> None:
    """Condition the write itself on ownership; an existence precheck races."""
    values = {field: getattr(file, field) for field in update_fields}
    if "updated_at" in values:
        file.updated_at = values["updated_at"] = timezone.now()
    if not owned_file(file).update(**values):
        raise IngestionCancelled("File removed or ingestion attempt replaced")


@contextmanager
def locked_ingestion_file(file: File) -> Iterator[File]:
    """Protect a short, database-only transition against deletion/replacement."""
    with transaction.atomic():
        current = owned_file(file).select_for_update().first()
        if current is None:
            raise IngestionCancelled("File removed or ingestion attempt replaced")
        yield current
