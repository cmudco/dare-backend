"""Library-wide operations on a user's own files: bulk tagging and content search."""

import re
from dataclasses import dataclass

from django.db import transaction
from django.db.models import Q
from django.http import Http404

from files.models import DocumentChunk, File, Tag

MAX_CONTENT_MATCHES = 50
SNIPPET_RADIUS = 80


@dataclass(frozen=True)
class ContentMatch:
    file_id: int
    snippet: str
    page: int | None


def add_tags_to_files(user, file_ids: list[int], tag_ids: list[int]) -> list[File]:
    """Add every tag to every file; tags a file already has are left alone.

    All ids must belong to the user (tags may also be global), otherwise
    nothing changes.
    """
    files = list(File.active_objects.filter(user=user, id__in=file_ids))
    tags = list(Tag.objects.filter(Q(user=user) | Q(user=None), id__in=tag_ids))
    if len(files) != len(set(file_ids)) or len(tags) != len(set(tag_ids)):
        raise Http404
    through = File.tags.through
    with transaction.atomic():
        through.objects.bulk_create(
            [through(file=file, tag=tag) for file in files for tag in tags],
            ignore_conflicts=True,
        )
    return list(
        File.active_objects.filter(pk__in=[file.pk for file in files]).prefetch_related(
            "tags"
        )
    )


def search_file_contents(user, query: str) -> list[ContentMatch]:
    """The first passage in each of the user's files that contains the query."""
    rows = (
        DocumentChunk.objects.filter(
            file__in=File.active_objects.filter(user=user), text__icontains=query
        )
        .order_by("file_id", "chunk_index")
        .values_list("file_id", "text", "page_start")
    )
    matches: dict[int, ContentMatch] = {}
    for file_id, text, page in rows.iterator():
        if file_id in matches:
            continue
        matches[file_id] = ContentMatch(file_id, _snippet(text, query), page)
        if len(matches) == MAX_CONTENT_MATCHES:
            break
    return list(matches.values())


def _snippet(text: str, query: str) -> str:
    flat = re.sub(r"\s+", " ", text).strip()
    at = flat.casefold().find(query.casefold())
    start = max(at - SNIPPET_RADIUS, 0)
    end = min(at + len(query) + SNIPPET_RADIUS, len(flat))
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(flat) else ""
    return f"{prefix}{flat[start:end]}{suffix}"
