import math
from dataclasses import dataclass

from django.db.models import Count, F, IntegerField, QuerySet, Sum
from django.db.models.functions import Coalesce, Length


@dataclass(frozen=True)
class SourceSize:
    """Rough prompt cost of reading a set of files in full."""

    file_count: int
    estimated_tokens: int

    def fits(self, token_budget: int) -> bool:
        return self.estimated_tokens <= token_budget


def estimate_source_size(files: QuerySet) -> SourceSize:
    """Estimate tokens at four characters each from the cached extraction.

    A file not parsed yet counts its byte size, which overstates the text, so
    an unparsed file can push a set into search but never sneak it past.
    """
    totals = files.aggregate(
        file_count=Count("id", distinct=True),
        chars=Sum(
            Coalesce(Length("extracted_text"), F("size"), output_field=IntegerField())
        ),
    )
    return SourceSize(
        file_count=totals["file_count"],
        estimated_tokens=math.ceil((totals["chars"] or 0) / 4),
    )
