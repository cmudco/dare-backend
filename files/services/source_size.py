import math
from dataclasses import dataclass

import tiktoken
from django.db.models import Count, F, IntegerField, QuerySet, Sum
from django.db.models.functions import Ceil, Coalesce

# One tokenizer for every provider: close enough to judge whether files fit.
_ENCODING = tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    """Count document text without interpreting tokenizer sentinel strings."""
    return len(_ENCODING.encode(text, disallowed_special=()))


@dataclass(frozen=True)
class SourceSize:
    """Prompt cost of reading a set of files in full."""

    file_count: int
    estimated_tokens: int

    def fits(self, token_budget: int) -> bool:
        return self.estimated_tokens <= token_budget


def estimate_source_size(files: QuerySet) -> SourceSize:
    """Sum the token counts stored when each file's text was extracted.

    A file not parsed yet counts its byte size at four bytes a token, which
    overstates the text, so it can push a set over the budget but never under.
    """
    totals = files.aggregate(
        file_count=Count("id", distinct=True),
        tokens=Sum(
            Coalesce(
                F("token_count"), Ceil(F("size") / 4.0), output_field=IntegerField()
            )
        ),
    )
    return SourceSize(
        file_count=totals["file_count"],
        estimated_tokens=math.ceil(totals["tokens"] or 0),
    )
